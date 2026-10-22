//! Who may grant in a governed pool, on a node that a phone's key founded: the phone's approval is the grant, the typed-yes key listed as a weak
//! admin may only end one, and no other key counts for an action that reaches beyond the device.
mod kit;

use ed25519_dalek::Signer;
use kit::grants::{agent, person, Person, Spec};
use kit::phone::{ask, FakePhone, Rig};
use kit::*;
use poolhouse_node::approvals::challenge;
use poolhouse_node::fsutil::{hex, sha256_hex, unhex};
use poolhouse_node::grants::spec::{pool_wide, signing_bytes, ACTIONS, DEVICE_LOCAL};
use poolhouse_node::grants::Scope;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};

const FOUNDING: &str = "g-00000000000000a0";

struct Pool {
    rig: Rig,
    phone: FakePhone,
    name: String,
    token: String,
}

impl Pool {
    /// A node founded by a phone's strong key; the typed-yes key `person()` is listed beside it as a weak admin.
    fn new() -> Pool {
        let rig = Rig::new();
        let mut phone = FakePhone::new("admin phone");
        rig.add(&mut phone, None);
        let typed = person();
        rig.node.lock().unwrap().persons.add(&typed.public(), "typed yes", kit::grants::now_ms()).unwrap();
        let (name, token) = rig.session("demo", "lead");
        let keys = json!([{"key": phone.public, "person": "ann", "label": "phone"}, {"key": typed.public(), "person": "ann", "label": "typed"}]);
        let draft = ok(rig.call("pool_draft_found", "", &token, json!({"title": "home", "policy": "secure", "project": "", "keys": keys})));
        let mut charter = draft["charter"].clone();
        for (i, entry) in draft["accept"].as_array().unwrap().iter().enumerate() {
            let bytes = unhex(entry["sign_bytes"].as_str().unwrap()).unwrap();
            charter["admins"][i]["acceptance"] = json!(if entry["key"] == json!(phone.public) { phone.sign_low(&bytes) } else { hex(&typed.key.sign(&bytes).to_bytes()) });
        }
        ok(rig.call("grant_issue", "demo", &token, typed.issue_params("demo", FOUNDING, &Spec::session(&name, "pool_create").on("home"))));
        ok(rig.call("pool_found", "", &token, json!({"charter": charter})));
        rig.node.lock().unwrap().fatigue.rules.backoff_ms = 0;
        Pool { rig, phone, name, token }
    }

    fn device(&self) -> String {
        self.rig.device()
    }

    /// A file on the board and its handle.
    fn file(&self) -> (String, std::path::PathBuf, Vec<u8>) {
        let bytes = b"shared file contents".to_vec();
        let blob = ok(self.rig.call("file_put", "demo", &self.token, json!({"upload":"upload0001", "offset":0, "total":bytes.len(), "data":hex(&bytes)})))["blob"].as_str().unwrap().to_string();
        let handle = blob[..12].to_string();
        ok(self.rig.call("post", "demo", &self.token, json!({"kind":"message", "fields":{"type":"file", "to":"#general", "body":"file: fixture.txt", "file":{
            "id":handle, "blob":blob, "sha":blob, "name":"fixture.txt", "size":bytes.len(), "type":"text", "text":true}}})));
        let path = self.rig.node.lock().unwrap().dir.join("boards/demo/files/blobs").join(format!("{blob}.bin"));
        (handle, path, bytes)
    }

    /// The phone's answer ``decision`` to the request ``id``, as the endpoint gives it.
    fn answer(&self, id: &str, decision: &str) -> Value {
        self.rig.phone("POST", "decide", "", self.phone.decision(&self.rig.get(&self.phone, id), decision)).1
    }

    /// Ask for ``action`` on ``resource`` and have the phone approve it.
    fn approve(&self, action: &str, resource: &str) -> Value {
        let made = ok(self.rig.ask("demo", &self.token, json!([ask(action, resource)]), json!({})));
        ok(self.answer(made["id"].as_str().unwrap(), "approve"))
    }

    fn grants(&self) -> Value {
        ok(self.rig.call("grants", "demo", &self.token, json!({})))
    }

    /// The status and the reason of the grant ``gid``, or `absent`.
    fn state(&self, gid: &str) -> (String, String) {
        let all = self.grants();
        let found = all["grants"].as_array().unwrap().iter().find(|g| g["gid"] == gid).cloned().unwrap_or(json!({"status": "absent", "why": ""}));
        (found["status"].as_str().unwrap().into(), found["why"].as_str().unwrap().into())
    }

    /// The gid of the one grant on the board besides the one that founded the pool.
    fn only_gid(&self) -> String {
        let all = self.grants();
        let mine: Vec<&Value> = all["grants"].as_array().unwrap().iter().filter(|g| g["gid"] != FOUNDING).collect();
        assert_eq!(mine.len(), 1, "{all}");
        mine[0]["gid"].as_str().unwrap().to_string()
    }

    /// The gid of the one grant of ``action`` on the board besides the one that founded the pool.
    fn only_gid_of(&self, action: &str) -> String {
        let all = self.grants();
        let mine: Vec<&Value> = all["grants"].as_array().unwrap().iter().filter(|g| g["action"] == action && g["gid"] != FOUNDING).collect();
        assert_eq!(mine.len(), 1, "{all}");
        mine[0]["gid"].as_str().unwrap().to_string()
    }

    /// The sentence of the refusal ``reply``, after checking it is a denial.
    fn denial(reply: &Value) -> String {
        assert_eq!(code(reply), "denied", "{reply}");
        reply["error"]["message"].as_str().unwrap().to_string()
    }

    fn delete(&self, handle: &str) -> Value {
        self.rig.call("file_delete", "demo", &self.token, json!({"handle": handle}))
    }

    /// Append an approved issue row of ``spec``, signed by ``sign`` for the key ``named``.
    fn append(&self, gid: &str, spec: &Spec, named: &str, sign: &dyn Fn(&[u8]) -> String) {
        let body = kit::govnode::approved_row(&self.device(), "demo", gid, spec, named, sign);
        self.rig.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &self.name, Value::Object(body)).unwrap();
    }

    fn enrol_typed(&self, who: &Person) {
        self.rig.node.lock().unwrap().persons.add(&who.public(), "another typed key", kit::grants::now_ms()).unwrap();
    }

    /// Append the revoke ``params`` as a row, as a writer with access to the log could.
    fn append_revoke(&self, mut params: Value) {
        params["op"] = json!("revoke");
        params["board"] = json!("demo");
        self.rig.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &self.name, params).unwrap();
    }

    /// Whether the session holds a grant of ``action`` now.
    fn holds(&self, action: &str) -> bool {
        let mut node = self.rig.node.lock().unwrap();
        let holder = node.tokens.resolve(&self.token).cloned().unwrap();
        poolhouse_node::grants::holds(&mut node, &holder, action, &Scope::any()).unwrap()
    }
}

fn typed_sign(who: &Person) -> impl Fn(&[u8]) -> String + '_ {
    move |bytes| hex(&who.key.sign(bytes).to_bytes())
}

#[test]
fn the_founding_phone_approves_a_file_delete_and_the_delete_works_once() {
    let pool = Pool::new();
    let (handle, path, _) = pool.file();
    let done = pool.approve("file_delete", &handle);
    assert_eq!(done["state"], "approved", "{done}");
    let gid = pool.only_gid();
    assert_eq!(pool.state(&gid).0, "live");
    let row = {
        let mut node = pool.rig.node.lock().unwrap();
        let board = &node.host("demo").unwrap().board;
        let own = board.origin().to_string();
        board.rows(&own).iter().rev().find(|r| r.kind == Kind::Grant).unwrap().body.clone()
    };
    assert_eq!(row["approval"]["decision"], "approve", "the row names the decision the phone signed: {row}");
    assert_eq!(row["person"], pool.phone.fingerprint().as_str(), "{row}");
    assert_eq!(pool.grants()["authority"], json!([pool.phone.fingerprint()]), "the founding phone is the one authority key of the pool");
    assert_eq!(pool.delete(&handle)["ok"], true);
    assert!(!path.exists(), "the delete the phone approved ran");
    let log = ok(pool.rig.call("grant_log", "demo", &pool.token, json!({"limit": 20})));
    assert!(log["log"].as_array().unwrap().iter().any(|e| e["op"] == "use" && e["action"] == "file_delete"), "{log}");
    assert_eq!(pool.state(&gid).0, "exhausted", "it was asked for once");
}

#[test]
fn a_scope_declined_before_is_approved_with_approve_repeat_and_that_grant_counts() {
    let pool = Pool::new();
    let first = ok(pool.rig.ask("demo", &pool.token, json!([ask("add_device", "*")]), json!({})));
    ok(pool.answer(first["id"].as_str().unwrap(), "deny"));
    let again = ok(pool.rig.ask("demo", &pool.token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!(ok(pool.answer(again["id"].as_str().unwrap(), "approve_repeat"))["state"], "approved");
    let gid = pool.only_gid();
    assert_eq!(pool.state(&gid).0, "live", "a grant the phone gave with approve_repeat is read back under the same bytes");
    let written = {
        let mut node = pool.rig.node.lock().unwrap();
        let board = &node.host("demo").unwrap().board;
        let own = board.origin().to_string();
        board.rows(&own).iter().rev().find(|r| r.kind == Kind::Grant).unwrap().body.clone()
    };
    let mut claims_approve = written.as_object().unwrap().clone();
    claims_approve["approval"]["decision"] = json!("approve");
    assert!(poolhouse_node::grants::spec::parse_issue(&claims_approve).is_err(), "a row that claims approve over an approve_repeat signature is no grant");
    let mut claims_repeat = written.as_object().unwrap().clone();
    claims_repeat["approval"]["context"]["repeat"] = json!(0);
    assert!(poolhouse_node::grants::spec::parse_issue(&claims_repeat).is_err(), "nor is approve_repeat with no earlier decline");
}

#[test]
fn a_high_s_approval_counts_because_the_phone_never_normalised_it() {
    let pool = Pool::new();
    let made = ok(pool.rig.ask("demo", &pool.token, json!([ask("sync_now", "*")]), json!({})));
    let view = pool.rig.get(&pool.phone, made["id"].as_str().unwrap());
    let fingerprint = pool.phone.fingerprint();
    let sig = pool.phone.sign_high(&challenge::bytes(&view["challenge"], "approve", &fingerprint));
    assert!(!poolhouse_node::gov::keys::Alg::P256.sig_shape(&sig), "the signature is the upper-half form");
    let body = json!({"id": view["id"], "decision": "approve", "key": fingerprint, "sig": sig, "text_sha256": sha256_hex(view["text"].as_str().unwrap().as_bytes())});
    assert_eq!(pool.rig.phone("POST", "decide", "", body).1["ok"], true);
    assert_eq!(pool.state(&pool.only_gid()).0, "live");
}

#[test]
fn a_weak_listed_key_ends_a_pool_wide_grant_and_the_end_outlasts_the_key_in_persons_json() {
    let pool = Pool::new();
    pool.approve("sync_now", "*");
    let gid = pool.only_gid();
    let typed = person();
    let revoked = pool.rig.call("grant_revoke", "demo", &pool.token, typed.revoke_params("demo", &gid, "ended"));
    assert_eq!(revoked["ok"], true, "{revoked}");
    assert_eq!(pool.state(&gid).0, "revoked");
    pool.rig.node.lock().unwrap().persons.remove(&typed.fingerprint(), kit::grants::now_ms()).unwrap();
    assert_eq!(pool.state(&gid).0, "revoked", "removing the key from persons.json brings nothing back");
}

#[test]
fn a_key_that_is_no_admin_neither_revokes_a_pool_wide_grant_nor_has_a_revoke_row_counted() {
    let pool = Pool::new();
    pool.approve("sync_now", "*");
    let gid = pool.only_gid();
    let outsider = agent();
    pool.enrol_typed(&outsider);
    let refused = pool.rig.call("grant_revoke", "demo", &pool.token, outsider.revoke_params("demo", &gid, "x"));
    assert_eq!(code(&refused), "denied", "{refused}");
    let mut row = outsider.revoke_params("demo", &gid, "x");
    row["op"] = json!("revoke");
    row["board"] = json!("demo");
    pool.rig.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &pool.name, row).unwrap();
    assert_eq!(pool.state(&gid).0, "live", "a revoke row a non-admin signed is ignored");
}

#[test]
fn a_device_local_grant_by_an_enrolled_key_counts_in_a_pool_and_a_pool_wide_one_does_not() {
    let pool = Pool::new();
    let typed = person();
    let local = pool.rig.call("grant_issue", "demo", &pool.token, typed.issue_params("demo", "g-00000000000000b1", &Spec::session(&pool.name, "link_read")));
    assert_eq!(local["ok"], true, "{local}");
    assert_eq!(pool.state("g-00000000000000b1").0, "live");
    let wide = pool.rig.call("grant_issue", "demo", &pool.token, typed.issue_params("demo", "g-00000000000000b2", &Spec::session(&pool.name, "sync_now")));
    assert_eq!(code(&wide), "denied", "{wide}");
    assert!(wide["error"]["message"].as_str().unwrap().contains("your phone gives standing grants"), "{wide}");
}

#[test]
fn a_device_in_no_pool_counts_any_enrolled_key() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = kit::node(dir.path());
    let (name, token) = session(&mut node, "demo", "lead");
    kit::grants::enrolled(dir.path(), &mut |m, b, t, p| req(&mut node, m, b, t, p), &token);
    let issued = req(&mut node, "grant_issue", "demo", &token, person().issue_params("demo", "g-00000000000000c1", &Spec::session(&name, "file_delete")));
    assert_eq!(issued["ok"], true, "{issued}");
    let listing = ok(req(&mut node, "grants", "demo", &token, json!({})));
    assert_eq!((listing["governance"].clone(), listing["grants"][0]["status"].clone()), (json!("unfounded"), json!("live")), "{listing}");
}

#[test]
fn a_decision_by_a_key_that_is_no_admin_changes_nothing() {
    let pool = Pool::new();
    let outsider = agent();
    pool.enrol_typed(&outsider);
    let made = ok(pool.rig.ask("demo", &pool.token, json!([ask("sync_now", "*")]), json!({})));
    let id = made["id"].as_str().unwrap().to_string();
    let (_, carrier) = pool.rig.session("demo", "carrier");
    for decision in ["approve", "deny"] {
        let mut body = outsider.decision(&made);
        body["decision"] = json!(decision);
        body["sig"] = json!(hex(&outsider.key.sign(&challenge::bytes(&made["challenge"], decision, &outsider.fingerprint())).to_bytes()));
        let refused = pool.rig.call("approval_decide", "", &carrier, body);
        assert_eq!(code(&refused), "denied", "{decision}: {refused}");
    }
    let mut node = pool.rig.node.lock().unwrap();
    assert_eq!(node.approvals.get(&id).unwrap().state, "pending", "the request is still waiting for an admin");
    let now = node.now_ms();
    assert_eq!(node.fatigue.declined_before("demo", &[("sync_now", "*")], now), 0, "and nothing was counted as a decline");
}

#[test]
fn rows_a_non_admin_or_a_weak_key_signed_are_listed_ignored_with_the_reason() {
    let pool = Pool::new();
    let (outsider, typed) = (agent(), person());
    pool.enrol_typed(&outsider);
    let spec = Spec::session(&pool.name, "sync_now");
    pool.append("g-00000000000000d1", &spec, &outsider.fingerprint(), &typed_sign(&outsider));
    assert!(pool.state("g-00000000000000d1").1.contains("not an admin of this pool now"), "{:?}", pool.state("g-00000000000000d1"));
    let admin = pool.phone.fingerprint();
    pool.append("g-00000000000000d2", &spec, &admin, &typed_sign(&outsider));
    assert!(pool.state("g-00000000000000d2").1.contains("signature is not that admin key's"), "a relabelled row: {:?}", pool.state("g-00000000000000d2"));
    pool.append("g-00000000000000d3", &spec, &typed.fingerprint(), &typed_sign(&typed));
    assert!(pool.state("g-00000000000000d3").1.contains("not strong"), "{:?}", pool.state("g-00000000000000d3"));
    let raw = typed.issue_params("demo", "g-00000000000000d4", &Spec::session(&pool.name, "file_delete"));
    let mut raw = raw.as_object().unwrap().clone();
    raw.insert("op".into(), json!("issue"));
    raw.insert("board".into(), json!("demo"));
    pool.rig.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &pool.name, Value::Object(raw)).unwrap();
    assert!(pool.state("g-00000000000000d4").1.contains("come from an approval on a phone"), "{:?}", pool.state("g-00000000000000d4"));
    let listing = pool.grants();
    assert_eq!(listing["governance"], "governed");
    assert_eq!(listing["authority"], json!([pool.phone.fingerprint()]));
    assert_eq!(listing["may_issue"], json!([]));
    let mut ends: Vec<String> = serde_json::from_value(listing["may_revoke"].clone()).unwrap();
    ends.sort();
    let mut listed = vec![pool.phone.fingerprint(), typed.fingerprint()];
    listed.sort();
    assert_eq!(ends, listed, "the keys the pool lists end its grants, the enrolled key it does not list does not");
    assert_eq!(listing["registry"]["device_local"], json!(DEVICE_LOCAL));
}

#[test]
fn a_raw_row_the_admin_phone_signed_itself_with_no_approval_is_no_grant() {
    let pool = Pool::new();
    let phone = &pool.phone;
    let mut body = kit::govnode::approved_row(&pool.device(), "demo", "g-00000000000000e1", &Spec::session(&pool.name, "sync_now"), &phone.fingerprint(), &|bytes| phone.sign(bytes));
    body.remove("approval");
    body.remove("sig");
    let sig = phone.sign(&signing_bytes(&body));
    body.insert("sig".into(), json!(sig));
    pool.rig.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &pool.name, Value::Object(body)).unwrap();
    let (state, why) = pool.state("g-00000000000000e1");
    assert_eq!(state, "ignored", "{why}");
    assert!(why.contains("come from an approval on a phone"), "a good signature of the admin phone is not an approval: {why}");
    assert!(!pool.holds("sync_now"));
}

#[test]
fn a_key_enrolled_here_ends_a_device_local_grant_by_a_call_and_by_a_row_where_it_cannot_end_a_pool_wide_one() {
    let pool = Pool::new();
    let (typed, outsider) = (person(), agent());
    pool.enrol_typed(&outsider);
    for gid in ["g-00000000000000b3", "g-00000000000000b4"] {
        assert_eq!(pool.rig.call("grant_issue", "demo", &pool.token, typed.issue_params("demo", gid, &Spec::session(&pool.name, "link_read")))["ok"], true);
        assert_eq!(pool.state(gid).0, "live");
    }
    let ended = pool.rig.call("grant_revoke", "demo", &pool.token, outsider.revoke_params("demo", "g-00000000000000b3", "done"));
    assert_eq!(ended["ok"], true, "{ended}");
    assert_eq!(pool.state("g-00000000000000b3").0, "revoked");
    pool.append_revoke(outsider.revoke_params("demo", "g-00000000000000b4", "done"));
    assert_eq!(pool.state("g-00000000000000b4").0, "revoked", "a revoke row of a key enrolled here counts for a grant that stays on this device");
    assert!(!pool.holds("link_read"));
    pool.approve("sync_now", "*");
    let wide = pool.only_gid_of("sync_now");
    pool.append_revoke(outsider.revoke_params("demo", &wide, "done"));
    assert_eq!(pool.state(&wide).0, "live", "and does not for one that reaches the pool");
}

#[test]
fn a_typed_key_grants_exactly_the_device_local_actions_in_a_pool_and_every_other_action_waits_for_a_phone() {
    let pool = Pool::new();
    let typed = person();
    assert_eq!(DEVICE_LOCAL, ["link_read", "link_remove", "source_add", "quarantine_release", "pool_leave"], "an action is pool-wide until a reviewed change lists it here");
    assert!(DEVICE_LOCAL.iter().all(|a| ACTIONS.contains(a) && !pool_wide(a)));
    assert_eq!(ACTIONS.iter().filter(|a| pool_wide(a)).count(), ACTIONS.len() - DEVICE_LOCAL.len());
    assert!(pool_wide("an_action_added_later"));
    for (n, action) in ACTIONS.iter().enumerate() {
        let gid = format!("g-{:016x}", 0xc000 + n);
        let reply = pool.rig.call("grant_issue", "demo", &pool.token, typed.issue_params("demo", &gid, &Spec::session(&pool.name, action)));
        if DEVICE_LOCAL.contains(action) {
            assert_eq!(reply["ok"], true, "{action}: {reply}");
            assert_eq!(pool.state(&gid).0, "live", "{action}");
            assert!(pool.holds(action), "{action}");
        } else {
            assert!(Pool::denial(&reply).contains("your phone gives standing grants"), "{action}: {reply}");
            assert_eq!(pool.state(&gid).0, "absent", "{action}: nothing was written");
            assert!(!pool.holds(action), "{action}");
        }
    }
}
