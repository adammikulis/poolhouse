//! Which phones a device in a pool takes, tells and lets read: the phone of a listed admin key joins with its code and its key's proof, any other
//! phone waits for an admin phone, only an admin phone is told or shown a request, and an admin key leaves only through an admin removal.
mod kit;

use kit::grants::{agent, fresh_nonce, my_anchor, now_ms};
use kit::phone::FakePhone;
use kit::pool::{found, Founded};
use kit::*;
use poolhouse_node::approvals::challenge;
use poolhouse_node::grants::authority::Authority;
use serde_json::json;

const LISTED_KEY: &str = "leaves through an admin removal";

fn authority(pool: &Founded) -> Vec<String> {
    Authority::of(&pool.rig.node.lock().unwrap()).authority()
}

#[test]
fn a_device_that_holds_only_a_typed_yes_key_gets_the_listed_phone_with_one_scan_and_then_its_approval_counts() {
    let mut pool = found().joined();
    assert!(authority(&pool).is_empty(), "the device knows no admin phone yet");
    let secret = pool.rig.offer();
    let (status, reply) = pool.rig.phone("POST", "add", "", pool.phone.add_body(&pool.rig.device(), &secret));
    assert_eq!((status, reply["ok"].clone(), reply["result"]["state"].clone()), (200, json!(true), json!("active")), "{reply}");
    assert_eq!(reply["result"]["approval"], "", "no approval was asked for: the charter already lists this key");
    pool.phone.token = reply["result"]["token"].as_str().unwrap().into();
    assert_eq!(authority(&pool), vec![pool.phone.fingerprint()]);
    let done = pool.approve_with(&pool.phone, "sync_now", "*");
    assert_eq!(done["state"], "approved", "{done}");
    assert_eq!(pool.state(&pool.gids()[0]).0, "live", "the approval of the phone that scanned counts as a pool's grant");
}

#[test]
fn a_phone_that_only_names_a_listed_key_without_its_proof_is_not_added() {
    let pool = found().joined();
    let other = FakePhone::new("other");
    let mut body = other.add_body(&pool.rig.device(), &pool.rig.offer());
    body["key"] = json!(pool.phone.public);
    let (_, reply) = pool.rig.phone("POST", "add", "", body);
    assert_eq!(Founded::denial(&reply), "That phone did not prove it holds its key, so it was not added.");
    assert!(authority(&pool).is_empty() && pool.rig.node.lock().unwrap().phones.all().next().is_none());
}

#[test]
fn a_phone_the_charter_does_not_list_waits_for_an_admin_phone_where_the_listed_one_does_not() {
    let pool = found();
    let other = FakePhone::new("other");
    let secret = pool.rig.offer();
    let (_, reply) = pool.rig.phone("POST", "add", "", pool.rig.add_body(&other, &secret));
    assert_eq!((reply["result"]["state"].clone(), reply["result"]["approval"].as_str().is_some_and(|a| !a.is_empty())), (json!("pending"), true), "{reply}");
    let waiting = pool.rig.phone("POST", "list", reply["result"]["token"].as_str().unwrap(), json!({})).1;
    assert!(Founded::denial(&waiting).contains("waiting for a phone you already added"), "{waiting}");
    assert_eq!(authority(&pool), vec![pool.phone.fingerprint()], "the waiting phone is no authority key");
}

#[test]
fn an_agent_on_a_device_with_no_admin_phone_is_refused_at_the_request_and_nothing_is_pushed() {
    let pool = found().joined();
    for _ in 0..8 {
        let asked = pool.rig.ask("demo", &pool.token, json!([kit::phone::ask("sync_now", "*")]), json!({}));
        assert!(Founded::denial(&asked).contains("No admin phone of this pool is added to this device"), "{asked}");
    }
    let node = pool.rig.node.lock().unwrap();
    assert!(node.approvals.all().is_empty(), "no request exists for a tap to be wasted on");
    assert!(pool.rig.pushed.sent().is_empty());
}

#[test]
fn a_phone_that_is_no_authority_key_is_told_and_shown_nothing() {
    let pool = found();
    let mut second = FakePhone::new("second phone");
    pool.rig.add(&mut second, Some(&pool.phone));
    pool.rig.push_to(&pool.phone, "https://push.example/admin");
    pool.rig.push_to(&second, "https://push.example/second");
    let id = pool.request("sync_now", "*");
    assert_eq!(pool.pushed_to(), vec!["https://push.example/admin".to_string()], "only the admin phone is told");
    let waiting = |phone: &FakePhone| pool.endpoint(phone, "list", json!({}))["result"]["approvals"].as_array().unwrap().iter().filter(|a| a["state"] == "pending").count();
    assert_eq!(waiting(&pool.phone), 1);
    assert_eq!(waiting(&second), 0);
    for op in ["list", "wait"] {
        assert_eq!(pool.endpoint(&second, op, json!({"after": 0}))["result"]["approvals"], json!([]), "{op}: not even a request that ended");
    }
    assert_eq!(pool.endpoint(&second, "get", json!({"id": id}))["ok"], false);
    assert_eq!(pool.endpoint(&pool.phone, "get", json!({"id": id}))["ok"], true);
    let (device, at) = (pool.rig.device(), now_ms());
    let read = |key: &str, sig: String| pool.rig.call("approval_pending", "", &pool.token, json!({"key": key, "sig": sig, "at_ms": at}));
    let typed = hex_sign(&pool, &challenge::list_bytes(&device, at));
    let by_typed = read(&pool.typed.fingerprint(), typed);
    assert!(Founded::denial(&by_typed).contains("not an admin key of this pool"), "{by_typed}");
    let by_phone = read(&pool.phone.fingerprint(), pool.phone.sign(&challenge::list_bytes(&device, at)));
    assert_eq!(ok(by_phone)["approvals"].as_array().unwrap().len(), 1);
}

fn hex_sign(pool: &Founded, bytes: &[u8]) -> String {
    use ed25519_dalek::Signer;
    poolhouse_node::fsutil::hex(&pool.typed.key.sign(bytes).to_bytes())
}

#[test]
fn a_key_the_charter_lists_is_not_unenrolled_and_a_refused_request_spends_no_nonce() {
    let pool = found();
    let (device, phone) = (pool.rig.device(), pool.phone.fingerprint());
    let nonce = fresh_nonce();
    let refused = pool.rig.call("person_unenroll", "", &pool.token, pool.typed.unenroll_params_with(&phone, &device, now_ms(), &nonce, &my_anchor()));
    assert!(Founded::denial(&refused).contains(LISTED_KEY), "{refused}");
    let own = pool.rig.call("person_unenroll", "", &pool.token, pool.typed.unenroll_params(&pool.typed.fingerprint(), &device));
    assert!(Founded::denial(&own).contains(LISTED_KEY), "a weak listed key leaves through an admin removal too: {own}");
    {
        let mut node = pool.rig.node.lock().unwrap();
        assert!(node.persons.get(&phone).is_some() && !node.persons.was_removed(&phone));
        assert!(node.persons.once(&nonce, now_ms()).unwrap(), "the refused request spent no nonce");
    }
    let outsider = agent();
    pool.rig.node.lock().unwrap().persons.add(&outsider.public(), "a laptop", now_ms()).unwrap();
    ok(pool.rig.call("person_unenroll", "", &pool.token, pool.typed.unenroll_params(&outsider.fingerprint(), &device)));
}

#[test]
fn a_phone_whose_key_the_charter_lists_is_not_removed_by_a_request_or_an_approval() {
    let pool = found();
    let asked = pool.rig.call("phone_remove", "", &pool.token, json!({"phone": pool.phone.id}));
    assert!(Founded::denial(&asked).contains(LISTED_KEY), "{asked}");
    let through = pool.endpoint(&pool.phone, "remove", json!({"id": pool.phone.id}));
    assert!(Founded::denial(&through).contains(LISTED_KEY), "{through}");
    let mut node = pool.rig.node.lock().unwrap();
    let mut approved = poolhouse_node::approvals::Approval::empty();
    approved.action = json!({"kind": "phone_remove", "phone": pool.phone.id, "fingerprint": pool.phone.fingerprint()});
    assert!(matches!(poolhouse_node::phone::remove(&mut node, &approved), Err(poolhouse_node::error::Error::Denied(_))));
    assert!(node.persons.get(&pool.phone.fingerprint()).is_some());
    assert!(node.approvals.all().is_empty(), "no request to remove it was ever made");
}
