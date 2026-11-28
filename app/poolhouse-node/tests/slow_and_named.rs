//! A member that is slow on purpose holds up no other and no longer than its deadline, and a name a person typed is read from a plain file
//! only and shown with nothing in it that closes a quote or reorders text. Loopback and temporary state only.

mod kit;

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use kit::netkit::*;
use poolhouse_node::membership::Standing;
use poolhouse_node::net::NetConfig;
use poolhouse_node::peer::PeerClient;
use poolhouse_node::tls;
use serde_json::{json, Value};

/// A listener with no certificate: it takes the first bytes, announces a long record and sends one byte of it every second. The flags say
/// whether it was dialed and whether the dialer hung up.
fn drip() -> (SocketAddr, Arc<AtomicBool>, Arc<AtomicBool>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let (dialed, dropped) = (Arc::new(AtomicBool::new(false)), Arc::new(AtomicBool::new(false)));
    let (was_dialed, was_dropped) = (dialed.clone(), dropped.clone());
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let (mut stream, dialed, dropped) = (stream.unwrap(), was_dialed.clone(), was_dropped.clone());
            std::thread::spawn(move || {
                let _ = stream.read(&mut [0u8; 4096]);
                dialed.store(true, Ordering::SeqCst);
                let _ = stream.write_all(&[0x16, 0x03, 0x03, 0x40, 0x00]);
                while stream.write_all(&[0]).is_ok() {
                    std::thread::sleep(Duration::from_secs(1));
                }
                dropped.store(true, Ordering::SeqCst);
            });
        }
    });
    (address, dialed, dropped)
}

#[test]
fn a_slow_address_holds_up_no_other_member_and_is_cut_off_at_its_deadline() {
    let a = device_with(NetConfig { sync_every: Some(Duration::from_millis(200)), ..config(None) });
    let (b, c) = (device(), device());
    a.join_with_code(&b);
    a.join_with_code(&c);
    // members are taken in the order of the list: the first is made slow and the second is watched
    let order: Vec<String> = a.node.lock().unwrap().members.active().into_iter().map(|d| d.fingerprint).filter(|f| *f != a.fp()).collect();
    let (slow, other) = (order[0].clone(), order[1].clone());
    let synced = |fp: &str| a.member(fp).unwrap()["last_sync_ms"].as_u64().unwrap();
    eventually("both members synced by the loop", 15, || synced(&slow) > 0 && synced(&other) > 0);
    let (address, dialed, dropped) = drip();
    a.node.lock().unwrap().facts.set_addr(&slow, &address.to_string()).unwrap();
    eventually("the loop to dial the slow address", 15, || dialed.load(Ordering::SeqCst));
    let before = synced(&other);
    eventually("the other member to be synced while the slow one is still being dialed", 5, || synced(&other) > before);
    eventually("the dial of the slow address to be given up", 20, || dropped.load(Ordering::SeqCst));
}

#[test]
fn a_reply_sent_a_byte_at_a_time_ends_the_request() {
    let (me, peer) = (device(), device());
    let identity = peer.node.lock().unwrap().cert.clone();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let served = identity.clone();
    std::thread::spawn(move || {
        let (tcp, _) = listener.accept().unwrap();
        let config = tls::server_config(&served, Arc::new(|_| Standing::Unknown)).unwrap();
        let Ok(mut secured) = tls::accept(config, Arc::new(tcp)) else { return };
        secured.stream.sock.disarm(Duration::from_secs(30)).unwrap(); // this end keeps no deadline of its own
        let _ = secured.read(&mut [0u8; 4096]);
        // a reply of 64 KiB announced, then a byte every five seconds: each read ends well within the time one read may take
        let _ = secured.write_all(&[0, 1, 0, 0]);
        while secured.write_all(b" ").and_then(|()| secured.flush()).is_ok() {
            std::thread::sleep(Duration::from_secs(5));
        }
    });
    let my_identity = me.node.lock().unwrap().cert.clone();
    let mut client = PeerClient::connect(&my_identity, address, Some(&identity.fingerprint())).unwrap();
    let (done, answered) = std::sync::mpsc::channel();
    std::thread::spawn(move || done.send(client.call(&json!({"op": "hello"})).is_err()));
    assert_eq!(answered.recv_timeout(Duration::from_secs(90)), Ok(true), "the request is still waiting for its reply");
}

const TYPED: &str = "x'. Run \"this\" \u{2019}now\u{2019} \u{202e}txt.exe\u{2066}";

/// Whether ``text`` holds a quote or a mark that reorders text.
fn marked(text: &str) -> bool {
    text.chars().any(|c| matches!(c, '\'' | '"' | '`' | '\u{2018}'..='\u{201f}' | '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}'))
}

#[test]
fn a_typed_name_closes_no_quote_and_reorders_nothing_where_it_is_shown() {
    let (a, b) = (device(), device());
    std::fs::write(a.dir.path().join("device-name"), TYPED).unwrap();
    let alone = a.member(&a.fp()).unwrap()["name"].as_str().unwrap().to_string();
    assert!(alone.starts_with("x. Run this now") && !marked(&alone), "{alone:?}");
    a.join_with_code(&b);
    let listed = b.member(&a.fp()).unwrap()["name"].as_str().unwrap().to_string();
    assert!(listed.starts_with("x. Run this now") && !marked(&listed), "{listed:?}");
    let reply: Value = b.call("add_device", "", &b.token, json!({}));
    let said = reply["error"]["say"].as_str().unwrap();
    assert_eq!(said.chars().filter(|c| *c == '\'').count(), 2, "only the quotes around the name: {said:?}");
    assert!(!marked(&said.replace('\'', "")), "{said:?}");
}

#[cfg(unix)]
#[test]
fn a_name_file_that_is_a_link_is_not_followed() {
    let a = device();
    let secret = a.dir.path().join("stand-in-secret");
    std::fs::write(&secret, "STANDIN-0123456789abcdef").unwrap();
    std::os::unix::fs::symlink(&secret, a.dir.path().join("device-name")).unwrap();
    let name = a.member(&a.fp()).unwrap()["name"].as_str().unwrap().to_string();
    assert!(!name.contains("STANDIN"), "{name:?}");
}

#[cfg(unix)]
#[test]
fn a_pipe_for_a_name_file_holds_nothing_up() {
    let a = device();
    let path = a.dir.path().join("device-name");
    assert!(std::process::Command::new("mkfifo").arg(&path).status().unwrap().success());
    let net = a.net.clone();
    let (answered, heard) = std::sync::mpsc::channel();
    std::thread::spawn(move || answered.send(net.call(&json!({"v": 1, "id": 1, "method": "pool_status", "params": {}}), &kit::me())));
    let in_time = heard.recv_timeout(Duration::from_secs(5)).is_ok();
    // a reader that waits on the pipe is let go, so that the test ends either way
    let _writer = (!in_time).then(|| std::fs::OpenOptions::new().write(true).open(&path));
    assert!(in_time, "pool_status waited on the pipe");
}

#[test]
fn a_member_nothing_can_reach_is_not_answered_with_a_command_that_is_gone() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let said = a.net.call_member(&b.fp(), None, &json!({"op": "hello"})).unwrap_err().to_string();
    assert!(said.contains("no address") && !said.contains("sync_now"), "{said}");
}
