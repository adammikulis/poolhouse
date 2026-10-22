//! A distrust row cuts an admin key off from granting and deciding at once, on every board, in either order of arrival; it only narrows.
mod kit;

use kit::grants::{agent, now_ms, Spec};
use kit::phone::FakePhone;
use kit::pool::{found, Founded};
use kit::*;
use poolhouse_node::grants::spec::signing_bytes;
use poolhouse_node::row::Kind;
use serde_json::{json, Map, Value};

/// The epoch the pool lists ``fingerprint`` at, and the pool's id.
fn since_of(pool: &Founded, fingerprint: &str) -> u64 {
    let node = pool.rig.node.lock().unwrap();
    node.members.gov().unwrap().state().chain.latest().charter.admins.iter().find(|k| k.fingerprint() == fingerprint).map_or(0, |k| k.since)
}

fn pool_id(pool: &Founded) -> String {
    pool.rig.node.lock().unwrap().members.gov().unwrap().id.clone()
}

/// The body of a distrust of ``subject`` at ``since`` in the pool ``named``, signed by ``person``, without its signature.
fn body(subject: &str, since: u64, named: &str, person: &str) -> Map<String, Value> {
    let mut body = Map::new();
    for (k, v) in [("op", json!("distrust")), ("board", json!("pool")), ("key", json!(subject)), ("since", json!(since)), ("pool", json!(named)), ("person", json!(person)),
                   ("at_ms", json!(now_ms())), ("reason", json!("its phone was lost"))] {
        body.insert(k.into(), v);
    }
    body
}

/// The `grant_distrust` params of ``body`` signed by a P-256 phone.
fn by_phone(phone: &FakePhone, body: Map<String, Value>) -> Value {
    let sig = phone.sign(&signing_bytes(&body));
    let mut params = body;
    params.remove("op");
    params.remove("board");
    params.insert("sig".into(), json!(sig));
    Value::Object(params)
}

/// Distrust ``subject`` with the typed-yes key the pool lists as a weak admin key, through the call; the reply.
fn distrust(pool: &Founded, subject: &str) -> Value {
    let params = pool.typed.sign_params(body(subject, since_of(pool, subject), &pool_id(pool), &pool.typed.fingerprint()));
    pool.rig.call("grant_distrust", "", &pool.token, params)
}

/// Append a row of ``body`` with the signature ``sig`` to the pool board, as a writer with access to the log could.
fn plant(pool: &Founded, mut body: Map<String, Value>, sig: String) -> poolhouse_node::error::Result<()> {
    body.insert("sig".into(), json!(sig));
    pool.rig.node.lock().unwrap().host("pool").unwrap().board.append_person(Kind::Grant, "node", Value::Object(body)).map(drop)
}

#[test]
fn another_admins_distrust_cuts_a_compromised_admin_off_on_every_board_and_its_revokes_still_count() {
    let pool = found();
    let (_, other_token) = pool.rig.session("other", "lead2");
    let made = ok(pool.rig.ask("other", &other_token, json!([kit::phone::ask("sync_now", "*")]), json!({})));
    ok(pool.answer(&pool.phone, made["id"].as_str().unwrap(), "approve"));
    pool.approve_with(&pool.phone, "sync_now", "*");
    let waiting = pool.request("task_authority", "*");
    let seen = pool.rig.get(&pool.phone, &waiting);
    let listing = |token: &str, board: &str| ok(pool.rig.call("grants", board, token, json!({})));
    let gids: Vec<String> = [("demo", pool.token.clone()), ("other", other_token.clone())].iter().map(|(b, t)| listing(t, b)["grants"][0]["gid"].as_str().unwrap().to_string()).collect();
    assert!(gids.len() == 2 && listing(&pool.token, "demo")["grants"].as_array().unwrap().iter().any(|g| g["status"] == "live"));
    let reply = distrust(&pool, &pool.phone.fingerprint());
    assert_eq!(reply["ok"], true, "{reply}");
    for (board, token) in [("demo", pool.token.as_str()), ("other", other_token.as_str())] {
        let now = listing(token, board);
        let ignored: Vec<&Value> = now["grants"].as_array().unwrap().iter().filter(|g| g["why"].as_str().unwrap().contains("distrusted")).collect();
        assert_eq!(ignored.len(), 1, "{board}: {now}");
        assert_eq!((now["authority"].clone(), now["distrusted"].clone()), (json!([]), json!([pool.phone.fingerprint()])));
    }
    let refused = pool.rig.phone("POST", "decide", "", pool.phone.decision(&seen, "approve")).1;
    assert!(Founded::denial(&refused).contains("not an admin key of this pool"), "{refused}");
    let again = pool.rig.ask("demo", &pool.token, json!([kit::phone::ask("note_verify", "*")]), json!({}));
    assert!(Founded::denial(&again).contains("No admin phone"), "{again}");
    let mut revoke = Map::new();
    for (k, v) in [("op", json!("revoke")), ("board", json!("demo")), ("gid", json!(gids[0])), ("at_ms", json!(now_ms())), ("reason", json!("ended")), ("person", json!(pool.phone.fingerprint()))] {
        revoke.insert(k.into(), v);
    }
    let ended = pool.rig.call("grant_revoke", "demo", &pool.token, by_phone(&pool.phone, revoke));
    assert_eq!(ended["ok"], true, "a distrusted key still ends a grant: {ended}");
    assert_eq!(pool.state(&gids[0]).0, "revoked");
}

#[test]
fn a_distrust_and_the_grant_it_cuts_have_the_same_result_in_either_order() {
    let after = found();
    after.approve_with(&after.phone, "sync_now", "*");
    ok(distrust(&after, &after.phone.fingerprint()));
    let gid = after.gids().remove(0);
    assert!(after.state(&gid).1.contains("distrusted"), "{:?}", after.state(&gid));
    let before = found();
    ok(distrust(&before, &before.phone.fingerprint()));
    before.append_approved("g-00000000000000f3", &Spec::session(&before.name, "sync_now"), &before.phone);
    assert!(before.state("g-00000000000000f3").1.contains("distrusted"), "{:?}", before.state("g-00000000000000f3"));
    assert_eq!(ok(distrust(&before, &before.phone.fingerprint()))["entry"], "", "the same distrust again writes nothing");
}

#[test]
fn a_distrust_that_should_not_count_changes_nothing_and_no_row_ends_one() {
    let pool = found();
    let (phone, typed, outsider) = (pool.phone.fingerprint(), pool.typed.fingerprint(), agent());
    pool.rig.node.lock().unwrap().persons.add(&outsider.public(), "a laptop", now_ms()).unwrap();
    let since = since_of(&pool, &phone);
    let sign_with = |who: &kit::grants::Person, b: &Map<String, Value>| {
        use ed25519_dalek::Signer;
        poolhouse_node::fsutil::hex(&who.key.sign(&signing_bytes(b)).to_bytes())
    };
    let cases = [
        ("a signer the pool does not list", body(&phone, since, &pool_id(&pool), &outsider.fingerprint()), &outsider),
        ("a stale epoch", body(&phone, since + 1, &pool_id(&pool), &typed), &pool.typed),
        ("another pool", body(&phone, since, "another-pool", &typed), &pool.typed),
        ("a key the pool does not list", body(&outsider.fingerprint(), 1, &pool_id(&pool), &typed), &pool.typed),
    ];
    for (what, row, signer) in cases {
        let sig = sign_with(signer, &row);
        plant(&pool, row, sig).unwrap();
        assert_eq!(pool.authority(), vec![phone.clone()], "{what}");
        assert_eq!(pool.grants()["distrusted"], json!([]), "{what}: the listing names no distrusted key");
    }
    let by_outsider = pool.rig.call("grant_distrust", "", &pool.token, outsider.sign_params(body(&phone, since, &pool_id(&pool), &outsider.fingerprint())));
    assert!(Founded::denial(&by_outsider).contains("counts for nothing"), "{by_outsider}");
    ok(pool.act("admin_remove", json!({"key": pool.typed.public()})));
    pool.pass(48 * 3_600_000);
    let removed_signer = body(&phone, since, &pool_id(&pool), &typed);
    let sig = sign_with(&pool.typed, &removed_signer);
    plant(&pool, removed_signer, sig).unwrap();
    assert_eq!(pool.authority(), vec![phone.clone()], "a removed admin's cut does not keep it");
    let own = body(&phone, since, &pool_id(&pool), &phone);
    assert_eq!(pool.rig.call("grant_distrust", "", &pool.token, by_phone(&pool.phone, own))["ok"], true);
    assert!(pool.authority().is_empty(), "an admin key may cut off its own key");
    let mut undo = body(&phone, since, &pool_id(&pool), &phone);
    undo.insert("op".into(), json!("undistrust"));
    let _ = plant(&pool, undo, pool.phone.sign(b"undistrust"));
    assert!(pool.authority().is_empty(), "there is no row that ends a distrust");
}

#[test]
fn a_distrust_row_at_or_before_the_floor_cuts_nothing_off() {
    let pool = found();
    ok(distrust(&pool, &pool.phone.fingerprint()));
    assert!(pool.authority().is_empty(), "while no floor stands the distrust cuts the key off");
    let pool = pool.restarted(|root| std::fs::write(root.join("grants_floor.json"), json!({"before_ms": now_ms() + 3_600_000}).to_string()).unwrap());
    assert_eq!(pool.authority(), vec![pool.phone.fingerprint()], "a row at or before the floor counts for nothing");
    assert_eq!(pool.grants()["distrusted"], json!([]));
}

#[test]
fn two_admins_that_distrust_each_other_leave_no_authority_key_and_no_grant() {
    let pool = found();
    let mut second = FakePhone::new("second phone");
    pool.rig.add(&mut second, Some(&pool.phone));
    pool.add_admin(&second);
    pool.approve_with(&second, "sync_now", "*");
    let (one, two) = (pool.phone.fingerprint(), second.fingerprint());
    for (signer, subject) in [(&pool.phone, &two), (&second, &one)] {
        let params = by_phone(signer, body(subject, since_of(&pool, subject), &pool_id(&pool), &signer.fingerprint()));
        assert_eq!(pool.rig.call("grant_distrust", "", &pool.token, params)["ok"], true);
    }
    assert!(pool.authority().is_empty());
    assert!(pool.state(&pool.gids().remove(0)).1.contains("distrusted"));
}

#[test]
fn a_listed_phone_that_is_distrusted_waits_for_approval_where_a_trusted_one_does_not() {
    let mut pool = found().joined();
    ok(distrust(&pool, &pool.phone.fingerprint()));
    let secret = pool.rig.offer();
    let (_, reply) = pool.rig.phone("POST", "add", "", pool.phone.add_body(&pool.rig.device(), &secret));
    assert_eq!(reply["result"]["state"], "pending", "{reply}");
    assert!(pool.authority().is_empty());
    pool.phone.token = reply["result"]["token"].as_str().unwrap().into();
}

#[test]
fn a_distrusted_phone_is_told_and_shown_nothing() {
    let pool = found();
    let mut second = FakePhone::new("second phone");
    pool.rig.add(&mut second, Some(&pool.phone));
    pool.add_admin(&second);
    pool.rig.push_to(&pool.phone, "https://push.example/first");
    pool.rig.push_to(&second, "https://push.example/second");
    ok(distrust(&pool, &second.fingerprint()));
    let id = pool.request("sync_now", "*");
    assert_eq!(pool.pushed_to(), vec!["https://push.example/first".to_string()]);
    assert_eq!(pool.endpoint(&second, "list", json!({}))["result"]["approvals"], json!([]));
    assert_eq!(pool.endpoint(&second, "get", json!({"id": id}))["ok"], false);
}
