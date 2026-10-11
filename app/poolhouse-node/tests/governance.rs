//! Who may change a pool: the three ways one plain member used to take the whole pool, against three real nodes.
//!
//! The members are joined by codes from the pool of `a` (`pool_of_three`), so `b` is a member of `c` with no grant and no signature of
//! any kind, and `a` is the device that made the pool. Everything `b` sends goes over the member protocol, as a stolen or
//! malicious member would send it.

mod kit;

use kit::netkit::*;
use poolhouse_node::cert::{self, Identity};
use poolhouse_node::error::Error;
use poolhouse_node::fsutil::hex;
use kit::govnode::pool_of_three;
use poolhouse_node::peer::PeerClient;
use serde_json::{json, Value};

fn identity(seed: u8) -> Identity {
    let dir = tempfile::tempdir().unwrap();
    cert::load_or_create(dir.path(), &kit::key(seed)).unwrap()
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
    assert_eq!(c.status()["governance"]["mode"], "governed");
}
