//! Who may change a pool: the three ways one plain member used to take the whole pool, against three real nodes.
//!
//! The members are joined by codes (`c` joins `a`, `b` joins `c`), so `b` is a member of `c` with no grant and no signature of
//! any kind, and `a` is the device that made the pool. Everything `b` sends goes over the member protocol, as a stolen or
//! malicious member would send it.

mod kit;

use kit::netkit::*;
use poolhouse_node::cert::{self, Identity};
use poolhouse_node::error::Error;
use poolhouse_node::fsutil::hex;
use poolhouse_node::membership::Policy;
use poolhouse_node::node::Node;
use poolhouse_node::peer::PeerClient;
use serde_json::{json, Value};

fn identity(seed: u8) -> Identity {
    let dir = tempfile::tempdir().unwrap();
    cert::load_or_create(dir.path(), &kit::key(seed)).unwrap()
}

/// `a` made the pool, `c` joined it, `b` joined `c`: `b` is a plain member of `c`, and `a` is the founder `c` knows.
fn pool_of_three() -> (Dev, Dev, Dev) {
    let (a, b, c) = (device(), device(), device());
    a.join_with_code(&c);
    c.join_with_code(&b);
    assert!(c.is_member(&a.fp()) && c.is_member(&b.fp()), "the setup is a pool in which c holds both");
    (a, b, c)
}

/// What `b` says to `c` in a `members` request; whether `c` answers or refuses does not matter, what `c` holds afterwards does.
fn members_request(b: &Dev, c: &Dev, rows: Vec<Value>, policy: Option<Value>) {
    let mut link = PeerClient::connect(&b.net.me, c.addr(), Some(&c.fp())).unwrap();
    let mut ask = json!({"op": "members", "pool": c.pool(), "rows": rows});
    if let Some(p) = policy {
        ask["policy"] = p;
    }
    let _ = link.call(&ask);
}

#[test]
fn a_plain_member_cannot_remove_the_founder_for_everyone() {
    let (a, b, c) = pool_of_three();
    let row = json!({"fingerprint": a.fp(), "cert": "", "name": "", "status": "revoked", "at": 1u64, "by": b.fp()});
    members_request(&b, &c, vec![row], None);
    assert!(c.is_member(&a.fp()), "the founder is still a member of c");
    let mut founder = PeerClient::connect(&a.net.me, c.addr(), Some(&c.fp())).expect("c still takes the founder's handshake");
    founder.call(&json!({"op": "boards"})).expect("and answers it");
}

#[test]
fn a_plain_member_cannot_enrol_a_certificate_no_join_approved() {
    let (_, b, c) = pool_of_three();
    let stranger = identity(31);
    let row = json!({"fingerprint": stranger.fingerprint(), "cert": hex(&stranger.der), "name": "stranger", "status": "active", "at": 1u64, "by": b.fp()});
    members_request(&b, &c, vec![row], None);
    assert!(c.member(&stranger.fingerprint()).is_none(), "c lists no device that no join approved");
    let reply = PeerClient::connect(&stranger, c.addr(), Some(&c.fp())).and_then(|mut l| l.call(&json!({"op": "boards"})));
    assert!(matches!(reply, Err(Error::Denied(_))), "the stranger is answered nothing: {reply:?}");
}

#[test]
fn a_plain_member_cannot_move_the_join_policy_or_freeze_it() {
    let (_, b, c) = pool_of_three();
    let frozen = json!({"policy": "open", "at": u64::MAX, "by": b.fp()});
    members_request(&b, &c, vec![], Some(frozen));
    assert_eq!(c.status()["policy"], "secure", "a member's word does not open the pool");
    c.set_policy("open");
    assert_eq!(c.status()["policy"], "open", "the pool's own local change still works, with no overflow");
    c.set_policy("secure");
    assert_eq!(c.status()["policy"], "secure");
}

#[test]
fn a_record_that_holds_the_newest_possible_policy_time_still_takes_a_local_change() {
    let dir = tempfile::tempdir().unwrap();
    drop(Node::open(dir.path()).unwrap());
    let path = dir.path().join("pool.json");
    let mut record: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    record["policy_at"] = json!(u64::MAX);
    record["policy_by"] = json!("a member");
    std::fs::write(&path, serde_json::to_vec(&record).unwrap()).unwrap();
    let mut node = Node::open(dir.path()).unwrap();
    assert!(node.change_policy(Policy::Open, "the owner").unwrap(), "the change is made, not frozen and not an overflow");
    assert_eq!(node.members.policy, Policy::Open);
    assert!(node.change_policy(Policy::Secure, "the owner").unwrap());
}

#[test]
fn a_policy_and_a_removal_do_not_spread_through_the_ordinary_exchange() {
    let (a, b, c) = pool_of_three();
    a.set_policy("open");
    c.ok("sync_now", "", &c.token, json!({}));
    b.ok("sync_now", "", &b.token, json!({}));
    assert_eq!((c.status()["policy"].clone(), b.status()["policy"].clone()), (json!("secure"), json!("secure")), "a's policy is a's");
    c.ok("member_remove", "", &c.token, json!({"fingerprint": a.fp()}));
    b.ok("sync_now", "", &b.token, json!({}));
    assert!(b.is_member(&a.fp()), "c's removal of a is c's: b's record is b's own");
}
