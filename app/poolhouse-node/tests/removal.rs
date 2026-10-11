//! A device that has recorded a removal exchanges nothing more with the removed device: not on a connection it already holds, not in a
//! sync cycle that began before it heard, not from a reply that arrives after it recorded the removal.

mod kit;

use kit::gov::person;
use kit::govnode::{found, sync, ANN};
use kit::netkit::*;
use poolhouse_node::cert::{self, Identity};
use poolhouse_node::error::Error;
use poolhouse_node::fsutil::hex;
use poolhouse_node::membership::Standing;
use poolhouse_node::netsync;
use poolhouse_node::peer::PeerClient;
use serde_json::json;

/// A device and the token of a session of its own on `demo` (every device's first session has the same name, so rows of two of them clash).
struct Writer<'a> {
    dev: &'a Dev,
    token: String,
}

fn writer<'a>(dev: &'a Dev, id: &str) -> Writer<'a> {
    Writer { token: dev.session("demo", id).1, dev }
}

impl Writer<'_> {
    fn say(&self, text: &str) {
        self.dev.say("demo", &self.token, text);
    }

    fn holds(&self, text: &str) -> bool {
        self.dev.texts("demo", &self.token).iter().any(|t| t == text)
    }
}

fn remove(by: &Dev, who: &Dev) {
    by.ok("member_remove", "", &by.token, json!({"fingerprint": who.fp()}));
}

fn outsider(seed: u8) -> Identity {
    let dir = tempfile::tempdir().unwrap();
    cert::load_or_create(dir.path(), &kit::key(seed)).unwrap()
}

#[test]
fn a_connection_open_to_a_member_that_is_then_removed_carries_nothing_more_either_way() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let (wa, wb) = (writer(&a, "a"), writer(&b, "b"));
    let mut live = PeerClient::connect(&a.net.me, b.addr(), Some(&b.fp())).unwrap();
    live.call(&json!({"op": "hello"})).unwrap();
    remove(&a, &b);
    wb.say("written by the removed device");
    wa.say("written after the removal");
    let got = netsync::sync_all(&a.node, &mut live);
    assert!(matches!(got, Err(Error::Denied(_))), "{got:?}");
    assert!(!wa.holds("written by the removed device"), "the removed device's entry was taken over a connection opened before the removal");
    assert!(!wb.holds("written after the removal"), "an entry was sent to the removed device over a connection opened before the removal");
    assert_eq!(b.member(&b.fp()).unwrap()["status"], "active", "the pool record was sent to the removed device over a connection opened before the removal");
}

#[test]
fn a_board_sync_in_progress_with_a_member_that_is_then_removed_stops() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let (wa, wb) = (writer(&a, "a"), writer(&b, "b"));
    let mut live = PeerClient::connect(&a.net.me, b.addr(), Some(&b.fp())).unwrap();
    netsync::peer_boards(&mut live).unwrap();
    remove(&a, &b);
    wb.say("written by the removed device");
    let got = netsync::sync_board(&a.node, &mut live, "demo");
    assert!(matches!(got, Err(Error::Denied(_))), "{got:?}");
    assert!(!wa.holds("written by the removed device"));
}

#[test]
fn the_pool_record_a_removed_device_sends_is_not_merged() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let stranger = outsider(7);
    let reply = json!({"rows": [{"fingerprint": stranger.fingerprint(), "cert": hex(&stranger.der), "name": "stranger", "status": "active", "at": 1, "by": "b"}]});
    assert!(netsync::take_members(&a.node, &b.fp(), &reply).is_ok(), "a member's reply is read for its builds");
    assert_eq!(a.node.lock().unwrap().members.standing(&stranger.fingerprint()), Standing::Unknown, "a member enrolled a device through a reply");
    remove(&a, &b);
    let got = netsync::take_members(&a.node, &b.fp(), &reply);
    assert!(matches!(got, Err(Error::Denied(_))), "{got:?}");
    assert_eq!(a.node.lock().unwrap().members.standing(&stranger.fingerprint()), Standing::Unknown, "a removed device enrolled a device through a reply that arrived after the removal");
}

#[test]
fn a_cycle_that_learns_of_a_removal_does_not_go_on_to_dial_the_removed_device() {
    let (a, b, c) = (device(), device(), device());
    // `a` dials its members in the order `pool_status` lists them, by fingerprint: the first carries the removal of the second, so the
    // pool is founded on the one listed first, which is the admin that removes the other.
    let (revoker, victim) = if b.fp() < c.fp() { (&b, &c) } else { (&c, &b) };
    found(revoker, person(ANN, "ann"), "secure");
    revoker.join_with_code(&a);
    revoker.join_with_code(victim);
    sync(victim);
    a.node.lock().unwrap().facts.set_addr(&victim.fp(), &victim.addr().to_string()).unwrap();
    a.net.sync_once(true).unwrap();
    assert!(a.is_member(&victim.fp()), "a heard of the victim from the revoker's acts");
    let (wa, wv) = (writer(&a, "a"), writer(victim, "victim"));
    remove(revoker, victim);
    wv.say("written by the removed device");
    let last_seen = || victim.member(&a.fp()).unwrap()["last_seen_ms"].clone();
    let before = last_seen();
    a.net.sync_once(true).unwrap();
    assert_eq!(a.member(&victim.fp()).unwrap()["status"], "revoked", "the first dial of the cycle carried the removal");
    assert_eq!(last_seen(), before, "the cycle opened a connection to the removed device after it had recorded the removal");
    assert!(!wa.holds("written by the removed device"), "the cycle dialed the removed device after it had recorded the removal");
    assert_eq!(a.member(&victim.fp()).unwrap()["last_error"], "", "a removed device is not dialed, so no dial to it failed");
}

#[test]
fn a_member_removed_while_it_is_being_dialed_is_not_recorded_as_a_failed_dial() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let silent = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    silent.set_nonblocking(true).unwrap();
    a.node.lock().unwrap().facts.set_addr(&b.fp(), &silent.local_addr().unwrap().to_string()).unwrap();
    let net = a.net.clone();
    let dial = std::thread::spawn(move || net.sync_once(true).unwrap());
    let mut held = None;
    eventually("a to open a connection to the address it holds for b", 10, || {
        held = silent.accept().ok().map(|(stream, _)| stream);
        held.is_some()
    });
    remove(&a, &b);
    drop(held);
    assert_eq!(dial.join().unwrap(), 0, "a dial that ended on a removed member was counted as reached");
    assert_eq!(a.member(&b.fp()).unwrap()["last_error"], "", "the refusal to talk to a removed member was recorded against it");
}

#[test]
fn a_reply_that_removes_its_own_sender_sets_no_policy() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let reply = json!({"rows": [{"fingerprint": b.fp(), "cert": "", "name": "", "status": "revoked", "at": 1, "by": b.fp()}], "policy": {"policy": "open", "at": 4_000_000_000_000u64, "by": "zzz"}});
    assert!(netsync::take_members(&a.node, &b.fp(), &reply).is_ok());
    assert_eq!(a.member(&b.fp()).unwrap()["status"], "active", "a member's reply removed the member that sent it, or another");
    assert_eq!(a.status()["policy"], "secure", "a member's reply set the pool's join policy");
}

/// A member that has not heard of a removal takes what the removed device wrote after it, and passes it on; a device that holds the
/// removal refuses it (docs/node.md, "Who governs a pool": the log cut of a removal). Real nodes, a governed pool, no entry of the
/// removed device's log known to the admin when it removed it.
#[test]
fn rows_of_a_removed_device_relayed_by_an_unaware_member_are_refused_by_a_device_that_holds_the_removal() {
    let (a, b, c) = (device(), device(), device());
    found(&a, person(ANN, "ann"), "secure");
    a.join_with_code(&b);
    a.join_with_code(&c);
    sync(&b);
    b.node.lock().unwrap().facts.set_addr(&c.fp(), &c.addr().to_string()).unwrap();
    b.net.sync_once(true).unwrap();
    assert!(b.is_member(&c.fp()), "the relay lists the device it will take the entry from");
    remove(&a, &c);
    let (wa, wb, wc) = (writer(&a, "a"), writer(&b, "b"), writer(&c, "c"));
    wc.say("written by the removed device");
    let mut live = PeerClient::connect(&b.net.me, c.addr(), Some(&c.fp())).unwrap();
    netsync::sync_all(&b.node, &mut live).unwrap();
    assert!(wb.holds("written by the removed device"), "the unaware relay took the entry");
    let mut to_a = PeerClient::connect(&b.net.me, a.addr(), Some(&a.fp())).unwrap();
    netsync::sync_all(&b.node, &mut to_a).unwrap();
    a.net.sync_once(true).unwrap();
    assert_eq!(a.member(&c.fp()).unwrap()["status"], "revoked");
    assert!(!wa.holds("written by the removed device"), "a relayed entry of a removed device was taken by a device that holds the removal");
}

#[test]
fn a_cycle_dials_a_member_that_is_not_removed() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let (wa, wb) = (writer(&a, "a"), writer(&b, "b"));
    wb.say("written by a member");
    a.net.sync_once(true).unwrap();
    assert!(wa.holds("written by a member"));
}
