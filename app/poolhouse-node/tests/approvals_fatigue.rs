//! A request that is made again and again must not wear a person down: one item for the same request, a hold after two declines, a quiet
//! notice instead of a buzz, a block on the notification, a number to match for what is not routine, caps on what reaches the phone, and
//! a trust event when it goes on. The phone is made of keys and the node is the real one.

mod kit;

use kit::phone::{ask, FakePhone, Rig};
use kit::*;
use poolhouse_node::approvals::context::{AskFacts, Evidence, Facts};
use poolhouse_node::approvals::fatigue::{kind_of, requester};
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
    rig.node.lock().unwrap().fatigue.rules.backoff_ms = 0;
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
    n.approvals.all().iter().filter(|a| a.kind == kind && a.state == state).map(|a| json!({"id": a.id, "urgency": a.urgency, "retries": a.retries, "text": a.text})).collect()
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
    assert!(of_kind(&rig, "hold", "pending").is_empty(), "asking the same thing is not yet a reason to hold anyone");
}

#[test]
fn another_ask_or_another_session_is_another_item() {
    let (rig, _phone) = setup();
    let ((_, one), (_, two)) = (rig.session("demo", "agent-1"), rig.session("demo", "agent-2"));
    let a = ok(rig.ask("demo", &one, json!([ask("add_device", "*")]), json!({})));
    assert_ne!(ok(rig.ask("demo", &one, json!([ask("link_read", "a>b")]), json!({})))["id"], a["id"]);
    assert_ne!(ok(rig.ask("demo", &two, json!([ask("add_device", "*")]), json!({})))["id"], a["id"]);
}

#[test]
fn a_kind_that_was_declined_waits_before_it_is_asked_again() {
    let (rig, phone) = setup();
    rig.node.lock().unwrap().fatigue.rules.backoff_ms = 30_000;
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
    let soon = rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({}));
    assert_eq!(code(&soon), "quota", "{soon}");
    assert!(message(&soon).contains("declined a moment ago"), "{soon}");
    ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
}

#[test]
fn two_declines_hold_the_requester_and_what_it_asks_next_is_kept_back_with_one_quiet_notice() {
    let (rig, phone) = setup();
    let (name, token) = rig.session("demo", "agent-1");
    let a = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    ok(answer(&rig, &phone, a["id"].as_str().unwrap(), "deny"));
    let b = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!(b["state"], "pending", "one decline does not hold anyone");
    ok(answer(&rig, &phone, b["id"].as_str().unwrap(), "deny"));
    let pushed = rig.pushed.sent().len();
    let c = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!((c["state"].as_str(), c["urgency"].as_str()), (Some("held"), Some("silent")), "{c}");
    assert!(c.get("code").is_some(), "the agent is still told the number it would show");
    assert_eq!(rig.pushed.sent().len(), pushed + 1, "one push went out for the held request: the quiet notice");
    let notice = of_kind(&rig, "hold", "pending");
    assert_eq!((notice.len(), notice[0]["urgency"].as_str()), (1, Some("quiet")));
    assert!(notice[0]["text"].as_str().unwrap().contains(&format!("the session {name} on board demo")), "{}", notice[0]["text"]);
    let listed = rig.phone("POST", "list", &phone.token, json!({})).1["result"]["approvals"].to_string();
    assert!(listed.contains(notice[0]["id"].as_str().unwrap()) && !listed.contains(c["id"].as_str().unwrap()), "the person sees the notice, never the held request");
    let status = ok(rig.call("approval_status", "demo", &token, json!({"id": c["id"]})));
    assert_eq!(status["state"], "held");
    let fetched = rig.phone("POST", "get", &phone.token, json!({"id": c["id"]})).1;
    assert_eq!(code(&fetched), "invalid", "a phone that guesses the id of a held request reads nothing: {fetched}");
    let decided = rig.phone("POST", "decide", "", phone.decision(&c, "approve")).1;
    assert!(message(&decided).contains("being held back"), "and a decision on it is refused: {decided}");
}

#[test]
fn a_held_requester_cannot_buzz_however_it_asks() {
    let (rig, phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    for _ in 0..2 {
        let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
        ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
    }
    let held = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let pushed = rig.pushed.sent().len();
    for i in 0..200 {
        let again = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"note": format!("{i}")})));
        assert_eq!(again["id"], held["id"], "the same request is the same held item");
    }
    for i in 0..30 {
        let other = rig.ask("demo", &token, json!([ask("link_read", &format!("a>b{i}"))]), json!({}));
        assert!(other["ok"] == true || matches!(code(&other).as_str(), "quota"), "{other}");
    }
    assert_eq!(rig.pushed.sent().len(), pushed, "not one more push, quiet or loud");
    assert_eq!(of_kind(&rig, "hold", "pending").len(), 1, "one notice at a time");
    assert!(of_kind(&rig, "grants", "held").len() <= 8, "only as many are kept as the hold allows");
    assert!(of_kind(&rig, "grants", "pending").is_empty(), "nothing waits to be answered");
    assert!(rig.node.lock().unwrap().approvals.get(held["id"].as_str().unwrap()).unwrap().retries >= 200);
    let notices = rig.node.lock().unwrap().approvals.all().iter().filter(|a| a.kind == "hold").count();
    assert!(notices <= 12, "the notice is made again only when the count has doubled, not at every request: {notices}");
}

#[test]
fn an_approval_wipes_out_what_that_kind_was_declined_for_so_one_decline_before_it_does_not_hold() {
    let (rig, phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let a = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    ok(answer(&rig, &phone, a["id"].as_str().unwrap(), "deny"));
    let b = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!(ok(answer(&rig, &phone, b["id"].as_str().unwrap(), "approve_repeat"))["state"], "approved");
    let c = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    ok(answer(&rig, &phone, c["id"].as_str().unwrap(), "deny"));
    let d = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!(d["state"], "pending", "a requester the person approved in between is not held for two declines far apart: {d}");
}

/// A session registered by the main session ``lead`` (a subagent): its name and token.
fn child(rig: &Rig, lead_token: &str, id: &str) -> (String, String) {
    let made = ok(rig.call("register", "demo", lead_token, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": id})));
    (made["name"].as_str().unwrap().into(), made["token"].as_str().unwrap().into())
}

/// The person's decline of a fresh request for ``resource`` by the session ``token``.
fn declined(rig: &Rig, phone: &FakePhone, token: &str, resource: &str) {
    let made = ok(rig.ask("demo", token, json!([ask("link_read", resource)]), json!({})));
    ok(answer(rig, phone, made["id"].as_str().unwrap(), "deny"));
}

#[test]
fn five_declines_by_the_person_across_one_agents_sessions_hold_those_sessions_that_were_declined_and_no_innocent_one() {
    let (rig, phone) = setup();
    let (lead, lead_token) = rig.session("demo", "lead");
    let kids: Vec<(String, String)> = (0..5).map(|i| child(&rig, &lead_token, &format!("kid-{i}"))).collect();
    declined(&rig, &phone, &lead_token, "a>b");
    for (i, (_, token)) in kids.iter().take(4).enumerate() {
        declined(&rig, &phone, token, &format!("c>d{i}"));
    }
    let held = ok(rig.ask("demo", &kids[0].1, json!([ask("sync_now", "*")]), json!({})));
    assert_eq!(held["state"], "held", "a session of the agent that was itself declined is held with it");
    let notice = of_kind(&rig, "hold", "pending");
    assert_eq!(notice.len(), 1);
    assert!(notice[0]["text"].as_str().unwrap().contains(&format!("the agent led by {lead} on board demo")), "{}", notice[0]["text"]);
    let innocent = ok(rig.ask("demo", &kids[4].1, json!([ask("sync_now", "*")]), json!({})));
    assert_eq!(innocent["state"], "pending", "a sibling nobody declined is not held for what the others did: {innocent}");
    let (_, stranger) = rig.session("demo", "stranger");
    assert_eq!(ok(rig.ask("demo", &stranger, json!([ask("sync_now", "*")]), json!({})))["state"], "pending", "nor is another agent that says it is the same model");
}

#[test]
fn what_one_session_leaves_unanswered_never_holds_another_of_the_same_agent_or_the_same_model() {
    let (rig, _phone) = setup();
    let (_, lead) = rig.session("demo", "lead");
    for i in 0..7 {
        let (_, token) = child(&rig, &lead, &format!("sleeper-{i}"));
        let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
        rig.node.lock().unwrap().approvals.update(made["id"].as_str().unwrap(), |a| a.expires_ms = 1).unwrap();
    }
    let (_, sibling) = child(&rig, &lead, "sibling");
    let made = ok(rig.ask("demo", &sibling, json!([ask("sync_now", "*")]), json!({})));
    assert_eq!(made["state"], "pending", "seven timeouts of sessions of the same agent hold no other session of it: {made}");
    let (_, other) = rig.session("demo", "someone-else");
    assert_eq!(ok(rig.ask("demo", &other, json!([ask("sync_now", "*")]), json!({})))["state"], "pending", "nor another agent that says it is the same model");
    let (_, again) = child(&rig, &lead, "sleeper-0");
    assert_eq!(ok(rig.ask("demo", &again, json!([ask("link_read", "a>b")]), json!({})))["state"], "pending", "and a session with one timeout is not held");
}

#[test]
fn an_agent_that_runs_while_the_person_sleeps_is_held_by_its_timeouts_never_suspended() {
    let (rig, phone) = setup();
    rig.node.lock().unwrap().fatigue.rules.hold_steps_ms.fill(1);
    let (name, token) = rig.session("demo", "night-agent");
    let granted = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    ok(answer(&rig, &phone, granted["id"].as_str().unwrap(), "approve"));
    let holder = poolhouse_node::identity::Holder { board: "demo".into(), name };
    for round in 0..5 {
        let made = rig.ask("demo", &token, json!([ask("link_read", &format!("a>b{round}"))]), json!({}));
        assert!(made["ok"] == true, "round {round}: a timeout is never a reason to refuse the agent: {made}");
        if made["result"]["state"] == "pending" {
            rig.node.lock().unwrap().approvals.update(made["result"]["id"].as_str().unwrap(), |a| a.expires_ms = 1).unwrap();
        }
        std::thread::sleep(std::time::Duration::from_millis(8));
    }
    assert!(poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &holder, "sync_now", &poolhouse_node::grants::Scope::on("*")).unwrap(), "five rounds of timeouts take no grant");
}

#[test]
fn a_suspension_takes_the_grants_of_the_one_session_that_earned_it_and_no_other() {
    let (rig, phone) = setup();
    rig.node.lock().unwrap().fatigue.rules.hold_steps_ms.fill(1);
    let (innocent, innocent_token) = rig.session("demo", "innocent");
    let granted = ok(rig.ask("demo", &innocent_token, json!([ask("sync_now", "*")]), json!({})));
    ok(answer(&rig, &phone, granted["id"].as_str().unwrap(), "approve"));
    for i in 0..9 {
        let (_, token) = rig.session("demo", &format!("pest-{i}"));
        let made = ok(rig.ask("demo", &token, json!([ask("link_read", &format!("a>b{i}"))]), json!({})));
        ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
        std::thread::sleep(std::time::Duration::from_millis(8));
    }
    let holder = poolhouse_node::identity::Holder { board: "demo".into(), name: innocent };
    assert!(poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &holder, "sync_now", &poolhouse_node::grants::Scope::on("*")).unwrap(), "nine other sessions of the same model, declined, took nothing from this one");
    assert_eq!(ok(rig.ask("demo", &innocent_token, json!([ask("link_read", "x>y")]), json!({})))["state"], "pending", "and it is not held");
}

#[test]
fn withdrawing_a_request_the_person_was_pushed_counts_as_a_decline_and_withdrawing_one_never_pushed_does_not() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    for i in 0..2 {
        let made = ok(rig.ask("demo", &token, json!([ask("link_read", &format!("a>b{i}"))]), json!({})));
        ok(rig.call("approval_cancel", "demo", &token, json!({"id": made["id"]})));
    }
    let next = ok(rig.ask("demo", &token, json!([ask("link_read", "a>b9")]), json!({})));
    assert_eq!(next["state"], "held", "two requests withdrawn after the person was told hold the requester like two declines: {next}");

    let (quiet, other) = setup();
    told(&quiet, Facts { prompted: Some(false), ..Facts::unrecorded() });
    let (_, token) = quiet.session("demo", "agent-2");
    for i in 0..3 {
        let made = ok(quiet.ask("demo", &token, json!([ask("link_read", &format!("a>b{i}"))]), json!({})));
        assert_eq!(made["urgency"], "silent");
        ok(quiet.call("approval_cancel", "demo", &token, json!({"id": made["id"]})));
    }
    let again = ok(quiet.ask("demo", &token, json!([ask("link_read", "a>b9")]), json!({})));
    assert_eq!(again["state"], "pending", "a request nobody was told about costs nothing to withdraw: {again}");
    drop(other);
}

#[test]
fn one_session_cannot_spend_the_hours_pushes_and_silence_the_honest_ones() {
    let (rig, _phone) = setup();
    {
        let mut n = rig.node.lock().unwrap();
        n.fatigue.rules.hold_after = [100, 100, 100];
        n.fatigue.rules.pushes_per_hour = 4;
    }
    let (_, spender) = rig.session("demo", "spender");
    for i in 0..6 {
        let made = ok(rig.ask("demo", &spender, json!([ask("link_read", &format!("x>y{i}"))]), json!({})));
        ok(rig.call("approval_cancel", "demo", &spender, json!({"id": made["id"]})));
    }
    let (_, honest) = rig.session("demo", "honest");
    let made = ok(rig.ask("demo", &honest, json!([ask("sync_now", "*")]), json!({})));
    assert_eq!(made["urgency"], "urgent", "the spender used its own budget of pushes, not the hour's: {made}");
    assert_eq!(rig.pushed.sent().len(), 3 + 1 + 1, "three for the spender, one quiet notice that the rest wait in the list, one for the honest session");
}

#[test]
fn the_same_request_asked_again_soon_after_it_was_withdrawn_or_ran_out_does_not_push_again() {
    let (rig, _phone) = setup();
    rig.node.lock().unwrap().fatigue.rules.hold_after = [100, 100, 100];
    let (_, token) = rig.session("demo", "agent-1");
    let first = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    ok(rig.call("approval_cancel", "demo", &token, json!({"id": first["id"]})));
    let again = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({"note": "again"})));
    assert_eq!((again["state"].as_str(), again["urgency"].as_str()), (Some("pending"), Some("silent")), "{again}");
    assert_ne!(again["id"], first["id"]);
    assert_eq!(rig.pushed.sent().len(), 1, "the person was told once, not once for each time it was asked");
    let other = ok(rig.ask("demo", &token, json!([ask("sync_now", "*"), ask("link_read", "a>b")]), json!({})));
    assert_eq!(other["urgency"], "urgent", "a different request is a different request");
    rig.node.lock().unwrap().approvals.update(other["id"].as_str().unwrap(), |a| a.expires_ms = 1).unwrap();
    let ran_out = ok(rig.ask("demo", &token, json!([ask("sync_now", "*"), ask("link_read", "a>b")]), json!({"note": "once more"})));
    assert_eq!(ran_out["urgency"], "silent", "nor does one that ran out: {ran_out}");
}

#[test]
fn sharing_a_board_for_reading_is_sensitive_and_a_wildcard_is_flagged_at_every_level_but_not_where_it_means_nothing() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let read = ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})));
    assert_eq!(read["risk"], "sensitive", "another board reading this one is not routine");
    assert!(read.get("code").is_some() && read["challenge"]["context"]["pick"].as_array().unwrap().len() == 3, "so it has a number to match");
    let everything = ok(rig.ask("demo", &token, json!([ask("link_read", "*")]), json!({})));
    assert!(everything["text"].as_str().unwrap().contains("FLAG: It asks for link_read on anything: a whole class of things, not one."), "{everything}");
    assert_eq!(everything["risk"], "sensitive");
    let ending = ok(rig.ask("demo", &token, json!([ask("link_remove", "*")]), json!({})));
    assert_eq!(ending["risk"], "routine");
    assert!(ending["text"].as_str().unwrap().contains("FLAG: It asks for link_remove on anything: a whole class of things, not one."), "a wildcard is flagged at the routine level too: {ending}");
    assert!(ending.get("code").is_none(), "and a routine request still has no number");
    let sync = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    assert!(!sync["text"].as_str().unwrap().contains("a whole class of things"), "an action with no resource is not flagged for the star it carries: {sync}");
}

#[test]
fn quiet_notices_are_capped_too_so_many_holds_cannot_buzz_the_phone_either() {
    let (rig, phone) = setup();
    rig.node.lock().unwrap().fatigue.rules.quiet_per_hour = 1;
    for who in ["agent-1", "agent-2"] {
        let (_, token) = rig.session("demo", who);
        for _ in 0..2 {
            let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
            ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
        }
        assert_eq!(ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})))["state"], "held");
    }
    assert_eq!(of_kind(&rig, "hold", "pending").len(), 2, "both notices are in the list");
    assert_eq!(rig.pushed.sent().len(), 4 + 1, "four requests and one quiet notice reached the phone: the second notice did not");
}

#[test]
fn a_request_nobody_answered_counts_as_a_timeout_and_never_as_consent() {
    let (rig, _phone) = setup();
    let (name, token) = rig.session("demo", "agent-1");
    for _ in 0..2 {
        let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
        rig.node.lock().unwrap().approvals.update(made["id"].as_str().unwrap(), |a| a.expires_ms = 1).unwrap();
    }
    let held = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    assert_eq!(held["state"], "held", "two requests that ran out hold the requester like two declines");
    assert!(!poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &poolhouse_node::identity::Holder { board: "demo".into(), name }, "add_device", &poolhouse_node::grants::Scope::on("*")).unwrap(), "a timeout grants nothing");
    assert_eq!(of_kind(&rig, "grants", "expired").len(), 2);
}

#[test]
fn the_person_answers_the_notice_allow_once_keep_holding_or_block() {
    let (rig, phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    for _ in 0..2 {
        let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
        ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
    }
    let held = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    let notice = of_kind(&rig, "hold", "pending")[0]["id"].as_str().unwrap().to_string();
    let seen = rig.get(&phone, &notice);
    assert!(seen["text"].as_str().unwrap().starts_with("FLAG:"), "it opens on the screen first");
    let pushed = rig.pushed.sent().len();
    let done = ok(answer(&rig, &phone, &notice, "approve"));
    assert_eq!(done["state"], "approved");
    let released = rig.node.lock().unwrap().approvals.get(held["id"].as_str().unwrap()).cloned().unwrap();
    assert_eq!((released.state.as_str(), released.urgency.as_str()), ("pending", "urgent"), "Allow once shows the held request as a normal one");
    assert_eq!(rig.pushed.sent().len(), pushed + 1, "and it is pushed as one");
    let more = ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})));
    assert_eq!(more["state"], "held", "the hold goes on: one request was let through, not the requester");
    assert!(of_kind(&rig, "hold", "pending").is_empty(), "no second notice after the person answered one");
    assert_eq!(rig.pushed.sent().len(), pushed + 1, "and nothing more reached the phone");
}

#[test]
fn keep_holding_says_nothing_more_and_block_refuses_everything_from_the_session_for_a_day() {
    let (rig, phone) = setup();
    let ((_, token), (_, other)) = (rig.session("demo", "agent-1"), rig.session("demo", "agent-2"));
    for _ in 0..2 {
        let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
        ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
    }
    let held = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    let notice = of_kind(&rig, "hold", "pending")[0]["id"].as_str().unwrap().to_string();
    assert_eq!(answer(&rig, &phone, &notice, "deny")["result"]["state"], "denied");
    assert_eq!(rig.node.lock().unwrap().approvals.get(held["id"].as_str().unwrap()).unwrap().state, "held", "Keep holding leaves it kept back");
    let pushed = rig.pushed.sent().len();
    ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})));
    assert!(of_kind(&rig, "hold", "pending").is_empty() && rig.pushed.sent().len() == pushed, "and says nothing more");
    let blocking = ok(rig.ask("demo", &other, json!([ask("sync_now", "*")]), json!({})));
    for _ in 0..2 {
        let made = ok(rig.ask("demo", &other, json!([ask("add_device", "*")]), json!({})));
        ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
    }
    let held_two = ok(rig.ask("demo", &other, json!([ask("link_read", "x>y")]), json!({})));
    assert_eq!(held_two["state"], "held");
    let notice_two = of_kind(&rig, "hold", "pending")[0]["id"].as_str().unwrap().to_string();
    assert_eq!(answer(&rig, &phone, &notice_two, "block")["result"]["state"], "denied");
    let refused = rig.ask("demo", &other, json!([ask("sync_now", "*")]), json!({"note": "again"}));
    assert_eq!(code(&refused), "denied", "{refused}");
    assert!(message(&refused).contains("A person blocked this agent"), "{refused}");
    assert_eq!(rig.node.lock().unwrap().approvals.get(held_two["id"].as_str().unwrap()).unwrap().state, "expired", "what was kept back is let go");
    assert!(rig.node.lock().unwrap().approvals.get(blocking["id"].as_str().unwrap()).is_some());
    ok(rig.ask("demo", &token, json!([ask("link_read", "p>q")]), json!({})));
}

#[test]
fn deny_and_block_on_the_request_itself_blocks_that_session_and_no_other() {
    let (rig, phone) = setup();
    let ((name, token), (_, other)) = (rig.session("demo", "agent-1"), rig.session("demo", "agent-2"));
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let waiting = ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    assert!(seen["text"].as_str().unwrap().contains("If you deny and block, it also cannot ask again for 1 day."), "the text says what a block does");
    assert_eq!(seen["challenge"]["context"]["block_ms"], 86_400_000);
    let done = rig.phone("POST", "decide", "", phone.decision(&seen, "block")).1;
    assert_eq!(done["result"]["state"], "denied", "{done}");
    assert!(done["result"]["reason"].as_str().unwrap().contains("blocked"));
    let refused = rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({}));
    assert_eq!(code(&refused), "denied", "{refused}");
    assert_eq!(ok(rig.call("approval_status", "demo", &token, json!({"id": waiting["id"]})))["state"], "cancelled", "what the session still had waiting ends with the block");
    ok(rig.ask("demo", &other, json!([ask("sync_now", "*")]), json!({})));
    let grants = poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &poolhouse_node::identity::Holder { board: "demo".into(), name }, "add_device", &poolhouse_node::grants::Scope::on("*")).unwrap();
    assert!(!grants);
}

#[test]
fn a_block_is_offered_only_where_the_text_says_so() {
    let (rig, phone) = setup();
    let secret = poolhouse_node::phone::enrol::make_offer(rig.dir.path(), kit::phone::now()).unwrap();
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
    let batched = rig.ask("demo", &token, json!([ask("add_device", "*"), ask("link_read", "a>b")]), json!({"note": "with another"}));
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
fn a_request_nobody_told_the_agent_to_make_is_labelled_listed_and_never_pushed() {
    let (rig, phone) = setup();
    told(&rig, Facts { prompted: Some(false), ..Facts::unrecorded() });
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    assert_eq!(made["urgency"], "silent");
    assert!(made["text"].as_str().unwrap().contains("FLAG: You gave this agent no instruction to do this; it asked on its own."), "{made}");
    assert_eq!(made["challenge"]["context"]["prompted"], "no");
    assert!(rig.pushed.sent().is_empty(), "no push with urgency for a request nobody asked for");
    let listed = rig.phone("POST", "list", &phone.token, json!({})).1["result"]["approvals"].to_string();
    assert!(listed.contains(made["id"].as_str().unwrap()), "it waits in the list");
    assert_eq!(answer(&rig, &phone, made["id"].as_str().unwrap(), "approve")["result"]["state"], "approved", "and can still be answered from there");
    told(&rig, Facts { prompted: Some(true), ..Facts::unrecorded() });
    ok(rig.ask("demo", &token, json!([ask("link_read", "a>b")]), json!({})));
    assert_eq!(rig.pushed.sent().len(), 1, "a request the person just asked for pushes as it always did");
}

#[test]
fn what_the_node_cannot_tell_is_not_unprompted_so_the_usual_request_feels_as_it_did() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    assert_eq!((made["urgency"].as_str(), made["challenge"]["context"]["prompted"].as_str()), (Some("urgent"), Some("unknown")));
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
    let pending = ok(rig.call("approval_pending", "", &token, json!({})));
    assert!(!pending.to_string().contains(&format!("\"code\":\"{shown}\"")), "no session reads another's number");
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
fn three_holds_in_a_day_suspend_the_session_its_grants_stop_and_the_pool_is_told() {
    let (rig, phone) = setup();
    {
        let mut n = rig.node.lock().unwrap();
        n.fatigue.rules.hold_steps_ms.fill(1);
    }
    let (name, token) = rig.session("demo", "agent-1");
    let granted = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
    ok(answer(&rig, &phone, granted["id"].as_str().unwrap(), "approve"));
    let holder = poolhouse_node::identity::Holder { board: "demo".into(), name: name.clone() };
    assert!(poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &holder, "sync_now", &poolhouse_node::grants::Scope::on("*")).unwrap());
    // Two declines hold it; each decline after a hold has ended holds it again, for the second time and then the third, which suspends.
    for round in 0..4 {
        let made = ok(rig.ask("demo", &token, json!([ask("link_read", &format!("a>b{round}"))]), json!({})));
        assert_eq!(made["state"], "pending", "round {round}: {made}");
        ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
        std::thread::sleep(std::time::Duration::from_millis(8));
    }
    let refused = rig.ask("demo", &token, json!([ask("link_read", "z>z")]), json!({}));
    assert_eq!(code(&refused), "denied", "{refused}");
    assert!(message(&refused).contains("suspended"), "{refused}");
    assert!(!poolhouse_node::grants::holds(&mut rig.node.lock().unwrap(), &holder, "sync_now", &poolhouse_node::grants::Scope::on("*")).unwrap(), "a suspended session holds no grant");
    let seen: Vec<String> = events(&rig).into_iter().map(|e| e.0).collect();
    for event in ["approval.held", "approval.bombing", "approval.suspended"] {
        assert!(seen.iter().any(|e| e == event), "{event} was not written to the pool board: {seen:?}");
    }
    let key = format!("session:demo/{name}");
    assert!(rig.node.lock().unwrap().fatigue.penalty(&key) >= 30, "the trust ledger has its penalty to read");
    assert!(of_kind(&rig, "hold", "pending").iter().any(|n| n["text"].as_str().unwrap().contains("suspended")), "and the person is told, quietly");
}

#[test]
fn past_the_cap_on_what_reaches_the_phone_in_an_hour_the_rest_wait_in_the_list_with_one_summary() {
    let (rig, phone) = setup();
    rig.node.lock().unwrap().fatigue.rules.pushes_per_hour = 3;
    let mut ids = Vec::new();
    for i in 0..8 {
        let (_, token) = rig.session("demo", &format!("agent-{i}"));
        ids.push(ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})))["id"].as_str().unwrap().to_string());
    }
    let loud = rig.pushed.sent().len();
    assert_eq!(loud, 3 + 1, "three requests were pushed, then one quiet summary and nothing else: {loud}");
    assert_eq!(of_kind(&rig, "hold", "pending").len(), 1);
    assert!(of_kind(&rig, "hold", "pending")[0]["text"].as_str().unwrap().contains("wait in your list"), "{}", of_kind(&rig, "hold", "pending")[0]["text"]);
    let listed = rig.phone("POST", "list", &phone.token, json!({})).1["result"]["approvals"].to_string();
    assert!(ids.iter().all(|id| listed.contains(id.as_str())), "every request is still in the list");
    assert_eq!(of_kind(&rig, "grants", "pending").iter().filter(|a| a["urgency"] == "silent").count(), 5);
    let notice = of_kind(&rig, "hold", "pending")[0]["id"].as_str().unwrap().to_string();
    assert_eq!(answer(&rig, &phone, &notice, "approve")["result"]["state"], "approved", "the notice only closes");
    assert_eq!(code(&answer(&rig, &phone, &notice, "block")), "denied");
}

#[test]
fn one_requester_cannot_have_more_than_three_requests_interrupt_at_once() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    for i in 0..5 {
        ok(rig.ask("demo", &token, json!([ask("link_read", &format!("a>b{i}"))]), json!({})));
    }
    assert_eq!(rig.pushed.sent().len(), 3 + 1, "three pushes and one quiet summary");
}

#[test]
fn a_request_from_a_device_outside_the_pool_never_pushes_and_says_so() {
    let (rig, _phone) = setup();
    let (_, token) = rig.session("demo", "agent-1");
    let stranger = "ab".repeat(32);
    let mut params = Map::new();
    params.insert("asks".into(), json!([ask("sync_now", "*")]));
    let made = request_from(&mut rig.node.lock().unwrap(), &stranger, "demo", &token, &params).unwrap();
    assert_eq!(made["urgency"], "silent");
    assert!(made["text"].as_str().unwrap().contains("FLAG: This request came from a device that is not a member of your pool."), "{made}");
    assert!(rig.pushed.sent().is_empty(), "a stranger's request never reaches the phone as a push");
    let own = rig.device();
    let mut other = Map::new();
    other.insert("asks".into(), json!([ask("link_read", "a>b")]));
    assert_eq!(request_from(&mut rig.node.lock().unwrap(), &own, "demo", &token, &other).unwrap()["urgency"], "urgent");
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
    let entry = || poolhouse_node::approvals::challenge::grant_entries(&approved, &node.cert.fingerprint(), approved.decided_ms, &phone.fingerprint(), decision["sig"].as_str().unwrap()).remove(0);
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
fn the_ledger_survives_a_restart_so_a_hold_is_not_forgotten() {
    let (rig, phone) = setup();
    let (name, token) = rig.session("demo", "agent-1");
    for _ in 0..2 {
        let made = ok(rig.ask("demo", &token, json!([ask("sync_now", "*")]), json!({})));
        ok(answer(&rig, &phone, made["id"].as_str().unwrap(), "deny"));
    }
    drop(rig.node);
    let mut node = poolhouse_node::node::Node::open(rig.dir.path()).unwrap();
    let who = requester("demo", &name, &name);
    let now = node.now_ms();
    assert!(matches!(node.fatigue.standing(&who, &kind_of(&["sync_now"]), now), poolhouse_node::approvals::fatigue::Standing::Held { .. }));
}
