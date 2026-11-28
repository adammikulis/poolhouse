//! What a host that is nobody costs the node: how long and how many connections it holds, and what the node survives when the system
//! gives it no descriptor or no thread.

mod kit;

use std::io::{ErrorKind, Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use kit::netkit::*;
use poolhouse_node::join_code::{context_for, Session};
use poolhouse_node::net::Net;
use poolhouse_node::peer::PeerClient;
use poolhouse_node::tls::HANDSHAKE_FOR;
use serde_json::{json, Value};

fn dial(port: u16) -> TcpStream {
    TcpStream::connect(("127.0.0.1", port)).unwrap()
}

/// Whether the other end ends ``tcp`` (an end of the stream or a reset) within ``wait``; what it sends first is read and dropped.
/// The socket is polled: Windows leaves one whose read timed out in no usable state.
fn ends_within(tcp: &mut TcpStream, wait: Duration) -> bool {
    let until = Instant::now() + wait;
    let mut got = [0u8; 4096];
    tcp.set_nonblocking(true).unwrap();
    let ended = loop {
        match tcp.read(&mut got) {
            Ok(0) => break true,
            Ok(_) => {}
            Err(e) if e.kind() == ErrorKind::WouldBlock => {
                if Instant::now() >= until {
                    break false;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            Err(_) => break true,
        }
    };
    tcp.set_nonblocking(false).unwrap();
    ended
}

/// Which of ``held`` the other end has closed altogether: the first byte sent to a closed socket draws a reset, and the next one fails.
fn closed(held: &mut [TcpStream]) -> usize {
    let first: Vec<bool> = held.iter_mut().map(|c| c.write_all(b"x").is_err()).collect();
    std::thread::sleep(Duration::from_millis(300));
    held.iter_mut().zip(first).map(|(c, failed)| failed || c.write_all(b"x").is_err()).filter(|gone| *gone).count()
}

#[test]
fn a_connection_that_sends_a_byte_at_a_time_is_cut_off_at_the_handshake_deadline() {
    let a = device();
    let mut c = dial(a.net.port);
    // the head of a long handshake record, which the bytes after it never fill
    c.write_all(&[0x16, 0x03, 0x01, 0x40, 0x00]).unwrap();
    let began = Instant::now();
    while !ends_within(&mut c, Duration::from_millis(500)) {
        assert!(began.elapsed() < Duration::from_secs(20), "the connection is still held after {:?}", began.elapsed());
        let _ = c.write_all(&[0]);
    }
    assert!(began.elapsed() > Duration::from_secs(5), "closed after {:?}, before a slow peer could finish", began.elapsed());
}

#[test]
fn an_idle_connection_is_closed_at_the_deadline_and_not_kept_to_linger() {
    let a = device();
    let mut held = [dial(a.net.port)];
    assert!(ends_within(&mut held[0], Duration::from_secs(20)), "nothing ended the connection within 20 s");
    assert_eq!(closed(&mut held), 1, "a handshake that timed out has no alert to be read, so the socket is closed");
}

#[test]
fn only_so_many_refused_connections_linger_at_once() {
    let a = device();
    let mut held: Vec<TcpStream> = (0..40).map(|_| dial(a.net.port)).collect();
    for c in &mut held {
        c.write_all(b"GET / HTTP/1.1\r\n\r\n").unwrap();
    }
    for c in &mut held {
        assert!(ends_within(c, Duration::from_secs(3)), "each is refused");
    }
    assert_eq!(closed(&mut held), 8, "32 linger for their alert to be read, and the rest are closed");
}

#[test]
fn one_address_holds_so_many_connections_and_the_next_is_closed() {
    let a = device();
    let mut held: Vec<TcpStream> = (0..60).map(|_| dial(a.net.port)).collect();
    std::thread::sleep(Duration::from_millis(500));
    let gone = held.iter_mut().filter_map(|c| ends_within(c, Duration::from_millis(5)).then_some(())).count();
    assert_eq!(gone, 12, "one address holds 48");
    drop(held);
    eventually("the places to be given back", 5, || {
        let mut next = dial(a.net.port);
        !ends_within(&mut next, Duration::from_millis(200))
    });
}

#[test]
fn a_stranger_is_kept_for_its_time_only_and_a_member_for_as_long_as_it_asks() {
    let (a, b, stranger) = (device(), device(), device());
    a.join_with_code(&b);
    let hello = json!({"op": "hello"});
    let mut unknown = PeerClient::connect(&stranger.net.me, a.addr(), Some(&a.fp())).unwrap();
    let mut member = PeerClient::connect(&b.net.me, a.addr(), Some(&a.fp())).unwrap();
    let began = Instant::now();
    while unknown.call(&hello).is_ok() {
        assert!(began.elapsed() < Duration::from_secs(40), "a stranger that keeps asking is still served after {:?}", began.elapsed());
        member.call(&hello).unwrap();
        std::thread::sleep(Duration::from_secs(2));
    }
    assert!(began.elapsed() > Duration::from_secs(20), "closed after {:?}", began.elapsed());
    member.call(&hello).unwrap();
}

fn connect(from: &Dev, to: &Dev) -> poolhouse_node::error::Result<PeerClient> {
    PeerClient::connect(&from.net.me, to.addr(), Some(&to.fp()))
}

/// Whether ``from`` dials ``to`` and is answered as a member.
fn served(from: &Dev, to: &Dev) -> poolhouse_node::error::Result<serde_json::Value> {
    connect(from, to).and_then(|mut c| c.call(&json!({"op": "boards"})))
}

/// The connections of ``tries`` that ``from``, which no pool lists, was let open to ``to``, oldest first.
fn crowd(from: &Dev, to: &Dev, tries: usize) -> Vec<PeerClient> {
    (0..tries).filter_map(|_| connect(from, to).ok().and_then(|mut c| c.call(&json!({"op": "hello"})).ok().map(|_| c))).collect()
}

/// How many of ``held`` still answer.
fn answering(held: &mut [PeerClient]) -> usize {
    held.iter_mut().filter_map(|c| c.call(&json!({"op": "hello"})).ok()).count()
}

#[test]
fn a_member_gets_in_past_strangers_at_its_own_address() {
    let (a, b, stranger) = (device(), device(), device());
    a.join_with_code(&b);
    b.net.sync_once(true).unwrap();
    eventually("a to hold the channel b opened", 10, || a.member(&b.fp()).unwrap()["reach"] == "channel");
    let mut strangers = crowd(&stranger, &a, 60);
    for dial in 0..20 {
        if let Err(e) = served(&b, &a) {
            panic!("dial {dial} of the member, whose address {} strangers hold: {e}", strangers.len());
        }
    }
    let left = answering(&mut strangers);
    assert!((27..=47).contains(&left), "one stranger at most goes for each dial, and with the channel they are 48 at most: {left} answer");
    a.net.call_member(&b.fp(), None, &json!({"op": "boards"})).expect("the channel is not taken from the member");
}

#[test]
fn a_member_this_device_reached_is_vouched_for_at_its_address() {
    let (a, b, stranger) = (device(), device(), device());
    a.join_with_code(&b);
    assert_eq!(a.net.sync_once(true).unwrap(), 1, "a dials b, which never dialed a as a member");
    let _strangers = crowd(&stranger, &a, 60);
    served(&b, &a).expect("the address a reached b at is known to a");
}

#[test]
fn the_address_a_member_was_recorded_at_is_vouched_for_after_a_restart() {
    let (a, b, stranger) = (device(), device(), device());
    a.join_with_code(&b);
    let fp = a.fp();
    let dir = a.stop();
    let node = Arc::new(Mutex::new(kit::node(dir.path())));
    let net = Net::start(node.clone(), config(None)).unwrap();
    let a = Dev { dir, node, net, token: String::new() };
    assert_eq!(a.fp(), fp);
    let _strangers = crowd(&stranger, &a, 60);
    served(&b, &a).expect("a knows the address it recorded for b before it restarted");
}

#[test]
fn removing_the_last_member_at_an_address_forgets_the_address() {
    let (a, b, c, stranger) = (device(), device(), device(), device());
    a.join_with_code(&b);
    a.join_with_code(&c);
    served(&b, &a).unwrap();
    served(&c, &a).unwrap();
    let remove = |who: &Dev| a.ok("member_remove", "", &a.token, json!({"fingerprint": who.fp()}));

    remove(&b);
    let strangers = crowd(&stranger, &a, 60);
    served(&c, &a).expect("c is still a member at the address");
    drop(strangers);

    remove(&c);
    let mut strangers = Vec::new();
    eventually("the places the first strangers held to be given back", 10, || {
        strangers = crowd(&stranger, &a, 60);
        strangers.len() >= 48
    });
    assert_eq!(strangers.len(), 48, "an address no member is at is refused past its share");
    assert_eq!(answering(&mut strangers), 48, "and takes no place from the connections it holds");
}

#[test]
fn a_peer_is_not_late_for_the_time_the_node_was_busy() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let node = a.node.clone();
    let (locked, wait) = std::sync::mpsc::channel();
    let busy = std::thread::spawn(move || {
        let _held = node.lock().unwrap();
        locked.send(()).unwrap();
        std::thread::sleep(HANDSHAKE_FOR + Duration::from_secs(2));
    });
    wait.recv().unwrap();
    served(&b, &a).expect("the wait for the node is not the peer's");
    busy.join().unwrap();
}

/// ``joiner``'s connection to ``to`` with the exchange of ``code`` made, and the proof that asks to be admitted.
fn exchanged(joiner: &Dev, to: &Dev, code: &str) -> (PeerClient, Value) {
    let mut c = PeerClient::connect(&joiner.net.me, to.addr(), None).unwrap();
    let mut s = Session::start(true, code, &context_for("a nonce"), &joiner.fp(), &c.fingerprint);
    let reply = c.call(&json!({"op": "join_exchange", "message": s.message(), "nonce": "a nonce", "name": "joiner"})).unwrap();
    s.receive(reply["message"].as_str().unwrap()).unwrap();
    let proof = json!({"op": "join_confirm", "confirmation": s.confirmation().unwrap(), "port": joiner.net.port});
    (c, proof)
}

/// A whole share of one address's connections to ``to``, silent, once the listener has taken them.
fn later(to: &Dev) -> Vec<TcpStream> {
    let held = (0..48).map(|_| dial(to.net.port)).collect();
    std::thread::sleep(Duration::from_millis(500));
    held
}

/// A pool of ``policy`` on a first device, with a member at the loopback address.
fn pool_with_a_member_here(policy: &str) -> (Dev, Dev) {
    let (a, b) = (device(), device());
    kit::govnode::found(&a, kit::gov::person(kit::govnode::ANN, "ann"), policy);
    a.join_with_code(&b);
    served(&b, &a).unwrap();
    (a, b)
}

fn code_of(a: &Dev) -> String {
    a.ok("add_device", "", &a.token, json!({}))["code"].as_str().unwrap().to_string()
}

/// ``ask`` sent on ``c`` while ``to`` is busy, so that it is read and not yet answered when later connections from the same address
/// take the place of the one it came on.
fn cut_off_before_the_answer(mut c: PeerClient, ask: Value, to: &Dev) -> Vec<TcpStream> {
    // answered once, so that the listener is waiting for the next request and has no other use for the node before it
    c.call(&json!({"op": "hello"})).unwrap();
    let busy = to.node.lock().unwrap();
    let asking = std::thread::spawn(move || c.call(&ask).map_err(|e| e.to_string()));
    std::thread::sleep(Duration::from_millis(500));
    let held = later(to);
    let answer = asking.join().unwrap();
    assert!(answer.is_err(), "the later connections take its place: {answer:?}");
    drop(busy);
    // the node now works through the request it had read
    std::thread::sleep(Duration::from_secs(1));
    held
}

#[test]
fn a_device_admitted_by_a_code_keeps_its_connection_past_later_ones() {
    let (a, _b) = pool_with_a_member_here("secure");
    let joiner = device();
    let (mut c, proof) = exchanged(&joiner, &a, &code_of(&a));
    c.call(&proof).unwrap();
    assert!(a.is_member(&joiner.fp()));
    let _later = later(&a);
    c.call(&json!({"op": "hello"})).expect("the connection of an admitted device is not hung up for another");
}

#[test]
fn a_device_admitted_by_an_open_pool_keeps_its_connection_past_later_ones() {
    let (a, _b) = pool_with_a_member_here("open");
    let guest = device();
    let mut c = connect(&guest, &a).unwrap();
    c.call(&json!({"op": "join_open", "name": "guest", "port": guest.net.port})).unwrap();
    assert!(a.is_member(&guest.fp()));
    let _later = later(&a);
    c.call(&json!({"op": "hello"})).expect("the connection of an admitted device is not hung up for another");
}

#[test]
fn a_join_cut_off_before_the_pool_admits_leaves_the_code_to_use() {
    let (a, _b) = pool_with_a_member_here("secure");
    let joiner = device();
    let code = code_of(&a);
    let (c, proof) = exchanged(&joiner, &a, &code);
    let _later = cut_off_before_the_answer(c, proof, &a);
    assert!(!a.is_member(&joiner.fp()), "the pool lists a device that did not join");
    joiner.ok("join_pool", "", &joiner.token, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": code}));
    assert!(a.is_member(&joiner.fp()) && joiner.pool() == a.pool());
}

#[test]
fn an_open_join_cut_off_before_the_pool_admits_admits_nobody_and_uses_up_no_ask() {
    let (a, _b) = pool_with_a_member_here("open");
    let guest = device();
    let ask = json!({"op": "join_open", "name": "guest", "port": guest.net.port});
    // as many as one address may ask in the window
    for _ in 0..5 {
        let c = connect(&guest, &a).unwrap();
        drop(cut_off_before_the_answer(c, ask.clone(), &a));
        assert!(!a.is_member(&guest.fp()), "the pool lists a device that did not join");
    }
    connect(&guest, &a).unwrap().call(&ask).expect("the cut asks were not counted");
    assert!(a.is_member(&guest.fp()));
}

/// Every source of the node outside its test modules starts a thread with `thread::Builder`, which answers an error where
/// `thread::spawn` panics, and a panic ends a release node.
#[test]
fn no_thread_is_started_in_a_way_that_ends_the_node_when_the_system_has_none() {
    fn scan(dir: &Path, found: &mut Vec<String>) {
        for entry in std::fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            if path.is_dir() {
                scan(&path, found);
            } else if name.ends_with(".rs") && !name.ends_with("tests.rs") {
                let text = std::fs::read_to_string(&path).unwrap().replace('\r', "");
                let code = text.split("#[cfg(test)]\nmod ").next().unwrap();
                found.extend(code.lines().enumerate().filter(|(_, l)| l.contains("thread::spawn(")).map(|(n, _)| format!("{name}:{}", n + 1)));
            }
        }
    }
    let mut found = Vec::new();
    scan(&Path::new(env!("CARGO_MANIFEST_DIR")).join("src"), &mut found);
    assert!(found.is_empty(), "thread::spawn in {found:?}");
}

#[cfg(unix)]
mod process {
    use super::*;
    use kit::short_dir;
    use poolhouse_node::client::Client;
    use std::os::unix::net::UnixStream;
    use std::os::unix::process::CommandExt;
    use std::process::{Child, Command, Stdio};

    /// A node process of the test's own state directory, killed when dropped.
    struct Running {
        child: Child,
        state: tempfile::TempDir,
    }

    impl Drop for Running {
        fn drop(&mut self) {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }

    /// Start the node with at most ``descriptors`` open files, ``more`` arguments and ``env`` over an empty environment.
    fn start(descriptors: u64, more: &[&str], env: &[(&str, &str)]) -> Running {
        let state = short_dir();
        let mut command = Command::new(env!("CARGO_BIN_EXE_poolhouse-node"));
        command.arg("run").arg("--state").arg(state.path()).args(more).env_clear().env("HOME", state.path()).envs(env.iter().copied())
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        // SAFETY: setrlimit is async-signal-safe and reads the one struct it is given.
        unsafe {
            command.pre_exec(move || {
                let few = libc::rlimit { rlim_cur: descriptors as libc::rlim_t, rlim_max: descriptors as libc::rlim_t };
                if libc::setrlimit(libc::RLIMIT_NOFILE, &few) != 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let node = Running { child: command.spawn().unwrap(), state };
        eventually("the node's socket", 10, || UnixStream::connect(node.state.path().join("node.sock")).is_ok());
        node
    }

    /// Let this test process open the connections the node is flooded with.
    fn room_to_dial() {
        // SAFETY: both calls take the one plain-data struct they are given.
        unsafe {
            let mut now: libc::rlimit = std::mem::zeroed();
            assert_eq!(libc::getrlimit(libc::RLIMIT_NOFILE, &mut now), 0);
            now.rlim_cur = now.rlim_max.min(4096);
            assert_eq!(libc::setrlimit(libc::RLIMIT_NOFILE, &now), 0);
        }
    }

    fn answers(node: &Running) -> bool {
        Client::connect(node.state.path()).and_then(|mut c| c.call("hello", "", "", json!({}))).is_ok()
    }

    #[test]
    fn the_node_stays_up_when_local_connections_use_every_descriptor() {
        room_to_dial();
        let mut node = start(64, &[], &[]);
        let held: Vec<UnixStream> = (0..100).filter_map(|_| UnixStream::connect(node.state.path().join("node.sock")).ok()).collect();
        std::thread::sleep(Duration::from_secs(1));
        assert_eq!(node.child.try_wait().unwrap(), None, "the node ended when it could not take a connection");
        drop(held);
        eventually("the node to answer again", 10, || answers(&node));
    }

    #[test]
    fn idle_peers_neither_end_the_node_nor_keep_a_local_client_out() {
        room_to_dial();
        let port = std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        let listen = format!("127.0.0.1:{port}");
        let mut node = start(256, &["--listen", &listen], &[]);
        eventually("the peer listener", 10, || TcpStream::connect(&listen).is_ok());
        let held: Vec<TcpStream> = (0..300).filter_map(|_| TcpStream::connect_timeout(&listen.parse().unwrap(), Duration::from_millis(200)).ok()).collect();
        std::thread::sleep(Duration::from_secs(1));
        assert!(answers(&node), "a local client is answered while {} peers wait", held.len());
        assert_eq!(node.child.try_wait().unwrap(), None);
    }

    #[test]
    fn a_node_that_can_start_no_thread_closes_the_connection_and_stays_up() {
        // a stack larger than the address space: the system starts no thread, and a node with no network starts none until a client comes
        let mut node = start(256, &[], &[("RUST_MIN_STACK", "281474976710656")]);
        for _ in 0..2 {
            assert!(!answers(&node), "no thread serves the connection, so it is closed");
            std::thread::sleep(Duration::from_millis(300));
            assert_eq!(node.child.try_wait().unwrap(), None, "the node ended when it could not start a thread");
        }
    }
}
