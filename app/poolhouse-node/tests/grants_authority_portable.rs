//! A row that names device `*`, built by hand and written on a trusted device, is a grant here only while a key this device knows as a phone's
//! signed it and the action may travel: an admin whose phone this device does not know, a key the charter does not list, an action that does not
//! travel and an action of this device alone each give nothing. Two real nodes of one pool over loopback TLS.
mod kit;

use std::sync::Arc;

use ed25519_dalek::{Signer, SigningKey};
use kit::govnode::{approved_row, found, link, ANN, BEN};
use kit::grants::{Person, Spec};
use kit::netkit::*;
use poolhouse_node::fsutil::hex;
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::grants::{check, Scope};
use poolhouse_node::identity::Holder;
use poolhouse_node::netsync::sync_all;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};

const BY_BEN: &str = "g-00000000000000f1";
const BY_ANN: &str = "g-00000000000000f2";
const BY_OUTSIDER: &str = "g-00000000000000f3";
const FILE_DELETE: &str = "g-00000000000000f4";
const LINK_READ: &str = "g-00000000000000f5";

struct Pair {
    here: Dev,
    there: Dev,
    actor: String,
    x: String,
    token: String,
}

fn person(seed: u8) -> Person {
    Person { key: SigningKey::from_bytes(&[seed; 32]) }
}

fn sync_both(a: &Dev, b: &Dev) {
    sync_all(&a.node, &mut link(a, b)).unwrap();
    sync_all(&b.node, &mut link(b, a)).unwrap();
}

/// A pool of ann and ben that `here` founded and `there` joined, with ben's phone key unknown on `here`, and `here` trusting `there`.
fn pair() -> Pair {
    let (here, there) = (device(), device());
    found(&here, Arc::new(RigSigner::new().with_key(ANN, "ann").with_key(BEN, "ben")), "secure");
    here.node.lock().unwrap().persons.remove(&person(BEN).fingerprint(), 1).unwrap();
    here.join_with_code(&there);
    let (x, token) = here.session("demo", "x");
    let (actor, _) = there.session("demo", "scribe");
    sync_both(&here, &there);
    here.ok("land_trust", "demo", &here.token, json!({"device": there.fp(), "trusted": true}));
    Pair { here, there, actor, x, token }
}

impl Pair {
    /// Append to the log of `there` the row of ``spec`` that ``who`` signed, and let `here` hear of it.
    fn plant(&self, who: &Person, gid: &str, spec: &Spec) {
        let body = approved_row(&self.there.fp(), "demo", gid, spec, &who.fingerprint(), &|bytes| hex(&who.key.sign(bytes).to_bytes()));
        self.there.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &self.actor, Value::Object(body)).unwrap();
        sync_both(&self.here, &self.there);
    }

    fn state(&self, gid: &str) -> (String, String) {
        let all = self.here.ok("grants", "demo", &self.token, json!({}));
        let found = all["grants"].as_array().unwrap().iter().find(|g| g["gid"] == gid).cloned().unwrap_or(json!({"status": "absent", "why": ""}));
        (found["status"].as_str().unwrap().to_string(), found["why"].as_str().unwrap().to_string())
    }

    fn holds(&self, action: &str) -> bool {
        check(&mut self.here.node.lock().unwrap(), &Holder { board: "demo".into(), name: self.x.clone() }, action, &Scope::any()).unwrap()
    }
}

#[test]
fn a_portable_row_of_an_admin_whose_phone_this_device_does_not_know_is_ignored_where_a_known_phone_counts() {
    let p = pair();
    let spec = Spec::session(&p.x, "sync_now").device("*");
    p.plant(&person(BEN), BY_BEN, &spec);
    let (status, why) = p.state(BY_BEN);
    assert_eq!(status, "ignored", "{why}");
    assert!(why.contains("does not know that key as a phone's"), "{why}");
    assert!(!p.holds("sync_now"));
    p.plant(&person(ANN), BY_ANN, &spec);
    assert_eq!(p.state(BY_ANN).0, "live", "the same row from a key this device knows as a phone's counts");
    assert!(p.holds("sync_now"));
}

#[test]
fn a_row_from_a_trusted_device_that_no_admin_here_signed_or_that_may_not_travel_gives_nothing() {
    let p = pair();
    p.plant(&person(0x55), BY_OUTSIDER, &Spec::session(&p.x, "sync_now").device("*"));
    let (status, why) = p.state(BY_OUTSIDER);
    assert_eq!(status, "ignored");
    assert!(why.contains("not an admin of this pool now"), "{why}");
    p.plant(&person(ANN), FILE_DELETE, &Spec::session(&p.x, "file_delete").device("*"));
    let (status, why) = p.state(FILE_DELETE);
    assert_eq!(status, "ignored");
    assert!(why.contains("the device it applies to"), "an action that does not travel is granted on the device it applies to: {why}");
    p.plant(&person(ANN), LINK_READ, &Spec::session(&p.x, "link_read").device("*"));
    assert_eq!(p.state(LINK_READ).0, "ignored", "a row for an action of one device counts from its own origin only");
    assert!(!p.holds("sync_now") && !p.holds("file_delete") && !p.holds("link_read"));
}
