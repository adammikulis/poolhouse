//! Standing grants on a real node: nothing is allowed without one, only a person's signature makes one, and a grant
//! allows exactly what it names.

mod kit;

use ed25519_dalek::SigningKey;
use kit::grants::{enrolled, gid_of, person, Person, Spec};
use kit::*;
use poolhouse_node::error::Error;
use poolhouse_node::grants::spec::{ACTIONS, FLOORS};
use poolhouse_node::grants::{check, holds, Scope};
use poolhouse_node::identity::Holder;
use poolhouse_node::node::Node;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};
use tempfile::tempdir;

fn holder(board: &str, name: &str) -> Holder {
    Holder { board: board.into(), name: name.into() }
}

fn caller(n: &mut Node) -> impl FnMut(&str, &str, &str, Value) -> Value + '_ {
    move |m, b, t, p| req(n, m, b, t, p)
}

fn listed(n: &mut Node, token: &str) -> Value {
    ok(req(n, "grants", "demo", token, json!({})))
}

fn status_of(n: &mut Node, token: &str, gid: &str) -> String {
    let all = listed(n, token);
    all["grants"].as_array().unwrap().iter().find(|g| g["gid"] == gid).map_or("absent", |g| g["status"].as_str().unwrap()).to_string()
}

fn put(n: &mut Node, token: &str, gid: &str, spec: &Spec) -> Value {
    enrolled(&n.dir.clone(), &mut caller(n), token);
    person().issue(&mut caller(n), "demo", token, gid, spec).clone()
}

const GID: &str = "g-00000000000000a1";

#[test]
fn nothing_is_allowed_until_a_grant_names_it() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    let me = holder("demo", &name);
    for action in ACTIONS {
        assert!(!check(&mut n, &me, action, &Scope::any()).unwrap(), "{action} is refused by default");
        assert!(!holds(&mut n, &me, action, &Scope::any()).unwrap());
    }
    for (method, params) in [("pool_leave", json!({})), ("member_remove", json!({"fingerprint": "ab".repeat(32)})),
                             ("shard_consent", json!({"enabled": false}))] {
        assert_eq!(code(&req(&mut n, method, "", &token, params)), "denied", "{method}");
    }
    enrolled(&n.dir.clone(), &mut caller(&mut n), &token);
    for action in ACTIONS {
        assert!(!check(&mut n, &me, action, &Scope::any()).unwrap(), "{action}: enrolling a person key grants nothing");
    }
    assert!(listed(&mut n, &token)["grants"].as_array().unwrap().is_empty());
}

#[test]
fn an_agent_cannot_grant_itself_or_anyone_a_thing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    let agent = Person { key: SigningKey::from_bytes(&[0x13; 32]) };
    let want = Spec::session(&name, "land_control");
    let reply = agent.issue(&mut caller(&mut n), "demo", &token, GID, &want);
    assert_eq!(code(&reply), "denied", "no person key is enrolled: {reply}");

    enrolled(&n.dir.clone(), &mut caller(&mut n), &token);
    assert_eq!(code(&agent.issue(&mut caller(&mut n), "demo", &token, GID, &want)), "denied", "a key the person did not enrol signs nothing");
    let mut posing = agent.issue_params("demo", GID, &want);
    posing["person"] = json!(person().fingerprint());
    assert_eq!(code(&req(&mut n, "grant_issue", "demo", &token, posing)), "denied", "naming the person's key is not signing with it");
    let mut edited = person().issue_params("demo", GID, &Spec::session("someone-else", "land_control"));
    edited["to"] = json!({"kind": "session", "name": name});
    assert_eq!(code(&req(&mut n, "grant_issue", "demo", &token, edited)), "denied", "a grant altered after the person signed it is refused");
    let mut unsigned = person().issue_params("demo", GID, &want);
    unsigned.as_object_mut().unwrap().remove("sig");
    assert_eq!(code(&req(&mut n, "grant_issue", "demo", &token, unsigned)), "denied", "an unsigned grant is signed by nobody");

    let mut own_key = agent.enrolment("the agent's own key", None);
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, own_key.clone())), "denied", "an enrolled key approves each new one");
    own_key["by"] = json!(agent.fingerprint());
    own_key["approval"] = own_key["proof"].clone();
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, own_key)), "denied", "a key cannot approve itself");
    assert_eq!(code(&req(&mut n, "grant_revoke", "demo", &token, agent.revoke_params("demo", GID, "x"))), "denied");

    assert!(!check(&mut n, &holder("demo", &name), "land_control", &Scope::any()).unwrap());
    assert!(listed(&mut n, &token)["grants"].as_array().unwrap().is_empty());
    assert_eq!(ok(req(&mut n, "person_keys", "", "", json!({})))["keys"].as_array().unwrap().len(), 1);
}

fn subagent(n: &mut Node, parent_token: &str, id: &str) -> (String, String) {
    let r = ok(req(n, "register", "demo", parent_token, json!({"model": "", "harness": "claude-code", "session": id})));
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

#[test]
fn a_grant_to_a_session_reaches_its_subagents_only_when_it_says_so_and_no_other_session() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((lead, lead_token), (stranger, _)) = (session(&mut n, "demo", "lead"), session(&mut n, "demo", "stranger"));
    let (kid, kid_token) = subagent(&mut n, &lead_token, "kid");
    let (grandkid, _) = subagent(&mut n, &kid_token, "grandkid");
    let (_, other_token) = session(&mut n, "demo", "other-lead");
    let (not_kin, _) = subagent(&mut n, &other_token, "not-kin");
    let alone = Spec::to(json!({"kind": "session", "name": lead}), "note_verify");
    ok(put(&mut n, &lead_token, GID, &alone));
    for (name, held) in [(&lead, true), (&kid, false), (&grandkid, false), (&stranger, false), (&not_kin, false)] {
        assert_eq!(holds(&mut n, &holder("demo", name), "note_verify", &Scope::any()).unwrap(), held, "{name} under a grant to {lead} alone");
    }
    let with_kin = Spec::to(json!({"kind": "session", "name": lead, "subagents": true}), "land_control");
    ok(put(&mut n, &lead_token, "g-00000000000000a9", &with_kin));
    for (name, held) in [(&lead, true), (&kid, true), (&grandkid, true), (&stranger, false), (&not_kin, false)] {
        assert_eq!(holds(&mut n, &holder("demo", name), "land_control", &Scope::any()).unwrap(), held, "{name} under a grant to {lead} and its subagents");
    }
    let listed = listed(&mut n, &kid_token);
    let mine: Vec<&Value> = listed["grants"].as_array().unwrap().iter().filter(|g| g["mine"] == true).collect();
    assert_eq!(mine.len(), 1, "the subagent sees the grant that names its parent and its subagents as its own: {listed}");
    let mut text = person().sign_params({
        let mut b = person().issue_params("demo", "g-00000000000000ab", &with_kin).as_object().unwrap().clone();
        b.insert("op".into(), json!("issue"));
        b.insert("board".into(), json!("demo"));
        b.insert("to".into(), json!({"kind": "session", "name": lead, "subagents": "yes"}));
        b
    });
    text["to"] = json!({"kind": "session", "name": lead, "subagents": "yes"});
    assert_eq!(code(&req(&mut n, "grant_issue", "demo", &lead_token, text)), "invalid", "subagents is true or false");
}

#[test]
fn a_use_count_on_an_action_another_device_may_use_names_the_one_device() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, at) = session(&mut n, "demo", "a");
    let here = n.cert.fingerprint();
    for action in ["task_authority", "note_verify", "sync_now", "shard_call"] {
        let anywhere = put(&mut n, &at, "g-00000000000000b9", &Spec::session(&a, action).uses(3));
        assert_eq!(code(&anywhere), "invalid", "{action} for any device with a use count");
        ok(put(&mut n, &at, &format!("g-00000000000000c{}", action.len()), &Spec::session(&a, action).uses(3).device(&here)));
        ok(put(&mut n, &at, &format!("g-00000000000000d{}", action.len()), &Spec::session(&a, action)));
    }
    ok(put(&mut n, &at, "g-00000000000000b8", &Spec::session(&a, "pool_leave").uses(3)));
}

#[test]
fn a_session_of_another_board_or_without_a_token_carries_no_grant() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, at) = session(&mut n, "demo", "a");
    let (_, other) = session(&mut n, "other", "b");
    enrolled(&n.dir.clone(), &mut caller(&mut n), &at);
    let params = person().issue_params("demo", GID, &Spec::session("x", "note_verify"));
    assert_eq!(code(&req(&mut n, "grant_issue", "demo", "", params.clone())), "denied");
    assert_eq!(code(&req(&mut n, "grant_issue", "demo", &other, params.clone())), "denied", "a session of another board does not carry this board's grants");
    assert_eq!(ok(req(&mut n, "grant_issue", "demo", &at, params))["gid"], GID);
}

#[test]
fn a_person_grant_allows_exactly_its_scope() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, at) = session(&mut n, "demo", "a");
    let (b, _) = session(&mut n, "demo", "b");
    ok(put(&mut n, &at, GID, &Spec::session(&a, "land_control").on("feat/*")));
    let (me, other) = (holder("demo", &a), holder("demo", &b));
    assert!(check(&mut n, &me, "land_control", &Scope::on("feat/x")).unwrap());
    assert!(!check(&mut n, &me, "land_control", &Scope::on("dev")).unwrap(), "another resource");
    assert!(!check(&mut n, &me, "land_control", &Scope::on("feat/../dev")).unwrap(), "a path that climbs out of the pattern");
    assert!(!check(&mut n, &me, "land_control", &Scope::any()).unwrap(), "no resource named is not every resource");
    assert!(!check(&mut n, &me, "land_trust", &Scope::on("feat/x")).unwrap(), "another action");
    assert!(!check(&mut n, &other, "land_control", &Scope::on("feat/x")).unwrap(), "another session");
    assert!(!check(&mut n, &holder("other", &a), "land_control", &Scope::on("feat/x")).unwrap(), "the same name on another board");

    let mut here = Spec::session(&a, "task_authority");
    here.device = "cd".repeat(32);
    ok(put(&mut n, &at, "g-00000000000000a2", &here));
    assert!(!check(&mut n, &me, "task_authority", &Scope::any()).unwrap(), "a grant for another device does nothing here");
    here.device = n.cert.fingerprint();
    ok(put(&mut n, &at, "g-00000000000000a3", &here));
    assert!(check(&mut n, &me, "task_authority", &Scope::any()).unwrap(), "a grant for this device");
}

#[test]
fn a_grant_ends_by_uses_by_time_and_by_revocation_and_each_use_is_on_the_board() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, at) = session(&mut n, "demo", "a");
    let me = holder("demo", &a);
    let here = n.cert.fingerprint();
    ok(put(&mut n, &at, GID, &Spec::session(&a, "note_verify").uses(2).device(&here)));
    assert!(check(&mut n, &me, "note_verify", &Scope::on("note-1")).unwrap());
    assert!(holds(&mut n, &me, "note_verify", &Scope::on("note-1")).unwrap(), "asking spends nothing");
    assert!(check(&mut n, &me, "note_verify", &Scope::on("note-2")).unwrap());
    assert!(!check(&mut n, &me, "note_verify", &Scope::on("note-3")).unwrap(), "two uses were granted");
    assert_eq!(status_of(&mut n, &at, GID), "exhausted");
    let log = ok(req(&mut n, "grant_log", "demo", &at, json!({})))["log"].clone();
    let uses: Vec<&Value> = log.as_array().unwrap().iter().filter(|e| e["op"] == "use").collect();
    assert_eq!(uses.len(), 2, "{log}");
    assert!(uses.iter().all(|e| e["gid"] == GID && e["user"] == json!(a) && e["action"] == "note_verify"), "{log}");
    let mut targets: Vec<&str> = uses.iter().map(|e| e["target"].as_str().unwrap()).collect();
    targets.sort_unstable();
    assert_eq!(targets, ["note-1", "note-2"]);

    ok(put(&mut n, &at, "g-00000000000000b1", &Spec::session(&a, "land_control").window(-200_000, -100_000)));
    assert_eq!(status_of(&mut n, &at, "g-00000000000000b1"), "expired");
    assert!(!check(&mut n, &me, "land_control", &Scope::any()).unwrap());
    ok(put(&mut n, &at, "g-00000000000000b2", &Spec::session(&a, "land_control").window(200_000, 3_600_000)));
    assert_eq!(status_of(&mut n, &at, "g-00000000000000b2"), "not-yet");
    assert!(!check(&mut n, &me, "land_control", &Scope::any()).unwrap(), "not before it starts");

    ok(put(&mut n, &at, "g-00000000000000b3", &Spec::session(&a, "land_control")));
    assert!(check(&mut n, &me, "land_control", &Scope::any()).unwrap());
    ok(person().revoke(&mut caller(&mut n), "demo", &at, "g-00000000000000b3"));
    assert_eq!(status_of(&mut n, &at, "g-00000000000000b3"), "revoked");
    assert!(!check(&mut n, &me, "land_control", &Scope::any()).unwrap(), "revoked");
    let again = person().issue(&mut caller(&mut n), "demo", &at, "g-00000000000000b3", &Spec::session(&a, "land_control"));
    assert_eq!(code(&again), "invalid", "a revoked grant id is not issued again");
}

#[test]
fn a_grant_that_is_short_lived_lapses_by_the_clock() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, at) = session(&mut n, "demo", "a");
    ok(put(&mut n, &at, GID, &Spec::session(&a, "sync_now").window(-1000, 700)));
    let me = holder("demo", &a);
    assert!(holds(&mut n, &me, "sync_now", &Scope::any()).unwrap());
    std::thread::sleep(std::time::Duration::from_millis(900));
    assert!(!check(&mut n, &me, "sync_now", &Scope::any()).unwrap(), "it ended while the node ran");
    assert_eq!(status_of(&mut n, &at, GID), "expired");
}

#[test]
fn grants_name_a_session_a_class_or_a_verified_account() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (lead, lt) = session(&mut n, "demo", "lead");
    let made = ok(req(&mut n, "register", "demo", &lt, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": "kid"})));
    let kid = made["name"].as_str().unwrap().to_string();
    let (outsider, _) = session(&mut n, "demo", "x");
    ok(put(&mut n, &lt, "g-00000000000000c1", &Spec::to(json!({"kind": "class", "family": "claude"}), "land_control")));
    ok(put(&mut n, &lt, "g-00000000000000c2", &Spec::to(json!({"kind": "class", "family": "claude", "main": false}), "note_verify")));
    ok(put(&mut n, &lt, "g-00000000000000c3", &Spec::to(json!({"kind": "class", "family": "qwen"}), "sync_now")));
    ok(put(&mut n, &lt, "g-00000000000000c4", &Spec::to(json!({"kind": "class", "family": "claude", "verified": true}), "file_delete")));
    ok(put(&mut n, &lt, "g-00000000000000c5", &Spec::to(json!({"kind": "account", "model": "claude-sonnet-5-5"}), "task_authority")));
    let (l, k) = (holder("demo", &lead), holder("demo", &kid));
    assert!(check(&mut n, &l, "land_control", &Scope::any()).unwrap(), "a class names its main sessions");
    assert!(!check(&mut n, &k, "land_control", &Scope::any()).unwrap(), "and not their subagents");
    assert!(check(&mut n, &k, "note_verify", &Scope::any()).unwrap(), "unless it says so");
    assert!(!check(&mut n, &l, "sync_now", &Scope::any()).unwrap(), "another family");
    assert!(!check(&mut n, &l, "file_delete", &Scope::any()).unwrap(), "a claimed model is not a verified one");
    assert!(!check(&mut n, &l, "task_authority", &Scope::any()).unwrap(), "an account is named by a verified model");
    n.host("demo").unwrap().names.verify_model(&lead, "claude-sonnet-5-5").unwrap();
    assert!(check(&mut n, &l, "file_delete", &Scope::any()).unwrap(), "once the node verified it");
    assert!(check(&mut n, &l, "task_authority", &Scope::any()).unwrap());
    assert!(check(&mut n, &holder("demo", &outsider), "land_control", &Scope::any()).unwrap(), "any main session of the family is named, whoever started it");
    let made = ok(req(&mut n, "register", "demo", "", json!({"model": "qwen3", "harness": "pi", "session": "q"})));
    assert!(!check(&mut n, &holder("demo", made["name"].as_str().unwrap()), "land_control", &Scope::any()).unwrap(), "a session of another family is not");
}

#[test]
fn a_grant_that_needs_review_is_used_only_with_one() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, at) = session(&mut n, "demo", "a");
    let mut spec = Spec::session(&a, "land_control");
    spec.review = true;
    ok(put(&mut n, &at, GID, &spec));
    let me = holder("demo", &a);
    assert!(!check(&mut n, &me, "land_control", &Scope::on("dev")).unwrap(), "no review stands behind this use");
    assert!(check(&mut n, &me, "land_control", &Scope { resource: "dev", reviewed: true }).unwrap());
}

#[test]
fn the_floors_are_never_granted_even_by_a_person_entry() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, at) = session(&mut n, "demo", "a");
    enrolled(&n.dir.clone(), &mut caller(&mut n), &at);
    let me = holder("demo", &a);
    for (i, floor) in FLOORS.iter().enumerate() {
        let gid = format!("g-0000000000000f{i:02x}");
        let reply = person().issue(&mut caller(&mut n), "demo", &at, &gid, &Spec::session(&a, floor));
        assert_eq!(code(&reply), "denied", "{floor}: {reply}");
        assert!(reply["error"]["message"].as_str().unwrap().contains("floor"), "{reply}");
        assert!(matches!(check(&mut n, &me, floor, &Scope::any()), Err(Error::Denied(_))), "{floor} is refused by name");
        let mut body = person().issue_params("demo", &gid, &Spec::session(&a, "land_control")).as_object().unwrap().clone();
        body.insert("action".into(), json!(floor));
        body.insert("op".into(), json!("issue"));
        body.insert("board".into(), json!("demo"));
        let mut row = person().sign_params(body).as_object().unwrap().clone();
        row.insert("op".into(), json!("issue"));
        row.insert("board".into(), json!("demo"));
        n.host("demo").unwrap().board.append(Kind::Grant, &a, Value::Object(row), "").unwrap();
    }
    let all = listed(&mut n, &at);
    assert!(all["grants"].as_array().unwrap().is_empty(), "a floor entry on the board is no grant: {all}");
    assert_eq!(all["ignored"].as_array().unwrap().len(), FLOORS.len());
    assert!(all["ignored"][0]["why"].as_str().unwrap().contains("floor"), "{all}");
    let unknown = person().issue(&mut caller(&mut n), "demo", &at, GID, &Spec::session(&a, "rm_rf_everything"));
    assert_eq!(code(&unknown), "invalid", "an action outside the registry is no action");
}

#[test]
fn grants_survive_a_restart_and_depend_on_the_enrolled_key() {
    let dir = tempdir().unwrap();
    let (a, at) = {
        let mut n = node(dir.path());
        let (a, at) = session(&mut n, "demo", "a");
        let here = n.cert.fingerprint();
        ok(put(&mut n, &at, GID, &Spec::session(&a, "note_verify").uses(2).device(&here)));
        assert!(check(&mut n, &holder("demo", &a), "note_verify", &Scope::any()).unwrap());
        (a, at)
    };
    {
        let mut n = node(dir.path());
        let me = holder("demo", &a);
        assert_eq!(status_of(&mut n, &at, GID), "live", "one use of two is spent and the grant is back after a restart");
        assert!(check(&mut n, &me, "note_verify", &Scope::any()).unwrap());
        assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "the spent uses are remembered");
    }
    {
        let mut n = node(dir.path());
        ok(put(&mut n, &at, "g-00000000000000d1", &Spec::session(&a, "land_control")));
        assert!(check(&mut n, &holder("demo", &a), "land_control", &Scope::any()).unwrap());
    }
    std::fs::write(dir.path().join("persons.json"), b"{}").unwrap();
    let mut n = node(dir.path());
    assert!(!check(&mut n, &holder("demo", &a), "land_control", &Scope::any()).unwrap(), "with its key gone from this node, a person's grant counts for nothing");
    assert_eq!(status_of(&mut n, &at, "g-00000000000000d1"), "ignored");
}

#[test]
fn the_default_grants_helper_gives_one_session_its_actions_and_no_other() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (a, at) = session(&mut n, "demo", "a");
    let (b, _) = session(&mut n, "demo", "b");
    allow(&mut n, "demo", &at, &a, &["land_control", "note_verify"]);
    assert!(check(&mut n, &holder("demo", &a), "land_control", &Scope::any()).unwrap());
    assert!(!check(&mut n, &holder("demo", &a), "land_trust", &Scope::any()).unwrap());
    assert!(!check(&mut n, &holder("demo", &b), "land_control", &Scope::any()).unwrap());
    assert_eq!(status_of(&mut n, &at, &gid_of(&a, "land_control")), "live");
}
