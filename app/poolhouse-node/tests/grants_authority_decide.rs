//! A key that is no admin key of the pool decides nothing: for each kind of request (a grant, a hold, a phone to add, a phone to remove) and each
//! way of answering it that the request offers, the answer is refused and the request, the phones and the fatigue ledger are as they were.
mod kit;

use ed25519_dalek::Signer;
use kit::grants::{agent, now_ms};
use kit::phone::FakePhone;
use kit::pool::{found, Founded};
use poolhouse_node::approvals::{challenge, decide::view, summary};
use poolhouse_node::fsutil::hex;
use poolhouse_node::phone::{Phone, Push, ACTIVE};
use serde_json::json;

fn state(pool: &Founded, id: &str) -> String {
    pool.rig.node.lock().unwrap().approvals.get(id).unwrap().state.clone()
}

#[test]
fn a_key_that_is_no_admin_key_decides_no_kind_of_request_in_any_way_it_offers() {
    let pool = found();
    let outsider = agent();
    pool.rig.node.lock().unwrap().persons.add(&outsider.public(), "a laptop", now_ms()).unwrap();
    let grant = pool.request("sync_now", "*");
    let declined = pool.request("note_verify", "*");
    assert_eq!(kit::ok(pool.answer(&pool.phone, &declined, "deny"))["state"], "denied");
    let again = pool.request("note_verify", "*");
    let hold = {
        let mut node = pool.rig.node.lock().unwrap();
        summary::raise(&mut node, "session:demo/noisy", "suspended", 2).unwrap();
        node.approvals.all().iter().find(|a| a.kind == "hold" && a.pending()).expect("a hold waits for a person").id.clone()
    };
    let new_phone = FakePhone::new("new phone");
    let add = {
        let secret = pool.rig.offer();
        let reply = pool.rig.phone("POST", "add", "", pool.rig.add_body(&new_phone, &secret)).1;
        reply["result"]["approval"].as_str().expect("an unlisted phone waits for approval").to_string()
    };
    let old = FakePhone::new("old phone");
    let remove = {
        let mut node = pool.rig.node.lock().unwrap();
        let (now, push) = (now_ms(), Push { p256dh: String::new(), auth: String::new(), endpoint: String::new() });
        node.phones.add(Phone { id: "ph-old".into(), name: "old phone".into(), fingerprint: old.fingerprint(), key: old.public.clone(), token_sha256: String::new(),
                                state: ACTIVE.into(), push, added_ms: now, seen_ms: now }).unwrap();
        poolhouse_node::phone::enrol::ask_to_remove(&mut node, "A test", "", "ph-old").unwrap()
    };
    let ledger = || std::fs::read(pool.rig.dir.path().join("fatigue.json")).ok();
    let before = ledger();
    let table = [("a grant", &grant, vec!["approve", "deny", "block"]), ("a grant asked again after a decline", &again, vec!["approve_repeat", "deny", "block"]),
                 ("a hold", &hold, vec!["approve", "deny", "block"]),
                 ("a phone to add", &add, vec!["approve", "deny"]), ("a phone to remove", &remove, vec!["approve", "deny"])];
    for (what, id, decisions) in table {
        let seen = {
            let node = pool.rig.node.lock().unwrap();
            view(&node, node.approvals.get(id).unwrap())
        };
        for decision in decisions {
            let bytes = challenge::bytes(&seen["challenge"], decision, &outsider.fingerprint());
            let body = json!({"id": id, "decision": decision, "key": outsider.fingerprint(), "sig": hex(&outsider.key.sign(&bytes).to_bytes())});
            let refused = pool.rig.phone("POST", "decide", "", body).1;
            assert!(Founded::denial(&refused).contains("not an admin key of this pool"), "{what}, {decision}: {refused}");
            assert_eq!(state(&pool, id), "pending", "{what}, {decision}: the request still waits for an admin");
        }
    }
    assert_eq!(ledger(), before, "no refused answer was counted as a decline, a hold or a block");
    let node = pool.rig.node.lock().unwrap();
    assert_eq!(node.phones.get("ph-old").unwrap().state, "active", "the phone to remove is still there");
    assert!(node.phones.all().any(|p| p.fingerprint == new_phone.fingerprint() && p.state == "pending"), "the phone to add still waits");
}
