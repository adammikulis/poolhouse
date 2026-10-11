mod kit;

use kit::*;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tempfile::tempdir;

const T0: u64 = 1_000_000_000;

fn clocked(node: &mut Node) -> Arc<AtomicU64> {
    let t = Arc::new(AtomicU64::new(T0));
    let c = t.clone();
    node.leases.clock = Box::new(move || c.load(Ordering::SeqCst));
    t
}

fn advance(t: &Arc<AtomicU64>, secs: u64) {
    t.fetch_add(secs * 1000, Ordering::SeqCst);
}

/// A lead, a worker registered under it holding a model slot, and an independent session.
struct Team {
    lead: (String, String),
    kid: (String, String),
    model: String,
    other: (String, String),
}

fn team(n: &mut Node) -> Team {
    let lead = session(n, "demo", "lead");
    allow(n, "demo", &lead.1, &lead.0, &["task_authority"]);
    let made = ok(req(n, "register", "demo", &lead.1, json!({"model": "qwen3", "harness": "pi", "session": "kid"})));
    let kid = (made["name"].as_str().unwrap().to_string(), made["token"].as_str().unwrap().to_string());
    let model = ok(req(n, "lease_acquire", "demo", &kid.1, json!({"resources": [{"type": "model_slot", "model": "qwen3"}], "ttl_s": 5000})))["id"].as_str().unwrap().to_string();
    Team { lead, kid, model, other: session(n, "demo", "other") }
}

fn spec(key: &str) -> Value {
    json!({"title": "Inspect native simulation", "description": "Collect a reproducible replay.", "acceptance": ["Replay passes"], "source_key": key})
}

fn make(n: &mut Node, token: &str, extra: Value) -> Value {
    let mut p = spec("");
    for (k, v) in extra.as_object().unwrap() {
        p[k] = v.clone();
    }
    req(n, "task_create", "demo", token, p)
}

fn id_of(v: Value) -> String {
    ok(v)["id"].as_str().unwrap().to_string()
}

fn assign(n: &mut Node, t: &Team, task: &str) -> String {
    let got = ok(req(n, "task_assign", "demo", &t.lead.1, json!({"task": task, "worker": t.kid.0, "lease": t.model, "capabilities": ["coding"]})));
    got["allocation_id"].as_str().unwrap().to_string()
}

fn claim(n: &mut Node, t: &Team, task: &str, allocation: &str) -> Value {
    req(n, "task_claim", "demo", &t.kid.1, json!({"task": task, "assign": allocation}))
}

fn proposal() -> Value {
    json!({"artifacts": {"replay.json": "a".repeat(64)}, "checks": [{"name": "Worker claims tests", "passed": true}], "summary": "Native replay proposed",
           "provenance": {"commit": "abc123", "environment": "native-python", "model": "qwen", "runtime": "Agents SDK"}})
}

fn submit(n: &mut Node, t: &Team, task: &str) -> Value {
    let mut p = proposal();
    p["task"] = json!(task);
    ok(req(n, "task_submit", "demo", &t.kid.1, p))
}

fn accepted() -> Value {
    json!({"accepted": true, "reason": "Independent native replay passed", "checks": [{"name": "Replay passes", "passed": true}]})
}

fn judge(n: &mut Node, token: &str, task: &str, decision: Value) -> Value {
    let mut p = decision;
    p["task"] = json!(task);
    req(n, "task_review", "demo", token, p)
}

fn get(n: &mut Node, token: &str, task: &str) -> Value {
    ok(req(n, "task_get", "demo", token, json!({"task": task})))
}

/// A task claimed and proposed, awaiting review.
fn proposed(n: &mut Node, t: &Team, key: &str) -> String {
    let task = id_of(make(n, &t.lead.1, json!({"source_key": key, "assignees": [t.kid.0]})));
    let a = assign(n, t, &task);
    ok(claim(n, t, &task, &a));
    submit(n, t, &task);
    task
}

/// The person takes the lead's task authority back.
fn lose_authority(n: &mut Node, t: &Team) {
    revoke(n, "demo", &t.lead.1, &t.lead.0, "task_authority");
}

#[test]
fn a_source_key_makes_one_task_and_a_changed_request_under_it_is_refused() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let first = id_of(make(&mut n, &t.lead.1, json!({"source_key": "repo:demo/sim:issue:42"})));
    assert_eq!(id_of(make(&mut n, &t.lead.1, json!({"source_key": "repo:demo/sim:issue:42"}))), first, "an identical request names the same task");
    assert_eq!(code(&make(&mut n, &t.lead.1, json!({"source_key": "repo:demo/sim:issue:42", "title": "Changed request"}))), "invalid");
    assert_eq!(code(&make(&mut n, &t.other.1, json!({"source_key": "repo:demo/sim:issue:42"}))), "invalid", "another creator's key is not reused");
    let listed = ok(req(&mut n, "task_list", "demo", &t.other.1, json!({})));
    assert_eq!(listed["tasks"].as_array().unwrap().len(), 1);
    assert_eq!((listed["tasks"][0]["state"].clone(), listed["tasks"][0]["created_by"].clone()), (json!("queued"), json!(t.lead.0)));
    for bad in [json!({"acceptance": []}), json!({"title": ""}), json!({"limits": {"max_retries": 11}}), json!({"limits": {"turns": 1}}), json!({"deps": ["task-99"]}), json!({"assignees": ["nobody"]})] {
        assert_eq!(code(&make(&mut n, &t.lead.1, bad.clone())), "invalid", "{bad}");
    }
}

#[test]
fn a_task_is_claimed_on_a_live_model_lease_and_a_proposal_waits_for_an_independent_review() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    assert_eq!(code(&req(&mut n, "task_assign", "demo", &t.other.1, json!({"task": task, "worker": t.kid.0, "lease": t.model}))), "denied", "only the worker's parent assigns it");
    assert_eq!(code(&req(&mut n, "task_assign", "demo", &t.lead.1, json!({"task": task, "worker": t.kid.0, "lease": "nonesuch"}))), "denied");
    let a = assign(&mut n, &t, &task);
    assert_eq!(code(&claim(&mut n, &t, &task, "nonesuch")), "denied");
    let lease = ok(claim(&mut n, &t, &task, &a));
    assert_eq!((lease["active"].clone(), lease["worker"].clone(), lease["resource"]["model"].clone()), (json!(true), json!(t.kid.0), json!("qwen3")));
    assert_eq!(code(&claim(&mut n, &t, &task, &a)), "denied", "a claimed task is not claimed again");
    let cp = ok(req(&mut n, "task_checkpoint", "demo", &t.kid.1, json!({"task": task, "summary": "Replay started", "commit": "abc123"})));
    assert_eq!(cp["summary"], "Replay started");
    assert_eq!(code(&req(&mut n, "task_checkpoint", "demo", &t.lead.1, json!({"task": task, "summary": "not the worker"}))), "denied");
    let p = submit(&mut n, &t, &task);
    assert_eq!(p["provenance"]["actor"], json!(t.kid.0), "the node states who and what ran it");
    assert_eq!((p["provenance"]["commit"].clone(), p["provenance"]["model_source"].clone()), (json!("abc123"), json!("verified-serving-resource")));
    let held = get(&mut n, &t.lead.1, &task);
    assert_eq!((held["state"].clone(), held["lease"]["active"].clone()), (json!("review"), json!(false)));
    assert_eq!(code(&judge(&mut n, &t.kid.1, &task, accepted())), "denied", "a worker does not review itself");
    assert_eq!(code(&judge(&mut n, &t.other.1, &task, json!({"accepted": true, "reason": "r", "checks": proposal()["checks"]}))), "invalid", "a worker's own claims do not satisfy acceptance");
    let review = ok(judge(&mut n, &t.other.1, &task, accepted()));
    assert_eq!((review["verifier"].clone(), review["proposal_id"].clone(), review["accepted"].clone()), (json!(t.other.0), p["id"].clone(), json!(true)));
    assert_eq!(review["proposal_hash"], p["proposal_hash"]);
    let done = get(&mut n, &t.lead.1, &task);
    assert_eq!(done["state"], "completed");
    assert_eq!(ok(req(&mut n, "task_list", "demo", &t.lead.1, json!({})))["metrics"]["verified_outcomes"], 1);
    assert_eq!(code(&judge(&mut n, &t.other.1, &task, accepted())), "invalid", "a decided task takes no second review");
}

#[test]
fn a_task_with_a_scope_stays_accepted_until_its_landing_completes_it() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    ok(req(&mut n, "task_scope", "demo", &t.lead.1, json!({"task": task, "scope": {"project": "/w/task-1", "branch": "lead/task-1", "baseline_commit": "b".repeat(40), "state": "reserved"}})));
    let a = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &a));
    submit(&mut n, &t, &task);
    ok(judge(&mut n, &t.other.1, &task, accepted()));
    let held = get(&mut n, &t.lead.1, &task);
    assert_eq!((held["state"].clone(), held["scope"]["branch"].clone()), (json!("accepted"), json!("lead/task-1")));
    let metrics = ok(req(&mut n, "task_list", "demo", &t.lead.1, json!({})));
    assert_eq!((metrics["metrics"]["verified_outcomes"].clone(), metrics["overview"]["awaiting_integration"].clone()), (json!(0), json!(1)));
    let outsider = session(&mut n, "demo", "outsider");
    lose_authority(&mut n, &t);
    assert_eq!(code(&req(&mut n, "task_complete", "demo", &outsider.1, json!({"task": task, "landed": "f".repeat(40)}))), "denied", "a session with no hand in the task records no landing");
}

#[test]
fn the_overview_counts_states_and_a_workers_own_progress_claims_never_make_an_estimate() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let clock = clocked(&mut n);
    let t = team(&mut n);
    let queued = id_of(make(&mut n, &t.lead.1, json!({"source_key": "k:queued"})));
    let waiting = id_of(make(&mut n, &t.lead.1, json!({"source_key": "k:waiting", "deps": [queued]})));
    let working = id_of(make(&mut n, &t.lead.1, json!({"source_key": "k:working"})));
    let a = assign(&mut n, &t, &working);
    ok(claim(&mut n, &t, &working, &a));
    ok(req(&mut n, "task_checkpoint", "demo", &t.kid.1, json!({"task": working, "summary": "Replay started", "progress": "99 percent complete"})));
    advance(&clock, 10);
    let listed = ok(req(&mut n, "task_list", "demo", &t.other.1, json!({})));
    let count = |key: &str| listed["overview"][key].clone();
    assert_eq!((count("total"), count("remaining"), count("queued"), count("running"), count("blocked"), count("review"), count("done")),
               (json!(3), json!(3), json!(2), json!(1), json!(0), json!(0), json!(0)));
    assert_eq!(listed["overview"]["eta_seconds"], Value::Null);
    assert_eq!(listed["overview"]["workers"][0]["id"], json!(t.kid.0));
    assert_eq!((listed["overview"]["workers"][0]["active"].clone(), listed["overview"]["workers"][0]["heartbeat_age_seconds"].clone()), (json!(true), json!(10)));
    let find = |id: &str| listed["tasks"].as_array().unwrap().iter().find(|x| x["id"] == id).unwrap().clone();
    assert_eq!(find(&working)["activity"]["eta_seconds"], Value::Null, "what a worker says of its progress is not a measurement");
    assert_eq!(find(&waiting)["activity"]["waiting_on"][0]["id"], json!(queued));
    submit(&mut n, &t, &working);
    ok(judge(&mut n, &t.other.1, &working, json!({"accepted": false, "reason": "An extra check failed", "checks": [{"name": "Replay passes", "passed": true}, {"name": "Extra", "passed": false}]})));
    let after = get(&mut n, &t.lead.1, &working);
    assert_eq!((after["activity"]["verified_checks"]["passed"].clone(), after["activity"]["verified_checks"]["total"].clone()), (json!(1), json!(2)));
}

#[test]
fn followers_hear_each_state_change_and_a_stranger_cannot_follow_for_a_worker() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    assert_eq!(ok(req(&mut n, "task_watch", "demo", &t.lead.1, json!({"task": task, "on": true})))["subscribed"], true);
    ok(req(&mut n, "task_watch", "demo", &t.other.1, json!({"task": task, "on": true})));
    let inbox = |n: &mut Node, token: &str| texts(&ok(req(n, "read", "demo", token, json!({"inbox": true})))["entries"]);
    let heard = inbox(&mut n, &t.lead.1);
    assert_eq!(heard.len(), 1);
    assert!(heard[0].contains(&format!("{} subscribed task {task}", t.other.0)), "{heard:?}");
    assert_eq!(code(&req(&mut n, "task_watch", "demo", &t.other.1, json!({"task": task, "on": true, "who": t.kid.0}))), "denied");
    assert_eq!(ok(req(&mut n, "task_watch", "demo", &t.lead.1, json!({"task": task, "on": true, "who": t.kid.0})))["subscriber"], json!(t.kid.0), "a parent follows for its worker");
    ok(req(&mut n, "task_watch", "demo", &t.kid.1, json!({"task": task, "on": false})));
    let a = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &a));
    let heard = inbox(&mut n, &t.lead.1);
    assert!(heard.iter().any(|m| m.contains("is working")), "{heard:?}");
    ok(req(&mut n, "task_watch", "demo", &t.other.1, json!({"task": task, "on": false})));
    assert!(inbox(&mut n, &t.lead.1).iter().any(|m| m.contains("unsubscribed task")));
    ok(req(&mut n, "task_watch", "demo", &t.lead.1, json!({"task": task, "on": false})));
    submit(&mut n, &t, &task);
    let after = ok(req(&mut n, "task_watchers", "demo", &t.lead.1, json!({"task": task})));
    assert_eq!(after["watchers"], json!([]));
    assert_eq!(inbox(&mut n, &t.other.1).iter().filter(|m| m.contains("is review")).count(), 0, "one who stopped following hears nothing more");
}

#[test]
fn a_second_claim_loses_and_a_released_model_lease_ends_the_workers_authority() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    let a = assign(&mut n, &t, &task);
    let (one, two) = (claim(&mut n, &t, &task, &a), claim(&mut n, &t, &task, &a));
    assert_eq!([one["ok"] == true, two["ok"] == true].iter().filter(|x| **x).count(), 1);
    ok(req(&mut n, "lease_release", "demo", &t.kid.1, json!({"id": t.model})));
    let late = req(&mut n, "task_checkpoint", "demo", &t.kid.1, json!({"task": task, "summary": "Cannot bypass released resource"}));
    assert_eq!(code(&late), "denied");
    assert!(late["error"]["message"].as_str().unwrap().contains("managed grant"), "{late}");
    assert_eq!(get(&mut n, &t.lead.1, &task)["checkpoints"], json!([]));
}

#[test]
fn review_authority_follows_the_mode_the_board_runs_in() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let task = proposed(&mut n, &t, "open");
    assert_eq!(ok(req(&mut n, "task_mode", "demo", &t.lead.1, json!({})))["mode"], "open");
    assert_eq!(ok(judge(&mut n, &t.other.1, &task, accepted()))["verifier"], json!(t.other.0), "in open mode any other member reviews");
    ok(req(&mut n, "task_mode", "demo", &t.lead.1, json!({"mode": "strict"})));
    assert_eq!(code(&req(&mut n, "task_mode", "demo", &t.lead.1, json!({"mode": "loose"}))), "invalid");
    lose_authority(&mut n, &t);
    assert_eq!(code(&req(&mut n, "task_mode", "demo", &t.lead.1, json!({"mode": "open"}))), "denied", "only a person sets the mode");
    let strict = proposed(&mut n, &t, "strict");
    for (who, token) in [("self", &t.kid.1), ("unrelated", &t.other.1)] {
        let refused = judge(&mut n, token, &strict, accepted());
        assert_eq!(code(&refused), "denied", "{who}");
    }
    assert_eq!(code(&judge(&mut n, "", &strict, accepted())), "denied", "a missing token reviews nothing");
    assert_eq!(get(&mut n, &t.lead.1, &strict)["state"], "review");
    assert_eq!(ok(judge(&mut n, &t.lead.1, &strict, accepted()))["verifier"], json!(t.lead.0), "the creator who is the worker's parent reviews in strict mode");
    assert_eq!(ok(req(&mut n, "task_list", "demo", &t.lead.1, json!({})))["metrics"]["verified_outcomes"], 2);
}

#[test]
fn an_unassigned_task_is_open_to_a_peers_worker_only_while_the_board_is_open_or_a_person_made_it() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let by_peer = id_of(make(&mut n, &t.other.1, json!({"source_key": "peers"})));
    let by_parent = id_of(make(&mut n, &t.lead.1, json!({"source_key": "parents"})));
    let ask = |n: &mut Node, task: &str| req(n, "task_assign", "demo", &t.lead.1, json!({"task": task, "worker": t.kid.0, "lease": t.model}));
    ok(ask(&mut n, &by_peer));
    ok(req(&mut n, "task_mode", "demo", &t.lead.1, json!({"mode": "strict"})));
    lose_authority(&mut n, &t);
    let refused = ask(&mut n, &by_peer);
    assert_eq!(code(&refused), "denied");
    assert!(refused["error"]["message"].as_str().unwrap().contains("eligible"), "{refused}");
    ok(ask(&mut n, &by_parent));
}

#[test]
fn a_designated_reviewer_judges_in_strict_mode_and_the_worker_never_does() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    ok(req(&mut n, "task_mode", "demo", &t.lead.1, json!({"mode": "strict"})));
    lose_authority(&mut n, &t);
    let task = id_of(make(&mut n, &t.lead.1, json!({"source_key": "peer", "reviewers": [t.other.0]})));
    let a = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &a));
    submit(&mut n, &t, &task);
    let stranger = session(&mut n, "demo", "stranger");
    assert_eq!(code(&judge(&mut n, &stranger.1, &task, accepted())), "denied");
    assert_eq!(ok(judge(&mut n, &t.other.1, &task, accepted()))["verifier"], json!(t.other.0));
}

#[test]
fn a_worker_named_its_own_reviewer_still_does_not_review_its_task() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    ok(req(&mut n, "task_mode", "demo", &t.lead.1, json!({"mode": "strict"})));
    let task = id_of(make(&mut n, &t.lead.1, json!({"source_key": "self", "assignees": [t.kid.0], "reviewers": [t.kid.0]})));
    let a = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &a));
    submit(&mut n, &t, &task);
    assert_eq!(code(&judge(&mut n, &t.kid.1, &task, accepted())), "denied");
    assert_eq!(get(&mut n, &t.lead.1, &task)["state"], json!("review"));
}

#[test]
fn another_workers_allocation_cannot_claim_a_task() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let made = ok(req(&mut n, "register", "demo", &t.lead.1, json!({"model": "qwen4", "harness": "pi", "session": "kid2"})));
    let kid2 = made["token"].as_str().unwrap().to_string();
    ok(req(&mut n, "lease_acquire", "demo", &kid2, json!({"resources": [{"type": "model_slot", "model": "qwen4"}], "ttl_s": 5000})));
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    let a = assign(&mut n, &t, &task);
    let stolen = req(&mut n, "task_claim", "demo", &kid2, json!({"task": task, "assign": a}));
    assert_eq!(code(&stolen), "denied", "{stolen}");
    assert_eq!(get(&mut n, &t.lead.1, &task)["state"], json!("queued"));
    ok(claim(&mut n, &t, &task, &a));
}

#[test]
fn dependencies_the_retry_budget_and_an_infrastructure_outcome() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let first = proposed(&mut n, &t, "first");
    let dependent = id_of(make(&mut n, &t.lead.1, json!({"source_key": "later", "deps": [first]})));
    let a = assign(&mut n, &t, &dependent);
    assert_eq!(code(&claim(&mut n, &t, &dependent, &a)), "denied", "a dependency under review is not accepted");
    ok(judge(&mut n, &t.other.1, &first, accepted()));
    ok(claim(&mut n, &t, &dependent, &a));
    ok(req(&mut n, "task_checkpoint", "demo", &t.kid.1, json!({"task": dependent, "summary": "Replay started"})));
    let mut p = proposal();
    p["task"] = json!(dependent);
    p["checks"] = json!([{"name": "Replay passes", "passed": false}]);
    ok(req(&mut n, "task_submit", "demo", &t.kid.1, p));
    let outcome = ok(judge(&mut n, &t.other.1, &dependent, json!({"accepted": false, "outcome": "blocked_infrastructure", "reason": "Native simulator unavailable",
        "checks": [{"name": "Replay passes", "passed": false}]})));
    assert_eq!(outcome["outcome"], "blocked_infrastructure");
    let held = get(&mut n, &t.lead.1, &dependent);
    assert_eq!((held["state"].clone(), held["failures"].clone()), (json!("blocked"), json!(0)));
    assert_eq!(code(&judge(&mut n, &t.other.1, &dependent, json!({"accepted": true, "outcome": "rejected", "reason": "x", "checks": [{"name": "a", "passed": true}]}))), "invalid");
    let strict = id_of(make(&mut n, &t.lead.1, json!({"source_key": "once", "limits": {"max_retries": 0}})));
    let b = assign(&mut n, &t, &strict);
    ok(claim(&mut n, &t, &strict, &b));
    submit(&mut n, &t, &strict);
    ok(judge(&mut n, &t.other.1, &strict, json!({"accepted": false, "reason": "wrong", "checks": [{"name": "Replay passes", "passed": false}]})));
    let spent = get(&mut n, &t.lead.1, &strict);
    assert_eq!((spent["state"].clone(), spent["failures"].clone()), (json!("rejected"), json!(1)));
    assert_eq!(code(&claim(&mut n, &t, &strict, &b)), "denied", "a rejected task is not taken again");
}

#[test]
fn a_missing_capability_or_model_refuses_the_claim_and_a_lease_not_renewed_expires() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let clock = clocked(&mut n);
    let t = team(&mut n);
    let needs = id_of(make(&mut n, &t.lead.1, json!({"source_key": "vision", "capabilities": ["vision"]})));
    let a = assign(&mut n, &t, &needs);
    assert_eq!(code(&claim(&mut n, &t, &needs, &a)), "denied");
    assert_eq!(get(&mut n, &t.lead.1, &needs)["state"], "queued");
    let other_model = id_of(make(&mut n, &t.lead.1, json!({"source_key": "model", "limits": {"model": "llama"}})));
    let c = assign(&mut n, &t, &other_model);
    assert_eq!(code(&claim(&mut n, &t, &other_model, &c)), "denied");
    let task = id_of(make(&mut n, &t.lead.1, json!({"source_key": "live"})));
    let b = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &b));
    advance(&clock, 60);
    ok(req(&mut n, "task_beat", "demo", &t.kid.1, json!({"task": task})));
    advance(&clock, 121);
    let late = req(&mut n, "task_beat", "demo", &t.kid.1, json!({"task": task}));
    assert_eq!(code(&late), "denied");
    assert!(late["error"]["message"].as_str().unwrap().contains("lease expired"));
    let held = get(&mut n, &t.lead.1, &task);
    assert_eq!((held["lease"]["active"].clone(), held["lease"]["deadline"].as_u64().unwrap() < T0 + 181_000), (json!(false), true));
}

#[test]
fn an_expired_claim_takes_a_bounded_independent_recovery_and_a_blocked_one_an_explicit_resume() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let clock = clocked(&mut n);
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    let a = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &a));
    assert_eq!(code(&req(&mut n, "task_ready", "demo", &t.other.1, json!({"task": task, "reason": "premature", "expired": true}))), "denied");
    advance(&clock, 121);
    assert_eq!(code(&claim(&mut n, &t, &task, &a)), "denied", "an expired claim still stands until it is recovered");
    ok(req(&mut n, "task_ready", "demo", &t.other.1, json!({"task": task, "reason": "Previous worker heartbeat expired", "expired": true})));
    ok(claim(&mut n, &t, &task, &a));
    ok(req(&mut n, "task_block", "demo", &t.kid.1, json!({"task": task, "reason": "Approval pending"})));
    assert_eq!(code(&claim(&mut n, &t, &task, &a)), "denied");
    assert_eq!(code(&req(&mut n, "task_ready", "demo", &t.kid.1, json!({"task": task, "reason": "Self approved", "expired": false}))), "denied");
    advance(&clock, 1);
    ok(req(&mut n, "task_ready", "demo", &t.other.1, json!({"task": task, "reason": "Approved continuation", "expired": false})));
    ok(claim(&mut n, &t, &task, &a));
    let held = get(&mut n, &t.lead.1, &task);
    let moves: Vec<&str> = held["checkpoints"].as_array().unwrap().iter().map(|c| c["transition"].as_str().unwrap()).collect();
    assert_eq!((moves, held["failures"].clone()), (vec!["recover", "resume"], json!(1)));
}

#[test]
fn a_blockage_counts_its_elapsed_time_and_releases_the_lease() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let clock = clocked(&mut n);
    let t = team(&mut n);
    lose_authority(&mut n, &t);
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    let a = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &a));
    assert_eq!(code(&req(&mut n, "task_block", "demo", &t.other.1, json!({"task": task, "reason": "not mine"}))), "denied", "a stranger blocks nothing");
    ok(req(&mut n, "task_block", "demo", &t.kid.1, json!({"task": task, "reason": "Waiting for native simulator"})));
    advance(&clock, 30);
    let listed = ok(req(&mut n, "task_list", "demo", &t.lead.1, json!({})));
    assert_eq!((listed["metrics"]["blocked_seconds"].clone(), listed["metrics"]["verified_outcomes"].clone(), listed["metrics"]["accepted_artifacts"].clone()), (json!(30), json!(0), json!(0)));
    assert_eq!(get(&mut n, &t.lead.1, &task)["lease"]["active"], false);
    let held = ok(req(&mut n, "claims", "demo", &t.lead.1, json!({})));
    assert_eq!(held["claims"].as_array().unwrap().len(), 0, "the task claim is the node's, never listed or taken by hand");
    assert_eq!(code(&req(&mut n, "claim", "demo", &t.lead.1, json!({"kind": "task", "key": "x"}))), "invalid");
}

#[test]
fn a_wall_time_budget_ends_a_workers_authority() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let clock = clocked(&mut n);
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.lead.1, json!({"limits": {"max_wall_s": 100}})));
    let a = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &a));
    advance(&clock, 90);
    ok(req(&mut n, "task_beat", "demo", &t.kid.1, json!({"task": task})));
    advance(&clock, 20);
    let over = req(&mut n, "task_checkpoint", "demo", &t.kid.1, json!({"task": task, "summary": "late"}));
    assert_eq!((code(&over), over["error"]["message"].as_str().unwrap().contains("wall time")), ("denied".to_string(), true));
}

#[test]
fn integration_records_are_kept_by_the_task_and_only_those_with_a_hand_in_it_write_them() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    ok(req(&mut n, "task_scope", "demo", &t.lead.1, json!({"task": task, "scope": {"state": "reserved"}})));
    let a = assign(&mut n, &t, &task);
    ok(claim(&mut n, &t, &task, &a));
    submit(&mut n, &t, &task);
    ok(judge(&mut n, &t.other.1, &task, accepted()));
    let outsider = session(&mut n, "demo", "outsider");
    lose_authority(&mut n, &t);
    let record = json!({"task": task, "kind": "integration", "rid": "integration:1", "record": {"state": "candidate", "commit": "c".repeat(40)}});
    assert_eq!(code(&req(&mut n, "task_record", "demo", &outsider.1, record.clone())), "denied");
    ok(req(&mut n, "task_record", "demo", &t.lead.1, record));
    ok(req(&mut n, "task_record", "demo", &t.lead.1, json!({"task": task, "kind": "integration", "rid": "integration:1", "record": {"state": "integrated"}})));
    ok(req(&mut n, "task_record", "demo", &t.lead.1, json!({"task": task, "kind": "event", "rid": "e1", "record": {"state": "integrated"}})));
    ok(req(&mut n, "task_record", "demo", &t.lead.1, json!({"task": task, "kind": "evidence", "rid": "proposal", "record": {"entries": [{"id": "e1", "outcome": "pass"}]}})));
    let held = get(&mut n, &t.lead.1, &task);
    assert_eq!(held["test_evidence"][0]["entries"][0]["id"], json!("e1"));
    assert_eq!((held["integrations"].as_array().unwrap().len(), held["integrations"][0]["state"].clone(), held["integration_events"].as_array().unwrap().len()), (1, json!("integrated"), 1));
    let landed = "f".repeat(40);
    ok(req(&mut n, "task_complete", "demo", &t.lead.1, json!({"task": task, "landed": landed})));
    let done = get(&mut n, &t.lead.1, &task);
    assert_eq!((done["state"].clone(), done["landed_commit"].clone()), (json!("completed"), json!(landed)));
    assert_eq!(code(&req(&mut n, "task_complete", "demo", &t.lead.1, json!({"task": task, "landed": landed}))), "invalid", "a completed task is not completed again");
}

#[test]
fn the_parent_of_an_assigned_worker_reserves_its_checkout_before_the_worker_claims() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.other.1, json!({"source_key": "scope-sponsor"})));
    lose_authority(&mut n, &t);
    let scope = json!({"task": task, "scope": {"state": "reserved"}});
    assert_eq!(code(&req(&mut n, "task_scope", "demo", &t.lead.1, scope.clone())), "denied", "no worker is assigned yet");
    let allocation = assign(&mut n, &t, &task);
    ok(req(&mut n, "task_scope", "demo", &t.lead.1, scope.clone()));
    let record = json!({"task": task, "kind": "allocation", "rid": allocation, "record": {"execution_config": {"harness": "pi"}}});
    ok(req(&mut n, "task_record", "demo", &t.lead.1, record.clone()));
    let moved = json!({"task": task, "scope": {"state": "active"}});
    ok(req(&mut n, "task_scope", "demo", &t.kid.1, moved));
    let stranger = session(&mut n, "demo", "stranger");
    assert_eq!(code(&req(&mut n, "task_scope", "demo", &stranger.1, scope)), "denied");
    assert_eq!(code(&req(&mut n, "task_record", "demo", &stranger.1, record)), "denied");
    let other_kind = json!({"task": task, "kind": "integration", "rid": "integration:1", "record": {"state": "candidate"}});
    assert_eq!(code(&req(&mut n, "task_record", "demo", &t.lead.1, other_kind)), "denied", "only the allocation is the parent's before the worker claims");
    let held = get(&mut n, &t.lead.1, &task);
    assert_eq!((held["scope"]["state"].clone(), held["allocations"][0]["execution_config"]["harness"].clone()), (json!("active"), json!("pi")));
}

#[test]
fn a_task_is_scoped_to_its_board_and_the_node_never_takes_a_sender_from_the_request() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let t = team(&mut n);
    let task = id_of(make(&mut n, &t.lead.1, json!({})));
    let elsewhere = session(&mut n, "side", "x");
    assert_eq!(code(&req(&mut n, "task_get", "side", &elsewhere.1, json!({"task": task}))), "invalid", "another board does not hold the task");
    assert_eq!(code(&req(&mut n, "task_get", "demo", &elsewhere.1, json!({"task": task}))), "denied", "a session of another board has no part in these tasks");
    assert_eq!(ok(req(&mut n, "task_list", "side", &elsewhere.1, json!({})))["tasks"], json!([]));
    assert_eq!(code(&req(&mut n, "task_create", "demo", &t.lead.1, json!({"title": "x", "acceptance": ["y"], "created_by": "someone"}))), "invalid");
    assert_eq!(code(&req(&mut n, "task_create", "demo", &t.lead.1, json!({"title": "x", "acceptance": ["y"], "author": "someone"}))), "denied");
    let listed = ok(req(&mut n, "task_list", "demo", &t.lead.1, json!({})));
    assert_eq!(listed["tasks"][0]["created_by"], json!(t.lead.0));
}

#[test]
fn tasks_survive_a_restart() {
    let dir = tempdir().unwrap();
    let (task, lead) = {
        let mut n = node(dir.path());
        let t = team(&mut n);
        (id_of(make(&mut n, &t.lead.1, json!({"source_key": "keep"}))), t.lead.1.clone())
    };
    let mut n = node(dir.path());
    assert_eq!(get(&mut n, &lead, &task)["source_key"], "keep");
}
