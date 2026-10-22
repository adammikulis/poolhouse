//! A phone added to a device in a pool is listed as an admin key of the person whose phone approves it: it accepts being listed when it is
//! added, the approving phone signs the listing with its approval, the node hands the act to the pool at that tap, and the new phone is an
//! admin when the hold has run.
mod kit;

use kit::phone::FakePhone;
use kit::pool::{found, Founded};
use kit::*;
use poolhouse_node::gov::act::Act;
use serde_json::{json, Value};

const HOUR: i64 = 3_600_000;

fn acts(pool: &Founded) -> usize {
    pool.rig.node.lock().unwrap().members.gov().unwrap().acts().len()
}

fn admin_adds(pool: &Founded) -> Vec<Act> {
    pool.rig.node.lock().unwrap().members.gov().unwrap().acts().values().filter(|a| a.kind == "admin_add").cloned().collect()
}

/// The `add` request of ``phone`` with a new code, as the reply of the endpoint.
fn add(pool: &Founded, phone: &FakePhone) -> Value {
    let secret = pool.rig.offer();
    pool.rig.phone("POST", "add", "", pool.rig.add_body(phone, &secret)).1
}

fn waiting(pool: &Founded, second: &mut FakePhone) -> String {
    let reply = add(pool, second);
    assert_eq!(reply["ok"], true, "{reply}");
    second.token = reply["result"]["token"].as_str().unwrap().into();
    second.id = reply["result"]["phone"].as_str().unwrap().into();
    reply["result"]["approval"].as_str().unwrap().to_string()
}

fn state(pool: &Founded, id: &str) -> String {
    pool.rig.node.lock().unwrap().approvals.get(id).unwrap().state.clone()
}

fn authority_sorted(pool: &Founded) -> Vec<String> {
    let mut keys = pool.authority();
    keys.sort();
    keys
}

#[test]
fn a_replacement_phone_is_listed_at_the_tap_and_approves_for_the_pool_when_the_hold_has_run() {
    let pool = found();
    let mut second = FakePhone::new("replacement");
    let id = waiting(&pool, &mut second);
    assert!(admin_adds(&pool).is_empty(), "nothing is handed to the pool before the tap");
    let before = acts(&pool);
    let done = ok(pool.answer(&pool.phone, &id, "approve"));
    assert_eq!(done["state"], "approved", "{done}");
    assert_eq!(acts(&pool), before + 1, "the node handed the act to the pool at the tap");
    let act = admin_adds(&pool).remove(0);
    assert_eq!(act.admin_keys(), vec![pool.phone.public.as_str()], "the approving phone signed it");
    assert_eq!(act.body["entry"]["key"], second.public);
    assert_eq!(act.body["entry"]["person"], "ann", "the person is the approving phone's, never the new phone's");
    assert!(pool.rig.node.lock().unwrap().gov_view()["pending"].is_object(), "the hold has begun");
    let new = pool.endpoint(&second, "status", json!({}));
    assert_eq!(new["result"]["state"], "active");
    assert_eq!(authority_sorted(&pool), vec![pool.phone.fingerprint()], "until the hold ends the new phone is an admin key by nothing");
    let asked = pool.request("sync_now", "*");
    let refused = pool.rig.phone("POST", "decide", "", second.decision(&pool.rig.get(&pool.phone, &asked), "approve")).1;
    assert!(Founded::denial(&refused).contains("not an admin key of this pool"), "{refused}");
    assert_eq!(state(&pool, &asked), "pending", "its refused decision moved nothing");
    pool.pass(25 * HOUR);
    let mut both = vec![pool.phone.fingerprint(), second.fingerprint()];
    both.sort();
    assert_eq!(authority_sorted(&pool), both, "the hold ran out");
    let approved = ok(pool.answer(&second, &asked, "approve"));
    assert_eq!(approved["state"], "approved");
    assert_eq!(pool.state(&pool.gids()[0]).0, "live", "the new phone's approval counts as the pool's grant");
}

#[test]
fn the_request_names_the_person_and_the_hold_and_the_new_phone_is_a_key_no_one_has_listed_yet() {
    let pool = found();
    let mut second = FakePhone::new("replacement");
    let id = waiting(&pool, &mut second);
    let view = pool.rig.get(&pool.phone, &id);
    let text = view["text"].as_str().unwrap();
    assert!(text.contains("listed as an admin key of ann") && text.contains("hold of 24 hours") && text.contains("You sign twice"), "{text}");
    assert_eq!(view["challenge"]["action"]["act"]["kind"], "admin_add", "the act the approving phone signs is inside the challenge it signs");
    assert_eq!(view["challenge"]["action"]["act"]["signatures"], json!([]));
    assert!(!pool.rig.shown(&id).is_empty(), "the number the new phone's agent screen shows ties the tap to the phone in hand");
    let wrong = {
        let mut body = pool.phone.decision(&view, "approve");
        body["pick"] = json!("0000");
        pool.rig.phone("POST", "decide", "", body).1
    };
    assert_eq!(wrong["ok"], false, "{wrong}");
    assert!(admin_adds(&pool).is_empty(), "a wrong number lists nothing");
}

#[test]
fn an_approval_without_the_right_second_signature_decides_nothing() {
    let pool = found();
    let mut second = FakePhone::new("replacement");
    let id = waiting(&pool, &mut second);
    let view = pool.rig.get(&pool.phone, &id);
    let act: Act = serde_json::from_value(view["challenge"]["action"]["act"].clone()).unwrap();
    let decide = |body: Value| pool.rig.phone("POST", "decide", "", body).1;
    let mut missing = pool.phone.decision(&view, "approve");
    missing.as_object_mut().unwrap().remove("act_sig");
    let mut other = pool.phone.decision(&view, "approve");
    other["act_sig"] = json!(pool.phone.sign_low(b"another act"));
    let mut high = pool.phone.decision(&view, "approve");
    high["act_sig"] = json!(pool.phone.sign_high(&act.sign_bytes()));
    let mut others = pool.phone.decision(&view, "approve");
    others["act_sig"] = json!(second.sign_low(&act.sign_bytes()));
    for (what, body) in [("missing", missing), ("over other bytes", other), ("not canonical", high), ("another key's", others)] {
        let refused = decide(body);
        assert!(Founded::denial(&refused).contains("second signature"), "{what}: {refused}");
        assert_eq!(state(&pool, &id), "pending", "{what}: the request waits");
    }
    assert!(admin_adds(&pool).is_empty() && pool.rig.node.lock().unwrap().phones.get(&second.id).unwrap().state == "pending");
    assert_eq!(ok(decide(pool.phone.decision(&view, "approve")))["state"], "approved");
}

#[test]
fn denying_a_phone_needs_no_second_signature_and_lists_nothing() {
    let pool = found();
    let mut second = FakePhone::new("replacement");
    let id = waiting(&pool, &mut second);
    let denied = ok(pool.answer(&pool.phone, &id, "deny"));
    assert_eq!(denied["state"], "denied");
    assert!(admin_adds(&pool).is_empty());
    assert_eq!(pool.rig.node.lock().unwrap().phones.get(&second.id).unwrap().state, "removed");
}

#[test]
fn a_phone_that_did_not_accept_the_terms_its_code_carries_is_not_added() {
    let pool = found();
    let phone = FakePhone::new("replacement");
    let secret = pool.rig.offer();
    let terms = {
        let node = pool.rig.node.lock().unwrap();
        poolhouse_node::phone::admit::terms(&node, node.offer.as_ref().unwrap().person()).unwrap()
    };
    assert_eq!((terms["person"].clone(), terms["since"].clone()), (json!("ann"), json!(2)));
    let base = phone.add_body(&pool.rig.device(), &secret);
    let mut older_app = base.clone();
    older_app.as_object_mut().unwrap().remove("acceptance");
    let mut someone_else = base.clone();
    someone_else["acceptance"] = json!(phone.accept(&json!({"pool": terms["pool"], "prev": terms["prev"], "since": 2, "person": "mallory"})));
    let mut stale = base.clone();
    stale["acceptance"] = json!(phone.accept(&json!({"pool": terms["pool"], "prev": "0".repeat(64), "since": 2, "person": "ann"})));
    let mut high = base.clone();
    let message = {
        let entry = poolhouse_node::gov::keys::AdminKey {
            key: phone.public.clone(), alg: poolhouse_node::gov::keys::Alg::P256, role: poolhouse_node::gov::keys::Role::Admin,
            strength: poolhouse_node::gov::keys::Strength::Strong, person: "ann".into(), since: 2, acceptance: String::new(), label: "replacement".into(),
        };
        entry.acceptance_message(terms["pool"].as_str().unwrap(), terms["prev"].as_str().unwrap())
    };
    high["acceptance"] = json!(phone.sign_high(&message));
    for (what, body, says) in [("an app without acceptance", older_app, "update the Poolhouse app"), ("another person", someone_else, "did not accept being listed"),
                               ("another charter", stale, "did not accept being listed"), ("a high-s signature", high, "did not accept being listed")] {
        let secret = pool.rig.offer();
        let mut body = body;
        body["secret"] = json!(secret);
        let refused = pool.rig.phone("POST", "add", "", body).1;
        assert!(Founded::denial(&refused).contains(says), "{what}: {refused}");
    }
    let node = pool.rig.node.lock().unwrap();
    assert!(node.phones.by_key(&phone.fingerprint()).is_none() && node.approvals.all().is_empty(), "nothing was kept for any of them");
}

#[test]
fn a_device_with_no_admin_phone_refuses_an_unlisted_phone_before_the_code_is_spent() {
    let pool = found().joined();
    let phone = FakePhone::new("replacement");
    let secret = pool.rig.offer();
    let refused = pool.rig.phone("POST", "add", "", pool.rig.add_body(&phone, &secret)).1;
    assert_eq!(Founded::denial(&refused), "An admin's phone adds this phone; none is connected here.");
    let listed = pool.rig.phone("POST", "add", "", pool.phone.add_body(&pool.rig.device(), &secret)).1;
    assert_eq!(listed["result"]["state"], "active", "the code was not spent by the refusal: {listed}");
}
