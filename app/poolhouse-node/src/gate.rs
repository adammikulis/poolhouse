//! Who may hold a connection to a listener of the node, and until when. A listener answers anyone on the network, so a host that opens
//! connections and says nothing must not be able to use them all: one address holds a few, the last few connections are kept for
//! addresses that a device the node knows has recently answered from (`Gate`), and a connection has a deadline (`Timed`). Such a
//! device may share its address with anyone (a router, a VPN, another program of its computer), so a connection from that address
//! that finds no place takes the place of the oldest one that has not kept its own (`Permit::keep`). A known device's connection has
//! not until its handshake is over and its certificate checked, and may be hung up before that like any other.

use std::cell::Cell;
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::net::{IpAddr, TcpStream};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

/// How long an address that a known device answered from keeps its claim on the reserved connections.
const KNOWN_FOR: Duration = Duration::from_secs(3600);
const MOST_KNOWN: usize = 64;
/// The connections hung up to make room that have yet to end: past that nothing more is hung up, so the connections of a listener
/// never pass its total by more than this.
const MOST_LEAVING: usize = 16;

/// An address known devices are at: when one last showed it, and which they are.
struct Known {
    at: Instant,
    devices: BTreeSet<String>,
}

struct Counts {
    live: usize,
    by_address: BTreeMap<IpAddr, usize>,
    known: BTreeMap<IpAddr, Known>,
    /// The connections that may be hung up to make room, by their number: the first is the oldest.
    yielding: BTreeMap<u64, (IpAddr, Arc<TcpStream>)>,
    /// The numbers of the connections hung up to make room that have not given their place back.
    leaving: BTreeSet<u64>,
    entered: u64,
}

impl Counts {
    fn give_back(&mut self, address: IpAddr) {
        self.live = self.live.saturating_sub(1);
        if let Some(n) = self.by_address.get_mut(&address) {
            *n -= 1;
            if *n == 0 {
                self.by_address.remove(&address);
            }
        }
    }

    /// Take the place of the oldest connection that yields, of the address ``of`` or else of the address with the most that yield;
    /// the connection to hang up, or none when no place can be made.
    fn make_room(&mut self, of: Option<IpAddr>) -> Option<Arc<TcpStream>> {
        if self.leaving.len() >= MOST_LEAVING {
            return None;
        }
        let from = match of {
            Some(address) => address,
            None => {
                // Connections that kept their places give nobody up, so they do not count against their address.
                let mut yield_at = BTreeMap::<IpAddr, usize>::new();
                self.yielding.values().for_each(|(a, _)| *yield_at.entry(*a).or_default() += 1);
                yield_at.into_iter().max_by_key(|(_, n)| *n)?.0
            }
        };
        let oldest = *self.yielding.iter().find(|(_, (a, _))| *a == from)?.0;
        let (_, tcp) = self.yielding.remove(&oldest)?;
        self.give_back(from);
        self.leaving.insert(oldest);
        Some(tcp)
    }
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
    number: u64,
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
        let counts = Counts { live: 0, by_address: BTreeMap::new(), known: BTreeMap::new(), yielding: BTreeMap::new(), leaving: BTreeSet::new(), entered: 0 };
        Arc::new(Gate { most, per_address, reserved, counts: Mutex::new(counts) })
    }

    /// A place for a connection from ``ip``, or none.
    pub fn enter(self: &Arc<Gate>, ip: IpAddr) -> Option<Permit> {
        self.place(ip, None)
    }

    /// Like `enter`, for a connection over ``tcp`` that has yet to show who it is: until `Permit::keep`, it may be hung up to make
    /// room for a connection from an address a known device answered from.
    pub fn enter_yielding(self: &Arc<Gate>, ip: IpAddr, tcp: &Arc<TcpStream>) -> Option<Permit> {
        self.place(ip, Some(tcp.clone()))
    }

    /// The known device ``device`` is at ``ip``: for `KNOWN_FOR` the connections from there may use the reserved places and make room
    /// among those that yield.
    pub fn vouch(&self, ip: IpAddr, device: &str) {
        let address = counted(ip);
        let mut c = self.counts.lock().unwrap_or_else(PoisonError::into_inner);
        if c.known.len() >= MOST_KNOWN && !c.known.contains_key(&address) {
            c.known.retain(|_, k| k.at.elapsed() < KNOWN_FOR);
        }
        if c.known.len() >= MOST_KNOWN && !c.known.contains_key(&address) {
            let oldest = c.known.iter().min_by_key(|(_, k)| k.at).map(|(a, _)| *a);
            if let Some(oldest) = oldest {
                c.known.remove(&oldest);
            }
        }
        let known = c.known.entry(address).or_insert_with(|| Known { at: Instant::now(), devices: BTreeSet::new() });
        known.at = Instant::now();
        known.devices.insert(device.to_string());
    }

    /// Forget each device ``still`` says no to, and each address only such devices were at.
    pub fn forget_unless(&self, still: impl Fn(&str) -> bool) {
        let mut c = self.counts.lock().unwrap_or_else(PoisonError::into_inner);
        c.known.retain(|_, k| {
            k.devices.retain(|d| still(d));
            !k.devices.is_empty()
        });
    }

    fn place(self: &Arc<Gate>, ip: IpAddr, yields: Option<Arc<TcpStream>>) -> Option<Permit> {
        let address = counted(ip);
        let mut c = self.counts.lock().unwrap_or_else(PoisonError::into_inner);
        let recent = c.known.get(&address).is_some_and(|k| k.at.elapsed() < KNOWN_FOR);
        let own_full = c.by_address.get(&address).copied().unwrap_or(0) >= self.per_address;
        let all_full = c.live >= self.most || (c.live + self.reserved >= self.most && !recent);
        let mut ended = None;
        if own_full || all_full {
            if !recent {
                return None;
            }
            ended = Some(c.make_room(own_full.then_some(address))?);
        }
        c.entered += 1;
        let number = c.entered;
        c.live += 1;
        *c.by_address.entry(address).or_default() += 1;
        if let Some(tcp) = yields {
            c.yielding.insert(number, (address, tcp));
        }
        drop(c);
        if let Some(tcp) = ended {
            let _ = crate::sys::hang_up(&tcp);
        }
        Some(Permit { gate: self.clone(), address, number })
    }
}

impl Permit {
    /// Keep the place from here on: the connection is not hung up to make room for another. False when it has been hung up already,
    /// so a caller that is about to do for the device what cannot be asked twice does not.
    pub fn keep(&self) -> bool {
        let mut c = self.gate.counts.lock().unwrap_or_else(PoisonError::into_inner);
        c.yielding.remove(&self.number);
        !c.leaving.contains(&self.number)
    }

    /// The known device ``device`` answered from this address (`Gate::vouch`).
    pub fn vouch(&self, device: &str) {
        self.gate.vouch(self.address, device);
    }
}

impl Drop for Permit {
    fn drop(&mut self) {
        let mut c = self.gate.counts.lock().unwrap_or_else(PoisonError::into_inner);
        // The place of a connection that was hung up to make room is another's already.
        if !c.leaving.remove(&self.number) {
            c.yielding.remove(&self.number);
            c.give_back(self.address);
        }
    }
}

thread_local! {
    /// How long this thread has waited on the node itself (`unhurried`) since it started.
    static OWN_WAITS: Cell<Duration> = const { Cell::new(Duration::ZERO) };
}

/// ``wait``, a wait on the node itself, whose time no deadline of a connection of this thread counts: a peer is not late because
/// the node was busy.
pub fn unhurried<T>(wait: impl FnOnce() -> T) -> T {
    let began = Instant::now();
    let out = wait();
    OWN_WAITS.set(OWN_WAITS.get() + began.elapsed());
    out
}

/// A connection with a deadline on the whole of a stage, not on each read: a host that sends a byte every few seconds is cut off
/// all the same. With no deadline, the socket's own timeouts hold.
pub struct Timed {
    tcp: Arc<TcpStream>,
    until: Option<Deadline>,
}

/// When a stage is to be done, and how long its thread had waited on the node when the stage began.
#[derive(Clone, Copy)]
struct Deadline {
    at: Instant,
    waited: Duration,
}

impl Deadline {
    fn within(span: Duration) -> Deadline {
        Deadline { at: Instant::now() + span, waited: OWN_WAITS.get() }
    }

    fn left(self) -> Duration {
        (self.at + OWN_WAITS.get().saturating_sub(self.waited)).saturating_duration_since(Instant::now())
    }
}

impl Timed {
    /// ``tcp`` with no deadline.
    pub fn open(tcp: impl Into<Arc<TcpStream>>) -> Timed {
        Timed { tcp: tcp.into(), until: None }
    }

    /// ``tcp``, to be done with within ``span``.
    pub fn within(tcp: impl Into<Arc<TcpStream>>, span: Duration) -> Timed {
        Timed { tcp: tcp.into(), until: Some(Deadline::within(span)) }
    }

    /// The reads and writes from now on are done within ``span`` together.
    pub fn arm(&mut self, span: Duration) {
        self.until = Some(Deadline::within(span));
    }

    /// End the deadline: each read and each write from now on may take ``each``.
    pub fn disarm(&mut self, each: Duration) -> std::io::Result<()> {
        self.rest(Some(each), each)
    }

    /// End the deadline: each read from now on may take ``read`` (any time at all when None) and each write ``write``.
    pub fn rest(&mut self, read: Option<Duration>, write: Duration) -> std::io::Result<()> {
        self.until = None;
        self.tcp.set_read_timeout(read)?;
        self.tcp.set_write_timeout(Some(write))
    }

    pub fn into_inner(self) -> Arc<TcpStream> {
        self.tcp
    }

    fn left(&self) -> std::io::Result<Option<Duration>> {
        let Some(until) = self.until else { return Ok(None) };
        let left = until.left();
        if left.is_zero() { Err(std::io::ErrorKind::TimedOut.into()) } else { Ok(Some(left)) }
    }
}

impl Read for Timed {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        if let Some(left) = self.left()? {
            self.tcp.set_read_timeout(Some(left))?;
        }
        (&*self.tcp).read(buf)
    }
}

impl Write for Timed {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if let Some(left) = self.left()? {
            self.tcp.set_write_timeout(Some(left))?;
        }
        (&*self.tcp).write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        (&*self.tcp).flush()
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
        phone.vouch("phone");
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

    /// Whether the gate hung up the server's end of the connection ``client`` is the other end of. The socket is polled: Windows
    /// leaves one whose read timed out in no usable state.
    fn hung_up(client: &mut TcpStream) -> bool {
        client.set_nonblocking(true).unwrap();
        let until = Instant::now() + Duration::from_millis(200);
        loop {
            match client.read(&mut [0u8; 1]) {
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock && Instant::now() < until => std::thread::sleep(Duration::from_millis(5)),
                Err(e) => return e.kind() != std::io::ErrorKind::WouldBlock,
                Ok(n) => return n == 0,
            }
        }
    }

    /// A connection that yields its place, from ``ip``: its place, the client's end and the server's.
    fn yielding(gate: &Arc<Gate>, ip: IpAddr) -> Option<(Permit, TcpStream, Arc<TcpStream>)> {
        let (client, server) = connected();
        let server = Arc::new(server);
        gate.enter_yielding(ip, &server).map(|place| (place, client, server))
    }

    #[test]
    fn a_known_address_at_its_share_gives_up_its_oldest_connection_that_showed_nothing() {
        let gate = Gate::new(10, 3, 0);
        let mut held: Vec<_> = (0..3).map(|_| yielding(&gate, at(1)).unwrap()).collect();
        assert!(yielding(&gate, at(1)).is_none(), "an address no device answered from has its share and no more");
        held[0].0.keep();
        held[0].0.vouch("b");
        let fourth = yielding(&gate, at(1)).expect("a known address makes room among its own");
        assert!(hung_up(&mut held[1].1), "the oldest that showed nothing goes");
        assert!(!hung_up(&mut held[0].1) && !hung_up(&mut held[2].1), "the one that was kept stays, and so does the later one");
        drop(held.remove(1));
        let again = yielding(&gate, at(1)).expect("room is made again");
        assert!(hung_up(&mut held[1].1), "the place of a hung-up connection was not given back twice");
        fourth.0.keep();
        again.0.keep();
        held[1].0.keep();
        assert!(yielding(&gate, at(1)).is_none(), "and nothing is taken from connections that were kept");
    }

    #[test]
    fn a_known_address_that_finds_every_place_taken_takes_one_of_the_address_that_holds_the_most() {
        let gate = Gate::new(6, 4, 2);
        let known = yielding(&gate, at(1)).unwrap();
        known.0.keep();
        known.0.vouch("b");
        let mut most: Vec<_> = (0..2).map(|_| yielding(&gate, at(2)).unwrap()).collect();
        let mut fewer = yielding(&gate, at(3)).unwrap();
        let reserved: Vec<_> = (0..2).map(|_| yielding(&gate, at(1)).unwrap()).collect();
        reserved.iter().for_each(|r| assert!(r.0.keep()));
        assert!(yielding(&gate, at(4)).is_none(), "a stranger has no claim");
        let _more = yielding(&gate, at(1)).expect("a known address is not refused for the others");
        assert!(hung_up(&mut most[0].1), "the address that holds the most gives up its oldest");
        assert!(!hung_up(&mut most[1].1) && !hung_up(&mut fewer.1));
    }

    #[test]
    fn a_connection_that_was_hung_up_to_make_room_cannot_keep_its_place() {
        let gate = Gate::new(10, 2, 0);
        let first = yielding(&gate, at(1)).unwrap();
        first.0.vouch("b");
        let second = yielding(&gate, at(1)).unwrap();
        let _third = yielding(&gate, at(1)).expect("room is made");
        assert!(!first.0.keep(), "the oldest was hung up");
        assert!(second.0.keep() && second.0.keep(), "one that still has its place keeps it, however often it asks");
    }

    #[test]
    fn the_address_that_gives_up_a_place_is_the_one_with_the_most_that_may_be_taken() {
        let gate = Gate::new(6, 6, 0);
        let known = yielding(&gate, at(1)).unwrap();
        known.0.keep();
        known.0.vouch("b");
        let channels: Vec<_> = (0..2).map(|_| yielding(&gate, at(2)).unwrap()).collect();
        channels.iter().for_each(|c| {
            c.0.keep();
        });
        let mut one = yielding(&gate, at(2)).unwrap();
        let mut two: Vec<_> = (0..2).map(|_| yielding(&gate, at(3)).unwrap()).collect();
        let _more = yielding(&gate, at(1)).expect("a known address makes room");
        assert!(hung_up(&mut two[0].1), "the address with the most that may be taken gives up its oldest");
        assert!(!hung_up(&mut one.1) && !hung_up(&mut two[1].1), "the address that holds the most in all, of which one may be taken, does not");
    }

    #[test]
    fn only_so_many_connections_are_on_their_way_out() {
        let gate = Gate::new(100, 1, 0);
        let first = yielding(&gate, at(1)).unwrap();
        first.0.vouch("b");
        let mut held = vec![first];
        held.extend(std::iter::from_fn(|| yielding(&gate, at(1))).take(MOST_LEAVING + 8));
        assert_eq!(held.len(), 1 + MOST_LEAVING, "nothing more is hung up while those before have not ended");
        held.remove(0);
        assert!(yielding(&gate, at(1)).is_some(), "one ended, so another may be hung up");
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
