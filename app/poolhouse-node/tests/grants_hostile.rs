//! Hostile input to the grant surfaces: every malformed or forged request is refused, none leaves a grant, an enrolled
//! key or a use behind, and the node answers each one.

mod kit;

use kit::grants::{enrolled, person, Person, Spec, HOUR};
use kit::*;
use poolhouse_node::grants::{check, Scope};
use poolhouse_node::identity::Holder;
use poolhouse_node::node::Node;
use serde_json::{json, Map, Value};
use tempfile::tempdir;

const GID: &str = "g-00000000000000a1";

fn caller(n: &mut Node) -> impl FnMut(&str, &str, &str, Value) -> Value + '_ {
    move |m, b, t, p| req(n, m, b, t, p)
}

/// A grant a person signed for ``name``, as the body the node checks (with its `op` and `board`).
fn body(name: &str) -> Map<String, Value> {
    let mut b = person().issue_params("demo", GID, &Spec::session(name, "note_verify")).as_object().unwrap().clone();
    b.insert("op".into(), json!("issue"));
    b.insert("board".into(), json!("demo"));
    b
}

fn refused(reply: &Value) -> String {
    assert_eq!(reply["ok"], false, "{reply}");
    let code = reply["error"]["code"].as_str().unwrap().to_string();
    assert!(code == "invalid" || code == "denied", "{reply}");
    code
}

type Edit = fn(&mut Map<String, Value>);

fn nested(depth: usize) -> Value {
    (0..depth).fold(json!("x"), |inner, _| json!({"kind": "class", "family": inner}))
}

fn edits() -> Vec<(&'static str, Edit)> {
    vec![
        ("a gid a megabyte long", |b| { b.insert("gid".into(), json!(format!("g-{}", "a".repeat(1 << 20)))); }),
        ("a gid that is not hex", |b| { b.insert("gid".into(), json!("g-zzzzzzzzzzzzzzzz")); }),
        ("a grantee of an unknown kind", |b| { b.insert("to".into(), json!({"kind": "root"})); }),
        ("a grantee with a field of its own", |b| { b.insert("to".into(), json!({"kind": "session", "name": "claude-aaaaaa", "admin": true})); }),
        ("a grantee named in capitals", |b| { b.insert("to".into(), json!({"kind": "session", "name": "ROOT"})); }),
        ("a grantee nested forty deep", |b| { b.insert("to".into(), nested(40)); }),
        ("a grantee that is a list", |b| { b.insert("to".into(), json!(["claude-aaaaaa"])); }),
        ("an action outside the registry", |b| { b.insert("action".into(), json!("format_disk")); }),
        ("an action with a control character", |b| { b.insert("action".into(), json!("note_verify\n")); }),
        ("a floor", |b| { b.insert("action".into(), json!("release_main")); }),
        ("a scope with a field of its own", |b| { b.insert("scope".into(), json!({"board": "demo", "device": "*", "resource": "*", "all": true})); }),
        ("a scope of another board", |b| { b.insert("scope".into(), json!({"board": "other", "device": "*", "resource": "*"})); }),
        ("a device that is not a fingerprint", |b| { b.insert("scope".into(), json!({"board": "demo", "device": "abc", "resource": "*"})); }),
        ("a resource with a wildcard in the middle", |b| { b.insert("scope".into(), json!({"board": "demo", "device": "*", "resource": "a*b"})); }),
        ("a resource that climbs", |b| { b.insert("scope".into(), json!({"board": "demo", "device": "*", "resource": "feat/../x"})); }),
        ("a resource that is not ASCII", |b| { b.insert("scope".into(), json!({"board": "demo", "device": "*", "resource": "feat/\u{e9}"})); }),
        ("a resource with a space", |b| { b.insert("scope".into(), json!({"board": "demo", "device": "*", "resource": "a b"})); }),
        ("a resource a thousand long", |b| { b.insert("scope".into(), json!({"board": "demo", "device": "*", "resource": "x".repeat(1000)})); }),
        ("an end before the start", |b| { let at = b["at_ms"].as_u64().unwrap(); b.insert("until_ms".into(), json!(at - 1)); }),
        ("a span of more than a year", |b| { let at = b["at_ms"].as_u64().unwrap(); b.insert("until_ms".into(), json!(at + 400 * 24 * HOUR)); }),
        ("an end with a fraction", |b| { b.insert("until_ms".into(), json!(1.5e12)); }),
        ("an end that is text", |b| { b.insert("until_ms".into(), json!("tomorrow")); }),
        ("zero uses", |b| { b.insert("uses".into(), json!(0)); }),
        ("a trillion uses", |b| { b.insert("uses".into(), json!(1_000_000_000_000u64)); }),
        ("uses as text", |b| { b.insert("uses".into(), json!("2")); }),
        ("negative uses", |b| { b.insert("uses".into(), json!(-1)); }),
        ("a condition of its own", |b| { b.insert("conditions".into(), json!({"sudo": true})); }),
        ("conditions that are text", |b| { b.insert("conditions".into(), json!("review")); }),
        ("a person key that is short", |b| { b.insert("person".into(), json!("abc")); }),
        ("a time ten minutes old", |b| { let at = b["at_ms"].as_u64().unwrap(); b.insert("at_ms".into(), json!(at - 10 * 60_000)); b.insert("until_ms".into(), json!(at + HOUR)); }),
        ("a time ten minutes ahead", |b| { let at = b["at_ms"].as_u64().unwrap(); b.insert("at_ms".into(), json!(at + 10 * 60_000)); b.insert("until_ms".into(), json!(at + 2 * HOUR)); }),
    ]
}

#[test]
fn a_person_signed_grant_with_a_malformed_body_is_refused_and_leaves_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    enrolled(&mut caller(&mut n), &token);
    for (what, edit) in edits() {
        let mut b = body(&name);
        edit(&mut b);
        let reply = req(&mut n, "grant_issue", "demo", &token, person().sign_params(b));
        refused(&reply);
        assert!(!reply["error"]["message"].as_str().unwrap().is_empty(), "{what}");
    }
    let all = ok(req(&mut n, "grants", "demo", &token, json!({})));
    assert!(all["grants"].as_array().unwrap().is_empty() && all["ignored"].as_array().unwrap().is_empty(), "nothing was written: {all}");
    assert!(!check(&mut n, &Holder { board: "demo".into(), name }, "note_verify", &Scope::any()).unwrap());
}

#[test]
fn extra_params_and_borrowed_identities_are_refused_before_anything_is_checked() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    enrolled(&mut caller(&mut n), &token);
    for (key, value) in [("admin", json!(true)), ("actor", json!("root")), ("as", json!("person")), ("sender", json!("human")), ("parent", json!("x")), ("board", json!("other"))] {
        let mut params = person().issue_params("demo", GID, &Spec::session(&name, "note_verify"));
        params[key] = value;
        refused(&req(&mut n, "grant_issue", "demo", &token, params));
    }
    refused(&req(&mut n, "grant_issue", "demo", &token, json!(["not", "an", "object"])));
    refused(&req(&mut n, "grant_issue", "", &token, person().issue_params("demo", GID, &Spec::session(&name, "note_verify"))));
    refused(&req(&mut n, "grants", "demo", &token, json!({"all": true})));
    assert!(ok(req(&mut n, "grants", "demo", &token, json!({})))["grants"].as_array().unwrap().is_empty());
}

#[test]
fn a_revocation_or_an_enrolment_with_hostile_input_changes_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    enrolled(&mut caller(&mut n), &token);
    assert_eq!(ok(person().issue(&mut caller(&mut n), "demo", &token, GID, &Spec::session(&name, "note_verify")))["gid"], GID);
    let me = Holder { board: "demo".into(), name: name.clone() };

    assert_eq!(refused(&req(&mut n, "grant_revoke", "demo", &token, person().revoke_params("demo", "g-00000000000000ff", "x"))), "invalid", "no such grant");
    let mut revoke = person().revoke_params("demo", GID, "x");
    revoke["reason"] = json!("bad\nreason");
    refused(&req(&mut n, "grant_revoke", "demo", &token, revoke));
    let stranger = Person { key: ed25519_dalek::SigningKey::from_bytes(&[0x21; 32]) };
    assert_eq!(refused(&req(&mut n, "grant_revoke", "demo", &token, stranger.revoke_params("demo", GID, "x"))), "denied");
    let mut stale = person().revoke_params("demo", GID, "x");
    stale["at_ms"] = json!(1);
    refused(&req(&mut n, "grant_revoke", "demo", &token, stale));
    assert!(check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "the grant stands");

    let enrolment = stranger.enrolment("an agent's key", Some(&stranger));
    let small_order = "01".to_string() + &"00".repeat(31);
    for (key, value) in [("public", json!(small_order)), ("public", json!("zz")), ("about", json!("x".repeat(100))), ("about", json!("tab\there")),
                         ("at_ms", json!(5)), ("proof", json!("00".repeat(64))), ("by", json!("ab".repeat(32)))] {
        let mut p = enrolment.clone();
        p[key] = value;
        refused(&req(&mut n, "person_enroll", "", &token, p));
    }
    refused(&req(&mut n, "person_enroll", "", "", enrolment));
    assert_eq!(ok(req(&mut n, "person_keys", "", "", json!({})))["keys"].as_array().unwrap().len(), 1, "only the person's key is enrolled");
}

#[test]
fn a_grant_row_forged_on_the_log_is_ignored_and_the_use_of_it_is_refused() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    enrolled(&mut caller(&mut n), &token);
    let me = Holder { board: "demo".into(), name: name.clone() };
    let agent = Person { key: ed25519_dalek::SigningKey::from_bytes(&[0x13; 32]) };
    let mut forged = agent.issue_params("demo", GID, &Spec::session(&name, "land_control")).as_object().unwrap().clone();
    forged.insert("op".into(), json!("issue"));
    forged.insert("board".into(), json!("demo"));
    forged.insert("person".into(), json!(person().fingerprint()));
    let mut copied = person().issue_params("demo", "g-00000000000000a2", &Spec::session("claude-bbbbbb", "land_control")).as_object().unwrap().clone();
    copied.insert("op".into(), json!("issue"));
    copied.insert("board".into(), json!("demo"));
    copied.insert("to".into(), json!({"kind": "session", "name": name}));
    for row in [forged, copied] {
        n.host("demo").unwrap().board.append(poolhouse_node::row::Kind::Grant, &name, Value::Object(row), "").unwrap();
    }
    assert!(!check(&mut n, &me, "land_control", &Scope::any()).unwrap());
    let all = ok(req(&mut n, "grants", "demo", &token, json!({})));
    assert!(all["grants"].as_array().unwrap().iter().all(|g| g["status"] == "ignored"), "{all}");
    assert_eq!(all["grants"].as_array().unwrap().len(), 2);
}
