//! A device that has recorded a removal exchanges nothing more with the removed device: not on a connection it already holds, not in a
//! sync cycle that began before it heard, not from a reply that arrives after it recorded the removal.

mod kit;

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
    let mut live = PeerClient::connect(&a.net.me, b.addr(), Some(&b.fp())).unwrap();
    let mut reply = live.call(&json!({"op": "members", "pool": a.pool(), "rows": []})).unwrap();
    let stranger = outsider(7);
    reply["rows"].as_array_mut().unwrap().push(json!({"fingerprint": stranger.fingerprint(), "cert": hex(&stranger.der), "name": "stranger", "status": "active", "at": 1, "by": "b"}));
    remove(&a, &b);
    let got = netsync::take_members(&a.node, &b.fp(), &reply);
    assert!(matches!(got, Err(Error::Denied(_))), "{got:?}");
    assert_eq!(a.node.lock().unwrap().members.standing(&stranger.fingerprint()), Standing::Unknown, "a removed device enrolled a device through a reply that arrived after the removal");
}

#[test]
fn a_cycle_that_learns_of_a_removal_does_not_go_on_to_dial_the_removed_device() {
    let (a, b, c) = (device(), device(), device());
    a.join_with_code(&b);
    a.join_with_code(&c);
    // `a` dials its members in the order of their fingerprints: the lower one is dialed first and carries the removal of the other.
    let (revoker, victim) = if b.fp() < c.fp() { (&b, &c) } else { (&c, &b) };
    let (wa, wv) = (writer(&a, "a"), writer(victim, "victim"));
    remove(revoker, victim);
    wv.say("written by the removed device");
    a.net.sync_once(true).unwrap();
    assert_eq!(a.member(&victim.fp()).unwrap()["status"], "revoked", "the first dial of the cycle carried the removal");
    assert!(!wa.holds("written by the removed device"), "the cycle dialed the removed device after it had recorded the removal");
    assert_eq!(a.member(&victim.fp()).unwrap()["last_error"], "", "a removed device is not dialed, so no dial to it failed");
}

#[test]
fn a_member_removed_while_it_is_being_dialed_is_not_recorded_as_a_failed_dial() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let stalled = b.node.lock().unwrap();
    let net = a.net.clone();
    let dial = std::thread::spawn(move || net.sync_once(true).unwrap());
    std::thread::sleep(std::time::Duration::from_millis(500));
    remove(&a, &b);
    drop(stalled);
    assert_eq!(dial.join().unwrap(), 0, "a dial that ended on a removed member was counted as reached");
    assert_eq!(a.member(&b.fp()).unwrap()["last_error"], "", "the refusal to talk to a removed member was recorded against it");
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
