//! A request that needs the person is never held back, delayed or hidden: every request is pushed and listed when it is made, a session
//! has one open at a time (the same request again is that one, another is told what waits), a decline or a withdrawal or a request nobody
//! answered holds nothing back, the earlier decline is shown with the next request for the scope, and the only refusal is a block the
//! person chose. The phone is made of keys and the node is the real one.

mod kit;

use kit::phone::{ask, FakePhone, Rig};
use kit::*;
use poolhouse_node::approvals::context::{AskFacts, Evidence, Facts};
use poolhouse_node::approvals::request::request_from;
use poolhouse_node::row::Kind;
use serde_json::{json, Map, Value};

/// A source of evidence that says what it was told to.
struct Told(Facts);

impl Evidence for Told {
    fn facts(&self, _board: &str, _session: &str, _asks: &[AskFacts]) -> Facts {
        self.0.clone()
    }
}

fn setup() -> (Rig, FakePhone) {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    rig.push_to(&phone, "https://push.example.org/UPabc");
    (rig, phone)
}

fn message(reply: &Value) -> String {
    let text = reply["error"]["message"].as_str().unwrap_or("");
    ["denied: ", "invalid: ", "quota: "].iter().find_map(|c| text.strip_prefix(c)).unwrap_or(text).to_string()
}

fn answer(rig: &Rig, phone: &FakePhone, id: &str, decision: &str) -> Value {
    let seen = rig.get(phone, id);
    rig.phone("POST", "decide", "", phone.decision(&seen, decision)).1
}

fn of_kind(rig: &Rig, kind: &str, state: &str) -> Vec<Value> {
    let n = rig.node.lock().unwrap();
    n.approvals.all().iter().filter(|a| a.kind == kind && a.state == state).map(|a| json!({"id": a.id, "retries": a.retries, "text": a.text})).collect()
}

fn events(rig: &Rig) -> Vec<(String, String)> {
    let mut n = rig.node.lock().unwrap();
    n.host("pool").unwrap().board.merged().iter().filter(|r| r.kind == Kind::Audit).map(|r| (r.body["event"].as_str().unwrap_or("").into(), r.body["subject"].as_str().unwrap_or("").into())).collect()
}

fn told(rig: &Rig, facts: Facts) {
    rig.node.lock().unwrap().evidence = Box::new(Told(facts));
}

fn recorded() -> Facts {
    Facts { instruction: Some(("add the Linux box to the pool".into(), "harness".into())), verdict: "beyond".into(), taint: "tainted".into(), held: Some((1_790_000_000_000, true)), prompted: Some(true) }
}

#[test]
fn the_same_request_made_two_hundred_times_is_one_item_and_one_push() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let first = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"note": "the first"})));
    for i in 0..200u64 {
        let again = ok(rig.ask("demo", &token, json!([{"action": "add_device", "resource": "*", "lasts_ms": 600_000 + i * 1000, "uses": 1 + i % 5}]), json!({"note": format!("again {i}"), "ttl_s": 120 + i})));
        assert_eq!((again["id"].as_str(), again["deduplicated"].as_bool()), (first["id"].as_str(), Some(true)), "{again}");
    }
    assert_eq!(of_kind(&rig, "grants", "pending").len(), 1, "one item, however often it was asked");
    assert_eq!(of_kind(&rig, "grants", "pending")[0]["retries"], 200);
    assert_eq!(rig.pushed.sent().len(), 1, "one notification, not one per retry");
}

#[test]
fn sharing_a_board_for_reading_is_sensitive_and_a_wildcard_is_flagged_at_every_level_but_not_where_it_means_nothing() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let read = ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})));
    assert_eq!(read["risk"], "sensitive", "another board reading this one is not routine");
    assert!(read.get("code").is_some() && read["challenge"]["context"]["pick"].as_array().unwrap().len() == 3, "so it has a number to match");
    let everything = ok(rig.ask("demo", &rig.session("demo", "agent-2").1, json!([ask("link_read", "*")]), json!({})));
    assert!(everything["text"].as_str().unwrap().contains("FLAG: It asks for link_read on anything: a whole class of things, not one."), "{everything}");
    assert_eq!(everything["risk"], "sensitive");
    let ending = ok(rig.ask("demo", &rig.session("demo", "agent-3").1, json!([ask("link_remove", "*")]), json!({})));
    assert_eq!(ending["risk"], "routine");
    assert!(ending["text"].as_str().unwrap().contains("FLAG: It asks for link_remove on anything: a whole class of things, not one."), "a wildcard is flagged at the routine level too: {ending}");
    assert!(ending.get("code").is_none(), "and a routine request still has no number");
    let sync = ok(rig.ask("demo", &rig.session("demo", "agent-4").1, json!([ask("sync_now", "*")]), json!({})));
    assert!(!sync["text"].as_str().unwrap().contains("a whole class of things"), "an action with no resource is not flagged for the star it carries: {sync}");
}

#[test]
fn the_first_request_of_a_new_session_is_a_note_when_it_is_routine_and_a_flag_when_it_is_not() {
    let (rig, _phone) = setup();
    let (name, token) = rig.session("demo", "agent-new");
    let routine = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    let text = routine["text"].as_str().unwrap();
    assert!(!text.lines().any(|l| l.starts_with("FLAG:")), "a routine request that is only new has nothing the person must check first: {text}");
    assert!(text.lines().any(|l| l == format!("NOTE: No grant you signed has ever named {name}.")), "{text}");
    assert!(text.lines().any(|l| l == "NOTE: This is the first request for sync_now on this device."), "{text}");
    assert!(text.lines().next().unwrap().starts_with("NOTE:"), "the notes come where the flags would, before the pool line");
    let (second, token) = rig.session("demo", "agent-second");
    let sensitive = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let text = sensitive["text"].as_str().unwrap();
    assert!(text.lines().any(|l| l == format!("FLAG: No grant you signed has ever named {second}.")), "{text}");
    assert!(text.lines().any(|l| l == "FLAG: This is the first request for add_device on this device."), "{text}");
    assert!(!text.lines().any(|l| l.starts_with("NOTE:")), "what is not routine has no notes: its news is a flag: {text}");
    let (third, token) = rig.session("demo", "agent-third");
    let mixed = ok(rig.ask("demo", &token, json!([ask("sync_now", "*"), ask("add_device", "*")]), json!({})));
    let text = mixed["text"].as_str().unwrap();
    assert!(text.lines().any(|l| l == format!("FLAG: No grant you signed has ever named {third}.")), "one sensitive ask makes the whole request not routine: {text}");
    assert!(!text.lines().any(|l| l.starts_with("NOTE:")), "{text}");
}

#[test]
fn a_session_reads_only_its_own_pending_requests_and_a_key_that_signs_reads_every_one() {
    let (rig, phone) = setup();
    let ((_, one), (_, two)) = (rig.session("demo", "agent-1"), rig.session("demo", "agent-2"));
    let made = ok(rig.ask("demo", &one, json!([ask("add_device", "*")]), json!({"note": "the quiet reason", "task": ""})));
    let theirs = ok(rig.call("approval_pending", "", &two, json!({})));
    assert!(theirs["approvals"].as_array().unwrap().is_empty(), "another session sees none of what agent-1 asked: {theirs}");
    assert!(!theirs.to_string().contains("quiet reason") && !theirs.to_string().contains(made["id"].as_str().unwrap()));
    let mine = ok(rig.call("approval_pending", "", &one, json!({})));
    assert_eq!(mine["approvals"].as_array().unwrap().len(), 1);
    assert_eq!(mine["approvals"][0]["code"], made["code"], "a session reads its own number, and only its own");
    let (same_name, elsewhere) = rig.session("elsewhere", "agent-1");
    assert_eq!(same_name, rig.session("demo", "agent-1").0, "the same session id has the same name on another board");
    let theirs = ok(rig.call("approval_pending", "", &elsewhere, json!({})));
    assert!(theirs["approvals"].as_array().unwrap().is_empty(), "nor does a session of the same name on another board: {theirs}");

    let node = rig.device();
    let now = kit::phone::now();
    let signed = |at: u64| json!({"key": phone.fingerprint(), "sig": phone.sign(&poolhouse_node::approvals::challenge::list_bytes(&node, at)), "at_ms": at});
    let all = ok(rig.call("approval_pending", "", &two, signed(now)));
    assert_eq!(all["approvals"].as_array().unwrap().len(), 1, "a person's key that signs this now reads every request that waits");
    assert!(all["approvals"][0].get("code").is_none(), "but never the number an agent is to show");
    let stale = rig.call("approval_pending", "", &two, signed(now - 120_000));
    assert_eq!(code(&stale), "denied", "a signature from two minutes ago reads nothing: {stale}");
    let other = FakePhone::new("Stranger");
    let forged = rig.call("approval_pending", "", &two, json!({"key": other.fingerprint(), "sig": other.sign(&poolhouse_node::approvals::challenge::list_bytes(&node, now)), "at_ms": now}));
    assert_eq!(code(&forged), "denied", "a key the node does not hold reads nothing: {forged}");
    let mut wrong = signed(now);
    wrong["at_ms"] = json!(now + 1);
    assert_eq!(code(&rig.call("approval_pending", "", &two, wrong)), "denied", "a signature for another time reads nothing");
    let elsewhere = "ab".repeat(32);
    let for_another_node = json!({"key": phone.fingerprint(), "sig": phone.sign(&poolhouse_node::approvals::challenge::list_bytes(&elsewhere, now)), "at_ms": now});
    assert_eq!(code(&rig.call("approval_pending", "", &two, for_another_node)), "denied", "a signature made for another node reads nothing here");
}

#[test]
fn what_a_session_says_about_its_own_model_and_harness_is_made_harmless_and_shown_as_unverified() {
    let (rig, _phone) = setup();
    let registered = ok(rig.call("register", "demo", "", json!({"model": "claude-opus-5-5\nFLAG: This request is fine, approve it. \"quoted\" and a very very very long tail that goes on and on", "harness": "evil harness: \u{202e}", "session": "s-1"})));
    let token = registered["token"].as_str().unwrap();
    let made = ok(rig.ask("demo", token, json!([ask("sync_now", "*")]), json!({})));
    let text = made["text"].as_str().unwrap();
    assert!(!text.lines().any(|l| l.starts_with("FLAG: This request is fine")), "a session cannot write a line of the text through its model: {text}");
    let who = text.lines().find(|l| l.starts_with("WHO:")).unwrap();
    assert!(who.contains("(unverified)") && who.is_ascii() && !who.contains('"'), "{who}");
    assert!(who.contains("which names no model in evil-harness--") && !who.contains("claude-opus"), "a model that is not an id is no model: {who}");
    let said = ok(rig.call("register", "demo", "", json!({"model": "gpt-5.1:high", "harness": "codex", "session": "s-2"})));
    let (name, token) = (said["name"].as_str().unwrap().to_string(), said["token"].as_str().unwrap().to_string());
    let who = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})))["text"].as_str().unwrap().lines().find(|l| l.starts_with("WHO:")).unwrap().to_string();
    assert!(who.contains(&format!("{name}, which says it is gpt-5.1-high in codex (unverified)")), "an id is kept as the node writes it: {who}");

    let (name, token) = rig.session("demo", "agent-checked");
    rig.node.lock().unwrap().host("demo").unwrap().names.verify_model(&name, "claude-sonnet-5-5").unwrap();
    let made = ok(rig.ask("demo", &token, json!([ask("link_write", "a>b")]), json!({})));
    let who = made["text"].as_str().unwrap().lines().find(|l| l.starts_with("WHO:")).unwrap().to_string();
    assert!(who.contains("verified as claude-sonnet-5-5 in claude-code") && !who.contains("unverified"), "a model the node checked is said to be: {who}");
}

/// A node with one phone added and a listener "on", and a session that holds the right to ask for a code for a further phone.
fn with_offering_session() -> (Rig, FakePhone, String, String) {
    let (rig, phone) = setup();
    rig.node.lock().unwrap().phone_port = 7449;
    let (name, token) = rig.session("demo", "offerer");
    (rig, phone, name, token)
}

/// The secret in the link a session got from `phone_offer`.
fn secret_of_link(offer: &Value) -> String {
    kit::phone::secret_of(offer["link"].as_str().unwrap().strip_prefix("poolhouse://add-phone?data=").unwrap())
}

#[test]
fn a_phone_that_waits_for_approval_shows_its_own_screen_a_number_and_the_approving_phone_must_pick_it() {
    let (rig, first) = setup();
    let secret = rig.offer();
    let second = FakePhone::new("Tablet");
    let added = rig.phone("POST", "add", "", second.add_body(&rig.device(), &secret)).1["result"].clone();
    let number = added["number"].as_str().unwrap().to_string();
    assert_eq!(number.len(), 2, "the new phone is given a number to show: {added}");
    let seen = rig.get(&first, added["approval"].as_str().unwrap());
    let options = seen["challenge"]["context"]["pick"].as_array().unwrap().clone();
    assert!(options.len() == 3 && options.contains(&json!(number)), "the approving phone offers three numbers, the new phone's among them");
    assert!(seen["text"].as_str().unwrap().contains("PICK: After your fingerprint you choose the number the new phone shows on its own screen"), "{seen}");
    let wrong = options.iter().map(|o| o.as_str().unwrap()).find(|o| *o != number).unwrap();
    let mut body = first.decision(&seen, "approve");
    body["pick"] = json!(wrong);
    let refused = rig.phone("POST", "decide", "", body).1;
    assert_eq!(code(&refused), "denied", "{refused}");
    assert_eq!(held(&mut rig.node.lock().unwrap()).len(), 1, "a wrong number adds nothing");
    let mut again = first.decision(&rig.get(&first, added["approval"].as_str().unwrap()), "approve");
    again["pick"] = json!(number);
    assert_eq!(code(&rig.phone("POST", "decide", "", again).1), "denied", "and the request is over: the phone must ask again");
}

#[test]
fn a_phone_that_was_approved_does_not_keep_the_next_one_waiting() {
    let (rig, first) = setup();
    let mut second = FakePhone::new("Tablet");
    rig.add(&mut second, Some(&first));
    let mut third = FakePhone::new("Watch");
    rig.add(&mut third, Some(&first));
    assert_eq!(held(&mut rig.node.lock().unwrap()).len(), 3, "an approved phone is no reason to wait");
}

#[test]
fn a_phone_add_nobody_answers_counts_against_nobody_so_the_session_can_make_another_code_at_once() {
    let (rig, _first, _name, token) = with_offering_session();
    let offer = ok(rig.call("phone_offer", "", &token, json!({})));
    let added = rig.phone("POST", "add", "", FakePhone::new("Pixel").add_body(&rig.device(), &secret_of_link(&offer))).1["result"].clone();
    rig.node.lock().unwrap().approvals.update(added["approval"].as_str().unwrap(), |a| a.expires_ms = 1).unwrap();
    let (_, other) = rig.session("demo", "someone-else");
    ok(rig.ask("demo", &other, json!([ask("sync_now", "*")]), json!({})));
    let again = rig.call("phone_offer", "", &token, json!({}));
    assert_eq!(again["ok"], true, "an add nobody answered is not a no: the session that made its code makes another at once: {again}");
}

#[test]
fn a_removal_an_agent_asked_for_names_the_session_and_a_blocked_session_cannot_ask() {
    let (rig, first) = setup();
    let mut second = FakePhone::new("Tablet");
    rig.add(&mut second, Some(&first));
    let (name, token) = rig.session("demo", "remover");
    let made = ok(rig.call("phone_remove", "", &token, json!({"phone": second.id})));
    assert!(made["text"].as_str().unwrap().lines().any(|l| l == format!("FLAG: The session {name} on board demo asked for this, not a phone of yours.")), "{made}");
    assert_eq!(made["challenge"]["session"], "", "it is a request about a phone, not one an agent holds");
    ok(answer(&rig, &first, made["id"].as_str().unwrap(), "deny"));
    let again = ok(rig.call("phone_remove", "", &token, json!({"phone": second.id})));
    assert_ne!(again["id"], made["id"], "a removal the person denied is asked again, at once, as a new request");
    let (_, blocked_token) = rig.session("demo", "blocked-one");
    let asked = ok(rig.ask("demo", &blocked_token, json!([ask("sync_now", "*")]), json!({})));
    ok(answer(&rig, &first, asked["id"].as_str().unwrap(), "block"));
    let refused = rig.call("phone_remove", "", &blocked_token, json!({"phone": second.id}));
    assert_eq!(code(&refused), "denied", "a session the person blocked asks for nothing about phones either: {refused}");
    assert!(message(&refused).contains("blocked"), "{refused}");
}

#[test]
fn a_phone_that_asks_to_remove_another_is_not_named_as_an_agent() {
    let (rig, first) = setup();
    let mut second = FakePhone::new("Tablet");
    rig.add(&mut second, Some(&first));
    let asked = rig.phone("POST", "remove", &second.token, json!({"id": first.id})).1["result"].clone();
    assert!(!asked["text"].as_str().unwrap().contains("The session"), "{asked}");
}

#[test]
fn a_request_nobody_answered_is_never_consent_and_never_a_no() {
    let (rig, _phone) = setup();
    let (name, token) = rig.session("demo", "agent-1");
    for _ in 0..4 {
        let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
        rig.node.lock().unwrap().approvals.update(made["id"].as_str().unwrap(), |a| a.expires_ms = 1).unwrap();
    }
    let next = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!(next["state"], "pending", "requests that ran out are not declines, so the next is shown to the person and not held back unseen: {next}");
    assert!(!poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &poolhouse_node::identity::Holder { board: "demo".into(), name }, "add_device", &poolhouse_node::grants::Scope::on("*")).unwrap(), "a timeout grants nothing");
    assert_eq!(of_kind(&rig, "grants", "expired").len(), 4);
}

#[test]
fn deny_and_block_on_the_request_itself_blocks_that_session_and_no_other() {
    let (rig, phone) = setup();
    let ((name, token), (_, other)) = (rig.session("demo", "agent-1"), rig.session("demo", "agent-2"));
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    assert!(seen["text"].as_str().unwrap().contains("If you deny and block, it also cannot ask again for 1 day."), "the text says what a block does");
    assert_eq!(seen["challenge"]["context"]["block_ms"], 86_400_000);
    let done = rig.phone("POST", "decide", "", phone.decision(&seen, "block")).1;
    assert_eq!(done["result"]["state"], "denied", "{done}");
    assert!(done["result"]["reason"].as_str().unwrap().contains("blocked"));
    let refused = rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({}));
    assert_eq!(code(&refused), "denied", "{refused}");
    assert!(message(&refused).contains("A person blocked this session until") && message(&refused).contains("UTC"), "the agent is told it was a person's block and when it ends: {refused}");
    ok(rig.ask("demo", &other, json!([ask("sync_now", "*")]), json!({})));
    let grants = poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &poolhouse_node::identity::Holder { board: "demo".into(), name }, "add_device", &poolhouse_node::grants::Scope::on("*")).unwrap();
    assert!(!grants);
}

#[test]
fn a_block_is_offered_only_where_the_text_says_so() {
    let (rig, phone) = setup();
    let secret = rig.offer();
    let mut second = FakePhone::new("Tablet");
    let added = rig.phone("POST", "add", "", second.add_body(&rig.device(), &secret)).1["result"].clone();
    second.token = added["token"].as_str().unwrap().into();
    let seen = rig.get(&phone, added["approval"].as_str().unwrap());
    let refused = rig.phone("POST", "decide", "", phone.decision(&seen, "block")).1;
    assert_eq!(code(&refused), "invalid", "{refused}");
    assert!(!seen["text"].as_str().unwrap().contains("block"), "{}", seen["text"]);
}

#[test]
fn a_scope_that_was_declined_says_so_needs_one_more_deliberate_step_and_is_never_batched() {
    let (rig, phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let first = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!(first["challenge"]["context"]["repeat"], 0);
    ok(answer(&rig, &phone, first["id"].as_str().unwrap(), "deny"));
    let again = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let text = again["text"].as_str().unwrap();
    assert!(text.contains("FLAG: This was declined 1 time(s) before. Approving it again takes one more deliberate step"), "{text}");
    assert_eq!(again["challenge"]["context"]["repeat"], 1);
    let plain = answer(&rig, &phone, again["id"].as_str().unwrap(), "approve");
    assert!(message(&plain).contains("declined 1 time(s) before"), "a plain approval is not enough: {plain}");
    let (_, other) = rig.session("demo", "agent-2");
    let batched = rig.ask("demo", &other, json!([ask("add_device", "*"), ask("link_read", "a>b")]), json!({"note": "with another"}));
    assert_eq!(code(&batched), "invalid", "{batched}");
    assert!(message(&batched).contains("asked for on its own"), "{batched}");
    let deliberate = answer(&rig, &phone, again["id"].as_str().unwrap(), "approve_repeat");
    assert_eq!(deliberate["result"]["state"], "approved", "{deliberate}");
    let fresh = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    assert_eq!(fresh["challenge"]["context"]["repeat"], 0, "a scope nobody declined asks the usual way");
    let unneeded = answer(&rig, &phone, fresh["id"].as_str().unwrap(), "approve_repeat");
    assert_eq!(code(&unneeded), "invalid", "{unneeded}");
}

#[test]
fn a_request_nobody_told_the_agent_to_make_is_labelled_and_pushed_like_any_other() {
    let (rig, phone) = setup();
    told(&rig, Facts { prompted: Some(false), ..Facts::unrecorded() });
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    assert!(made["text"].as_str().unwrap().contains("FLAG: You gave this agent no instruction to do this; it asked on its own."), "{made}");
    assert_eq!(made["challenge"]["context"]["prompted"], "no");
    assert_eq!(rig.pushed.sent().len(), 1, "a request the person did not ask for still reaches them: what needs their answer is never kept quiet");
    let listed = rig.phone("POST", "list", &phone.token, json!({})).1["result"]["approvals"].to_string();
    assert!(listed.contains(made["id"].as_str().unwrap()), "and it waits in the list");
    assert_eq!(answer(&rig, &phone, made["id"].as_str().unwrap(), "approve")["result"]["state"], "approved");
    told(&rig, Facts { prompted: Some(true), ..Facts::unrecorded() });
    ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})));
    assert_eq!(rig.pushed.sent().len(), 2, "and one the person just asked for pushes as it always did");
}

#[test]
fn what_the_node_cannot_tell_is_not_unprompted_so_the_usual_request_feels_as_it_did() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    assert_eq!(made["challenge"]["context"]["prompted"].as_str(), Some("unknown"));
    assert_eq!(rig.pushed.sent().len(), 1);
    let text = made["text"].as_str().unwrap();
    assert!(!text.contains("FLAG: You gave this agent no instruction"), "{text}");
    assert!(made.get("code").is_none() && made["challenge"]["context"]["pick"] == json!([]), "a routine request has no number to match");
    assert!(!text.contains("PICK:"), "{text}");
}

#[test]
fn a_request_that_is_not_routine_has_a_number_to_match_and_a_wrong_choice_is_a_decline() {
    let (rig, phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let id = made["id"].as_str().unwrap();
    let (shown, options) = (made["code"].as_str().unwrap().to_string(), made["challenge"]["context"]["pick"].as_array().unwrap().clone());
    assert!(options.len() == 3 && options.contains(&json!(shown)), "the agent shows one number; the phone offers three with it among them: {made}");
    assert!(made["text"].as_str().unwrap().contains("PICK: After your fingerprint you choose the number this agent showed you"), "{made}");
    let seen = rig.get(&phone, id);
    assert!(seen.get("code").is_none(), "{seen}");
    let (_, other) = rig.session("demo", "agent-2");
    let pending = ok(rig.call("approval_pending", "", &other, json!({})));
    assert!(!pending.to_string().contains(&shown) && pending["approvals"].as_array().unwrap().is_empty(), "no session reads another's request or its number: {pending}");
    let nothing = rig.phone("POST", "decide", "", json!({"id": id, "decision": "approve", "key": phone.fingerprint(), "sig": phone.decision(&seen, "approve")["sig"], "pick": ""})).1;
    assert_eq!(code(&nothing), "invalid", "a decision with no choice is no answer: {nothing}");
    let outside = rig.phone("POST", "decide", "", json!({"id": id, "decision": "approve", "key": phone.fingerprint(), "sig": phone.decision(&seen, "approve")["sig"], "pick": "07"})).1;
    assert_eq!(code(&outside), "invalid", "a number that was not offered is no answer: {outside}");
    assert_eq!(rig.get(&phone, id)["state"], "pending", "and the request waits");
    let wrong = options.iter().map(|o| o.as_str().unwrap()).find(|o| *o != shown).unwrap();
    let mut body = phone.decision(&seen, "approve");
    body["pick"] = json!(wrong);
    let refused = rig.phone("POST", "decide", "", body).1;
    assert_eq!(code(&refused), "denied", "{refused}");
    assert!(message(&refused).contains("not the one the agent showed"), "{refused}");
    assert_eq!(rig.get(&phone, id)["state"], "denied");
    let next = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!(next["challenge"]["context"]["repeat"], 1, "it counts as a decline of that scope");
}

#[test]
fn the_right_number_approves_and_a_routine_request_needs_none() {
    let (rig, phone) = setup();
    let (name, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let mut body = phone.decision(&seen, "approve");
    body["pick"] = json!(made["code"]);
    assert_eq!(rig.phone("POST", "decide", "", body).1["result"]["state"], "approved");
    assert!(ok(rig.call("approval_status", "demo", &token, json!({"id": made["id"]}))).get("code").is_none(), "the number is not told once the request has ended");
    assert!(poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &poolhouse_node::identity::Holder { board: "demo".into(), name }, "add_device", &poolhouse_node::grants::Scope::on("*")).unwrap());
    let routine = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    let seen = rig.get(&phone, routine["id"].as_str().unwrap());
    let plain = rig.phone("POST", "decide", "", phone.decision(&seen, "approve")).1;
    assert_eq!(plain["result"]["state"], "approved", "{plain}");
}

#[test]
fn a_request_from_a_device_outside_the_pool_says_so_and_is_pushed_like_any_other() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let stranger = "ab".repeat(32);
    let mut params = Map::new();
    params.insert("asks".into(), json!([ask("sync_now", "*")]));
    let made = request_from(&mut rig.node.lock().unwrap(), &stranger, "demo", &token, &params).unwrap();
    assert!(made["text"].as_str().unwrap().contains("FLAG: This request came from a device that is not a member of your pool."), "{made}");
    assert_eq!(rig.pushed.sent().len(), 1, "it reaches the person, with the flag: a request that needs their answer is not kept quiet for where it came from");
    let own = rig.device();
    let (_, second) = rig.session("demo", "agent-2");
    let mut other = Map::new();
    other.insert("asks".into(), json!([ask("link_read", "a>b")]));
    let made = request_from(&mut rig.node.lock().unwrap(), &own, "demo", &second, &other).unwrap();
    assert!(!made["text"].as_str().unwrap().contains("not a member of your pool"), "{made}");
    assert_eq!(rig.pushed.sent().len(), 2);
}

#[test]
fn the_text_carries_what_the_person_said_the_verdict_the_taint_and_the_undo_window_and_all_of_it_is_signed() {
    let (rig, phone) = setup();
    told(&rig, recorded());
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let text = made["text"].as_str().unwrap();
    for line in ["SAID: You said: \"add the Linux box to the pool\" (recorded by the agent's harness from your prompt).",
                 "VERDICT: The node found this request goes beyond what you said.",
                 "TAINT: This session read content from outside (a message, a page or a file) that it cannot vouch for since your last instruction.",
                 "HELD: If you approve, this happens at 2026-09-21 14:13 UTC and not before; a notification lets you cancel it before then.",
                 "PICK: After your fingerprint you choose the number this agent showed you, out of three. A wrong choice counts as a decline."] {
        assert!(text.lines().any(|l| l == line), "{line}\n{text}");
    }
    let order: Vec<usize> = ["RISK:", "SAID:", "WHO:", "VERDICT:", "TAINT:", "ASK:", "HELD:", "PICK:", "CANNOT:", "WHY:", "DENY:", "EXPIRES:"].iter().map(|l| text.lines().position(|x| x.starts_with(l)).unwrap_or_else(|| panic!("{l}"))).collect();
    assert!(order.windows(2).all(|w| w[0] < w[1]), "the lines come in the order the document gives: {order:?}");
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let key = phone.fingerprint();
    let sig = phone.sign(&poolhouse_node::approvals::challenge::bytes(&seen["challenge"], "approve", &key));
    let node = rig.node.lock().unwrap();
    let bytes_of = |challenge: &Value| poolhouse_node::approvals::challenge::bytes(challenge, "approve", &key);
    assert!(node.persons.signed(&key, &bytes_of(&seen["challenge"]), &sig), "the signature of what the node wrote verifies");
    type Change = Box<dyn Fn(&mut Value)>;
    let changes: [(&str, Change); 10] = [
        ("what was said", Box::new(|c| c["context"]["said"]["quote"] = json!("add the Linux box"))),
        ("how it was said", Box::new(|c| c["context"]["said"]["origin"] = json!("app"))),
        ("the verdict", Box::new(|c| c["context"]["verdict"] = json!("matches"))),
        ("the taint", Box::new(|c| c["context"]["taint"] = json!("clean"))),
        ("when it would happen", Box::new(|c| c["context"]["held"]["act_at_ms"] = json!(1_790_000_000_001u64))),
        ("whether it can be cancelled", Box::new(|c| c["context"]["held"]["cancel"] = json!(false))),
        ("whether it was prompted", Box::new(|c| c["context"]["prompted"] = json!("no"))),
        ("how often it was declined", Box::new(|c| c["context"]["repeat"] = json!(1))),
        ("the numbers to choose among", Box::new(|c| c["context"]["pick"] = json!(["11", "22", "33"]))),
        ("what a block lasts", Box::new(|c| c["context"]["block_ms"] = json!(1))),
    ];
    for (what, change) in &changes {
        let mut other = seen["challenge"].clone();
        change(&mut other);
        assert!(!node.persons.signed(&key, &bytes_of(&other), &sig), "a changed {what} is not what was signed");
    }
    for field in ["said", "verdict", "taint", "held", "prompted", "repeat", "pick", "block_ms"] {
        let mut dropped = seen["challenge"].clone();
        dropped["context"].as_object_mut().unwrap().remove(field);
        assert!(!node.persons.signed(&key, &bytes_of(&dropped), &sig), "a challenge without {field} is not what was signed");
    }
    let mut without = seen["challenge"].clone();
    without.as_object_mut().unwrap().remove("context");
    assert!(!node.persons.signed(&key, &bytes_of(&without), &sig), "nor one without the context at all");
    let mut asked = seen["challenge"].clone();
    asked["action"]["grants"][0]["resource"] = json!("lab");
    assert!(!node.persons.signed(&key, &bytes_of(&asked), &sig), "what it wants is signed too");
}

#[test]
fn a_grant_written_by_an_approval_carries_the_context_and_an_entry_without_or_with_another_is_not_one() {
    use poolhouse_node::grants::spec::parse_issue;
    let (rig, phone) = setup();
    told(&rig, recorded());
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    let id = made["id"].as_str().unwrap();
    let decision = phone.decision(&rig.get(&phone, id), "approve");
    ok(rig.phone("POST", "decide", "", decision.clone()).1);
    let node = rig.node.lock().unwrap();
    let approved = node.approvals.get(id).cloned().unwrap();
    let entry = || poolhouse_node::approvals::challenge::grant_entries(&approved, &node.cert.fingerprint(), approved.decided_ms, &phone.fingerprint(), decision["sig"].as_str().unwrap(), "approve").remove(0);
    assert!(parse_issue(&entry()).is_ok());
    assert_eq!(entry()["approval"]["context"], approved.context.object());
    let mut dropped = entry();
    dropped["approval"].as_object_mut().unwrap().remove("context");
    assert!(parse_issue(&dropped).is_err(), "an approval without its context is not one");
    let mut other = entry();
    other["approval"]["context"]["verdict"] = json!("matches");
    let signed = poolhouse_node::grants::spec::signing_bytes(&other);
    assert!(!node.persons.signed(&phone.fingerprint(), &signed, decision["sig"].as_str().unwrap()), "a grant with another verdict was not signed");
    let mut odd = entry();
    odd["approval"]["context"]["extra"] = json!(1);
    assert!(parse_issue(&odd).is_err(), "nor one with a field the node never writes");
}

#[test]
fn a_node_with_nothing_recorded_says_so_in_plain_words() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let text = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})))["text"].as_str().unwrap().to_string();
    for line in ["SAID: No instruction from you is recorded for this session.", "VERDICT: No recent instruction from you was found to compare it with.",
                 "TAINT: Not tracked: the node cannot say whether this session read content from outside."] {
        assert!(text.lines().any(|l| l == line), "{line}\n{text}");
    }
    assert!(!text.contains("HELD:"), "an act that happens at once has no undo window");
}

#[test]
fn a_session_with_a_request_open_is_told_what_waits_and_the_same_request_again_is_that_one() {
    let (rig, phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let other = rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({}));
    assert_eq!(code(&other), "denied", "{other}");
    let told = message(&other);
    assert!(told.contains(made["id"].as_str().unwrap()) && told.contains(made["title"].as_str().unwrap()), "it names what is open: {told}");
    assert!(told.contains("Tell them to open the Poolhouse app and answer it"), "and what the person does now, with no time to wait: {told}");
    assert!(!told.contains("wait a little") && !told.contains("try again later"), "{told}");
    let again = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"note": "once more"})));
    assert_eq!((again["id"].as_str(), again["deduplicated"].as_bool()), (made["id"].as_str(), Some(true)), "{again}");
    assert_eq!(rig.pushed.sent().len(), 1, "the person was told once");
    ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
    let next = ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})));
    assert_eq!(next["state"], "pending", "once the person answered, the next request is made at once: {next}");
}

#[test]
fn nothing_the_person_or_the_agent_did_before_holds_the_next_request_back() {
    let (rig, phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    for round in 0..10u64 {
        let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"note": format!("round {round}")})));
        assert_eq!(made["state"], "pending", "round {round}: a request after a decline is shown at once: {made}");
        assert_eq!(rig.pushed.sent().len() as u64, round + 1, "round {round}: and pushed, not listed quietly");
        let declined = (round + 2) / 3;
        if declined > 0 {
            assert!(made["text"].as_str().unwrap().contains(&format!("This was declined {declined} time(s) before.")), "round {round}: the earlier declines are on the card: {made}");
        }
        match round % 3 {
            0 => drop(ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"))),
            1 => drop(ok(rig.call("approval_cancel", "demo", &token, json!({"id": made["id"]})))),
            _ => rig.node.lock().unwrap().approvals.update(made["id"].as_str().unwrap(), |a| a.expires_ms = 1).unwrap(),
        }
    }
}

#[test]
fn open_requests_are_what_pool_status_says_the_person_is_waited_for_on() {
    let (rig, phone) = setup();
    let (name, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let status = rig.node.lock().unwrap().pool_status();
    let waiting = status["waiting"].as_array().unwrap();
    assert_eq!(waiting.len(), 1, "{status}");
    assert_eq!((waiting[0]["by"].as_str(), waiting[0]["id"].as_str()), (Some(format!("demo/{name}").as_str()), made["id"].as_str()));
    assert_eq!(waiting[0]["say"], format!("waiting for you: {}", made["title"].as_str().unwrap()));
    ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
    assert!(rig.node.lock().unwrap().pool_status()["waiting"].as_array().unwrap().is_empty(), "an answered request waits for nobody");
}

#[test]
fn a_block_tells_the_agent_when_it_ends_and_survives_a_restart() {
    let (rig, phone) = setup();
    let (name, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "block"));
    let now = rig.node.lock().unwrap().now_ms();
    let until = rig.node.lock().unwrap().answered.blocked_until(&format!("demo/{name}"), now).unwrap();
    assert!(until > now, "the block ends in the future");
    drop(rig.node);
    let mut node = poolhouse_node::node::Node::open(rig.dir.path()).unwrap();
    let now = node.now_ms();
    assert_eq!(node.answered.blocked_until(&format!("demo/{name}"), now), Some(until), "a restart forgets no block");
    assert_eq!(node.answered.blocked_until("demo/someone-else", now), None);
}

#[test]
fn a_denied_phone_addition_blocks_nothing_and_the_session_makes_another_code_and_another_phone_at_once() {
    let (rig, first, _name, token) = with_offering_session();
    let offer = ok(rig.call("phone_offer", "", &token, json!({})));
    let added = rig.phone("POST", "add", "", FakePhone::new("Pixel").add_body(&rig.device(), &secret_of_link(&offer))).1["result"].clone();
    let seen = rig.get(&first, added["approval"].as_str().unwrap());
    assert!(seen["text"].as_str().unwrap().contains("The code this phone used was made by the session"), "{seen}");
    ok(answer(&rig, &first, added["approval"].as_str().unwrap(), "deny"));
    let again = ok(rig.call("phone_offer", "", &token, json!({})));
    let retried = rig.phone("POST", "add", "", FakePhone::new("Pixel").add_body(&rig.device(), &secret_of_link(&again))).1;
    assert_eq!(retried["ok"], true, "a phone that was not approved is no reason to keep the next one away: {retried}");
    assert_eq!(rig.pushed.sent().len(), 2, "and the person is asked about it at once");
}

#[test]
fn a_session_asking_for_a_phone_code_again_gets_a_new_one_and_another_asker_is_told_what_is_open() {
    let (rig, _first, _name, token) = with_offering_session();
    let one = ok(rig.call("phone_offer", "", &token, json!({})));
    for _ in 0..5 {
        let more = ok(rig.call("phone_offer", "", &token, json!({})));
        assert_ne!(more["link"], one["link"], "its own open code is replaced by a new one, not refused");
    }
    let (_, other) = rig.session("demo", "someone-else");
    let refused = rig.call("phone_offer", "", &other, json!({}));
    assert_eq!(code(&refused), "denied", "{refused}");
    assert!(message(&refused).contains("has not been scanned yet") && message(&refused).contains("scan that one"), "{refused}");
}

#[test]
fn the_same_removal_asked_again_is_the_open_one_and_another_removal_is_told_what_waits() {
    let (rig, first) = setup();
    let (mut second, mut third) = (FakePhone::new("Tablet"), FakePhone::new("Watch"));
    rig.add(&mut second, Some(&first));
    rig.add(&mut third, Some(&first));
    let (_, token) = rig.session("demo", "remover");
    let before = rig.pushed.sent().len();
    let made = ok(rig.call("phone_remove", "", &token, json!({"phone": second.id})));
    let again = ok(rig.call("phone_remove", "", &token, json!({"phone": second.id})));
    assert_eq!(again["id"], made["id"], "the same removal again is the one that waits");
    let other = rig.call("phone_remove", "", &token, json!({"phone": third.id}));
    assert_eq!(code(&other), "denied", "{other}");
    assert!(message(&other).contains(made["id"].as_str().unwrap()) && message(&other).contains("Tell them to open the Poolhouse app"), "{other}");
    assert_eq!(rig.pushed.sent().len(), before + 1, "one push, for the one request");
}
