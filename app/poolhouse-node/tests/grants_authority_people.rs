//! A device that holds the admin keys of three people: a code that names no one person adds no phone, a phone of another person cannot approve a
//! phone that is listed for someone else, and a listing that needs a second person's signature waits for `pool_cosign` and starts its hold then.
mod kit;

use kit::gov::{person, public};
use kit::grants::{now_ms, person as typed_person, Spec};
use kit::phone::{secret_of, FakePhone, Rig};
use kit::pool::{Founded, FOUNDING};
use kit::*;
use poolhouse_node::fsutil::unhex;
use poolhouse_node::gov::act::Act;
use poolhouse_node::gov::signer::RigSigner;
use serde_json::json;

const CY: u8 = 3;
const HOUR: i64 = 3_600_000;
const LINK: &str = "poolhouse://add-phone?data=";

/// A pool of ann's phone, ben's phone and cy's key, three people with a strong admin key each, on a device whose signer holds cy's key; the
/// pool, with ann's phone as the `Founded` one, and ben's phone.
fn found_three() -> (Founded, FakePhone) {
    let rig = Rig::new();
    let mut ann = FakePhone::new("ann phone");
    rig.add(&mut ann, None);
    let mut ben = FakePhone::new("ben phone");
    rig.add(&mut ben, Some(&ann));
    let typed = typed_person();
    {
        let mut node = rig.node.lock().unwrap();
        node.persons.add(&typed.public(), "typed yes", now_ms()).unwrap();
        node.persons.add_phone_key(poolhouse_node::grants::person::ED25519, &public(CY), "cy's key", now_ms()).unwrap();
        node.set_signer(person(CY, "cy"));
    }
    let (name, token) = rig.session("demo", "lead");
    let keys = json!([{"key": ann.public, "person": "ann", "label": "ann"}, {"key": ben.public, "person": "ben", "label": "ben"}, {"key": public(CY), "person": "cy", "label": "cy"}]);
    let draft = ok(rig.call("pool_draft_found", "", &token, json!({"title": "home", "policy": "secure", "project": "", "keys": keys})));
    let mut charter = draft["charter"].clone();
    for (i, entry) in draft["accept"].as_array().unwrap().iter().enumerate() {
        let (bytes, key) = (unhex(entry["sign_bytes"].as_str().unwrap()).unwrap(), entry["key"].as_str().unwrap());
        charter["admins"][i]["acceptance"] = json!(if key == ann.public { ann.sign_low(&bytes) } else if key == ben.public { ben.sign_low(&bytes) } else { RigSigner::sign_as(CY, &bytes) });
    }
    ok(rig.call("grant_issue", "demo", &token, typed.issue_params("demo", FOUNDING, &Spec::session(&name, "pool_create").on("home"))));
    ok(rig.call("pool_found", "", &token, json!({"charter": charter})));
    rig.node.lock().unwrap().fatigue.rules.backoff_ms = 0;
    (Founded { rig, phone: ann, typed, name, token }, ben)
}

fn admin_adds(pool: &Founded) -> Vec<Act> {
    pool.rig.node.lock().unwrap().members.gov().unwrap().acts().values().filter(|a| a.kind == "admin_add").cloned().collect()
}

fn state(pool: &Founded, id: &str) -> String {
    pool.rig.node.lock().unwrap().approvals.get(id).unwrap().state.clone()
}

/// A new phone added with a code that ann's phone made, and the id of the request that waits for a phone of ann's to approve it.
fn ann_adds_a_phone(pool: &Founded) -> (FakePhone, String) {
    let offer = pool.endpoint(&pool.phone, "offer", json!({}));
    let secret = secret_of(offer["result"]["link"].as_str().expect("ann's phone made a code").strip_prefix(LINK).unwrap());
    let mut dan = FakePhone::new("dan phone");
    let added = pool.rig.phone("POST", "add", "", pool.rig.add_body(&dan, &secret)).1;
    assert_eq!((added["ok"].clone(), added["result"]["state"].clone()), (json!(true), json!("pending")), "{added}");
    dan.token = added["result"]["token"].as_str().unwrap().into();
    dan.id = added["result"]["phone"].as_str().unwrap().into();
    (dan, added["result"]["approval"].as_str().unwrap().to_string())
}

#[test]
fn a_code_that_names_no_one_person_adds_no_phone_where_a_code_from_one_persons_phone_does() {
    let (pool, _ben) = found_three();
    let dan = FakePhone::new("dan phone");
    let asked = pool.rig.node.lock().unwrap().approvals.all().len();
    let secret = pool.rig.offer();
    let refused = pool.rig.phone("POST", "add", "", pool.rig.add_body(&dan, &secret)).1;
    assert!(Founded::denial(&refused).contains("More than one person's admin phone is added here"), "{refused}");
    {
        let node = pool.rig.node.lock().unwrap();
        assert!(node.phones.by_key(&dan.fingerprint()).is_none() && node.approvals.all().len() == asked, "nothing was kept for the refused phone");
    }
    assert!(admin_adds(&pool).is_empty(), "nothing was handed to the pool");
    let (_, id) = ann_adds_a_phone(&pool);
    let card = pool.rig.get(&pool.phone, &id)["text"].as_str().unwrap().to_string();
    assert!(card.contains("listed as an admin key of ann"), "{card}");
}

#[test]
fn a_phone_of_another_person_cannot_approve_a_phone_that_is_listed_for_someone_else() {
    let (pool, ben) = found_three();
    let (dan, id) = ann_adds_a_phone(&pool);
    let view = pool.rig.get(&ben, &id);
    let refused = pool.rig.phone("POST", "decide", "", ben.decision(&view, "approve")).1;
    assert!(Founded::denial(&refused).contains("Only an admin phone of ann approves a phone that is listed for ann"), "{refused}");
    assert_eq!(state(&pool, &id), "pending", "its refused decision moved nothing");
    assert!(admin_adds(&pool).is_empty(), "no listing was handed to the pool");
    assert_eq!(pool.rig.node.lock().unwrap().phones.get(&dan.id).unwrap().state, "pending");
    assert_eq!(ok(pool.answer(&pool.phone, &id, "approve"))["state"], "approved", "the phone of the person it is listed for approves it");
}

#[test]
fn a_listing_that_needs_a_second_person_waits_for_pool_cosign_and_its_hold_starts_then() {
    let (pool, _ben) = found_three();
    let (dan, id) = ann_adds_a_phone(&pool);
    assert_eq!(ok(pool.answer(&pool.phone, &id, "approve"))["state"], "approved");
    let act = admin_adds(&pool).remove(0);
    assert_eq!(act.admin_keys(), vec![pool.phone.public.as_str()], "ann's tap signed it, and a listing needs two of the three people");
    let waiting = pool.rig.node.lock().unwrap().gov_view();
    assert!(waiting["pending"].is_null(), "no hold has begun: {waiting}");
    assert_eq!((waiting["waiting_for_signers"][0]["act"].clone(), waiting["waiting_for_signers"][0]["have"].clone(), waiting["waiting_for_signers"][0]["need"].clone()),
               (json!(act.id()), json!(1), json!(2)), "{waiting}");
    pool.pass(50 * HOUR);
    assert!(!pool.authority().contains(&dan.fingerprint()), "a listing short of a signer takes effect after no time");
    pool.approve_with(&pool.phone, "admin_manage", "cosign");
    let cosigned = ok(pool.rig.call("pool_cosign", "", &pool.token, json!({"act": act.id()})));
    assert_eq!(cosigned["status"], "pending", "{cosigned}");
    assert!(!pool.authority().contains(&dan.fingerprint()), "the hold has only begun");
    pool.pass(25 * HOUR);
    assert!(pool.authority().contains(&dan.fingerprint()), "the hold ran out");
    let seen = pool.rig.node.lock().unwrap().gov_view();
    assert!(seen["waiting_for_signers"].as_array().unwrap().is_empty() && seen["pending"].is_null(), "{seen}");
}
