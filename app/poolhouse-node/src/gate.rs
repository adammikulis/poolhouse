//! Who may hold a connection to a listener of the node, and until when. A listener answers anyone on the network, so a host that opens
//! connections and says nothing must not be able to use them all: one address holds a few, the last few connections are kept for
//! addresses that a device the node knows has recently answered from (`Gate`), and a connection has a deadline (`Timed`).

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{IpAddr, TcpStream};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

/// How long an address that a known device answered from keeps its claim on the reserved connections.
const KNOWN_FOR: Duration = Duration::from_secs(3600);
const MOST_KNOWN: usize = 64;

struct Counts {
    live: usize,
    by_address: BTreeMap<IpAddr, usize>,
    known: BTreeMap<IpAddr, Instant>,
}

/// The connections of one listener: at most ``most`` together, ``per_address`` for one address, and the last ``reserved`` only for
/// addresses a known device answered from.
pub struct Gate {
    most: usize,
    per_address: usize,
    reserved: usize,
    counts: Mutex<Counts>,
}

/// One connection's place. Dropping it gives the place back.
pub struct Permit {
    gate: Arc<Gate>,
    address: IpAddr,
}

/// The address a connection is counted under: a v4-mapped address as v4, and a v6 address as its /64, since one host owns all of those.
fn counted(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => IpAddr::V4(v4),
            None => {
                let s = v6.segments();
                IpAddr::V6(std::net::Ipv6Addr::new(s[0], s[1], s[2], s[3], 0, 0, 0, 0))
            }
        },
        v4 => v4,
    }
}

impl Gate {
    pub fn new(most: usize, per_address: usize, reserved: usize) -> Arc<Gate> {
        Arc::new(Gate { most, per_address, reserved, counts: Mutex::new(Counts { live: 0, by_address: BTreeMap::new(), known: BTreeMap::new() }) })
    }

    /// A place for a connection from ``ip``, or none.
    pub fn enter(self: &Arc<Gate>, ip: IpAddr) -> Option<Permit> {
        let address = counted(ip);
        let mut c = self.counts.lock().unwrap_or_else(PoisonError::into_inner);
        let held = c.by_address.get(&address).copied().unwrap_or(0);
        let recent = c.known.get(&address).is_some_and(|t| t.elapsed() < KNOWN_FOR);
        if held >= self.per_address || c.live >= self.most || (c.live + self.reserved >= self.most && !recent) {
            return None;
        }
        c.live += 1;
        *c.by_address.entry(address).or_default() += 1;
        Some(Permit { gate: self.clone(), address })
    }
}

impl Permit {
    /// A known device answered from this address: its next connections may use the reserved places.
    pub fn vouch(&self) {
        let mut c = self.gate.counts.lock().unwrap_or_else(PoisonError::into_inner);
        if c.known.len() >= MOST_KNOWN && !c.known.contains_key(&self.address) {
            c.known.retain(|_, t| t.elapsed() < KNOWN_FOR);
        }
        if c.known.len() >= MOST_KNOWN && !c.known.contains_key(&self.address) {
            let oldest = c.known.iter().min_by_key(|(_, t)| **t).map(|(a, _)| *a);
            if let Some(oldest) = oldest {
                c.known.remove(&oldest);
            }
        }
        c.known.insert(self.address, Instant::now());
    }
}

impl Drop for Permit {
    fn drop(&mut self) {
        let mut c = self.gate.counts.lock().unwrap_or_else(PoisonError::into_inner);
        c.live = c.live.saturating_sub(1);
        if let Some(n) = c.by_address.get_mut(&self.address) {
            *n -= 1;
            if *n == 0 {
                c.by_address.remove(&self.address);
            }
        }
    }
}

/// A connection with a deadline on the whole of a stage, not on each read: a host that sends a byte every few seconds is cut off
/// all the same. With no deadline, the socket's own timeouts hold.
pub struct Timed {
    tcp: TcpStream,
    until: Option<Instant>,
}

impl Timed {
    /// ``tcp`` with no deadline.
    pub fn open(tcp: TcpStream) -> Timed {
        Timed { tcp, until: None }
    }

    /// ``tcp``, to be done with within ``span``.
    pub fn within(tcp: TcpStream, span: Duration) -> Timed {
        Timed { tcp, until: Some(Instant::now() + span) }
    }

    /// The reads and writes from now on are done within ``span`` together.
    pub fn arm(&mut self, span: Duration) {
        self.until = Some(Instant::now() + span);
    }

    /// End the deadline: each read and each write from now on may take ``each``.
    pub fn disarm(&mut self, each: Duration) -> std::io::Result<()> {
        self.until = None;
        self.tcp.set_read_timeout(Some(each))?;
        self.tcp.set_write_timeout(Some(each))
    }

    pub fn into_inner(self) -> TcpStream {
        self.tcp
    }

    fn left(&self) -> std::io::Result<Option<Duration>> {
        let Some(until) = self.until else { return Ok(None) };
        let left = until.saturating_duration_since(Instant::now());
        if left.is_zero() { Err(std::io::ErrorKind::TimedOut.into()) } else { Ok(Some(left)) }
    }
}

impl Read for Timed {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        if let Some(left) = self.left()? {
            self.tcp.set_read_timeout(Some(left))?;
        }
        self.tcp.read(buf)
    }
}

impl Write for Timed {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if let Some(left) = self.left()? {
            self.tcp.set_write_timeout(Some(left))?;
        }
        self.tcp.write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.tcp.flush()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    fn connected() -> (TcpStream, TcpStream) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        (client, listener.accept().unwrap().0)
    }

    #[test]
    fn a_peer_that_says_nothing_is_cut_off_at_the_deadline() {
        let (_client, server) = connected();
        let mut timed = Timed::within(server, Duration::from_millis(300));
        let began = Instant::now();
        assert!(timed.read(&mut [0u8; 16]).is_err());
        assert!(began.elapsed() < Duration::from_secs(2), "{:?}", began.elapsed());
    }

    #[test]
    fn a_peer_that_sends_a_byte_at_a_time_is_cut_off_at_the_same_deadline() {
        let (client, server) = connected();
        let sender = std::thread::spawn(move || {
            let mut client = client;
            for _ in 0..40 {
                if client.write_all(b"x").is_err() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        });
        let mut timed = Timed::within(server, Duration::from_millis(600));
        let (began, mut got) = (Instant::now(), 0);
        while let Ok(n) = timed.read(&mut [0u8; 16]) {
            got += n;
            assert!(n > 0, "the peer did not close");
        }
        assert!(began.elapsed() < Duration::from_millis(1500) && (3..15).contains(&got), "{:?} {got}", began.elapsed());
        drop(timed);
        sender.join().unwrap();
    }

    fn at(last: u8) -> IpAddr {
        IpAddr::V4(std::net::Ipv4Addr::new(192, 168, 1, last))
    }

    #[test]
    fn one_address_holds_a_few_connections_and_gives_them_back() {
        let gate = Gate::new(10, 3, 2);
        let held: Vec<_> = (0..3).map(|_| gate.enter(at(7)).expect("within its share")).collect();
        assert!(gate.enter(at(7)).is_none(), "a fourth from one address");
        assert!(gate.enter(at(8)).is_some(), "another address is not held up by it");
        drop(held);
        assert!(gate.enter(at(7)).is_some(), "the places came back");
    }

    #[test]
    fn the_last_places_are_kept_for_addresses_a_phone_answered_from() {
        let gate = Gate::new(6, 6, 2);
        let phone = gate.enter(at(1)).unwrap();
        phone.vouch();
        let strangers: Vec<_> = (10..13).map(|i| gate.enter(at(i)).expect("the shared places")).collect();
        assert_eq!(strangers.len(), 3);
        assert!(gate.enter(at(20)).is_none(), "the rest are reserved");
        let known = gate.enter(at(1)).expect("a known address uses a reserved place");
        assert!(gate.enter(at(21)).is_none(), "one reserved place is left, and a stranger has no claim on it");
        let last = gate.enter(at(1));
        assert!(last.is_some(), "but the phone does");
        assert!(gate.enter(at(1)).is_none(), "and nothing is over the total of six");
        drop((known, last));
    }

    #[test]
    fn a_v6_host_is_counted_as_its_whole_prefix() {
        let gate = Gate::new(10, 1, 0);
        let a: IpAddr = "2001:db8:1:2::1".parse().unwrap();
        let b: IpAddr = "2001:db8:1:2:ffff::9".parse().unwrap();
        let _held = gate.enter(a).unwrap();
        assert!(gate.enter(b).is_none(), "another address in the same /64 is the same host");
        assert!(gate.enter("2001:db8:1:3::1".parse().unwrap()).is_some());
    }
}
