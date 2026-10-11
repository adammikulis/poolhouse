//! The task calls: who may take each step, the entry it writes, and what the caller gets back.

use serde_json::{json, Map, Value};

use super::fold::{fold, Assign, Board, Task, ACCEPTED, BLOCKED, QUEUED, REVIEW, WORKING};
use super::show;
use super::{HEARTBEAT_S, MAX_TASKS};
use crate::error::{Error, Result};
use crate::grants::Scope;
use crate::identity::Holder;
use crate::lease::rpc;
use crate::lease::table::{Event, Why};
use crate::lease::types::{Resource, State};
use crate::node::Node;
use crate::row::Kind;

fn denied<T>(why: &str) -> Result<T> {
    Err(Error::Denied(why.into()))
}

fn invalid<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> &'a str {
    p.get(key).and_then(Value::as_str).unwrap_or("")
}

/// The live session of this board behind ``token``; a session of a linked board takes no part in its tasks.
fn member(node: &mut Node, token: &str, board: &str) -> Result<String> {
    let access = node.access(token, board, true)?;
    let retired = node.host(board)?.names.get(&access.actor).is_none_or(|i| i.retired);
    if access.holder.board != board || retired {
        return denied("only a live session of this board uses its tasks");
    }
    Ok(access.actor)
}

fn parent_of(node: &mut Node, board: &str, name: &str) -> Result<String> {
    Ok(node.host(board)?.names.get(name).map(|i| i.parent.clone()).unwrap_or_default())
}

/// Whether ``name`` stands in for the person: a session with no parent that holds the task authority grant. Nothing is spent.
fn person(node: &mut Node, board: &str, name: &str) -> Result<bool> {
    let top = node.host(board)?.names.get(name).is_some_and(|i| i.parent.is_empty() && !i.retired);
    Ok(top && crate::grants::holds(node, &Holder { board: board.into(), name: name.into() }, super::AUTHORITY, &Scope::on(board))?)
}

/// Like `person`, for where the person's authority is what lets ``name`` through: the use of the grant on ``what`` is written to the board.
fn exercised(node: &mut Node, board: &str, name: &str, what: &str) -> Result<bool> {
    let top = node.host(board)?.names.get(name).is_some_and(|i| i.parent.is_empty() && !i.retired);
    Ok(top && crate::grants::check(node, &Holder { board: board.into(), name: name.into() }, super::AUTHORITY, &Scope::on(what))?)
}

fn write(node: &mut Node, board: &str, actor: &str, mut body: Map<String, Value>) -> Result<Value> {
    body.insert("at".into(), json!(node.leases.now()));
    let body = Value::Object(body);
    crate::fold::check_local(Kind::Task, actor, &body, "")?;
    let row = node.host(board)?.board.append(Kind::Task, actor, body, "")?;
    Ok(json!({"seq": row.seq, "id": row.id()}))
}

fn step(op: &str, task: &Task) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("op".into(), json!(op));
    m.insert("task".into(), json!(task.id));
    m
}

fn tell(node: &mut Node, board: &str, from: &str, to: &str, subject: &str, body: &str) -> Result<()> {
    if !crate::row::valid_name(to) || !node.can_receive(board, to)? {
        return Ok(());
    }
    let clip = |s: &str, n: usize| s.chars().filter(|c| !c.is_control()).take(n).collect::<String>();
    let body = json!({"type": "status", "from": from, "to": to, "subject": clip(subject, 200), "body": clip(body, 400)});
    crate::fold::check_local(Kind::Message, from, &body, "")?;
    node.host(board)?.board.append(Kind::Message, from, body, "")?;
    Ok(())
}

fn watchers(t: &Task, but: &str) -> Vec<String> {
    t.watchers.iter().filter(|(w, on)| **on && w.as_str() != but).map(|(w, _)| w.clone()).collect()
}

/// Tell the followers of a task that its state changed from ``before``.
fn announce(node: &mut Node, board: &str, actor: &str, task: &str, before: &str) -> Result<()> {
    let b = fold(node, board)?;
    let Some(t) = b.find(task) else { return Ok(()) };
    if t.state == before {
        return Ok(());
    }
    let (subject, body) = (format!("Task {before}-{}: {}", t.state, t.shown), format!("Task {} is {}: {}", t.shown, t.state, t.text("title").chars().take(160).collect::<String>()));
    for who in watchers(t, actor) {
        tell(node, board, actor, &who, &subject, &body)?;
    }
    Ok(())
}

fn find<'b>(b: &'b Board, asked: &str) -> Result<&'b Task> {
    b.find(asked).ok_or_else(|| Error::Invalid(format!("{asked} is not a task of this board")))
}

fn mode_of(b: &Board, t: &Task) -> String {
    if t.state == REVIEW { b.mode.clone() } else { "open".into() }
}

/// Whether ``who`` may do the work of ``t`` now: assigned to it, or the child of its maker, or nobody is assigned.
fn eligible(node: &mut Node, board: &str, who: &str, t: &Task, mode: &str) -> Result<bool> {
    let delegated = t.list("assignees").iter().any(|a| a == who);
    if delegated || parent_of(node, board, who)? == t.by {
        return Ok(true);
    }
    if t.list("assignees").is_empty() {
        return Ok(mode != "strict" || person(node, board, &t.by)?);
    }
    Ok(false)
}

/// Whether ``who`` may judge the work of ``t`` under ``mode``: never its worker; the person, or any member in open
/// mode, or a designated reviewer or the maker who is the worker's parent in strict mode.
fn qualifies(node: &mut Node, board: &str, who: &str, t: &Task, mode: &str) -> Result<bool> {
    if who == t.worker {
        return Ok(false);
    }
    if mode == "open" || t.list("reviewers").iter().any(|r| r == who) || who == t.by && parent_of(node, board, &t.worker)? == who {
        return Ok(true);
    }
    exercised(node, board, who, &t.id)
}

/// The assignment ``asked`` of ``t``, a number of this device or an entry id.
fn assignment<'t>(b: &Board, t: &'t Task, asked: &str) -> Option<&'t Assign> {
    let id = if !asked.is_empty() && asked.bytes().all(|c| c.is_ascii_digit()) { format!("{}:{}:{asked}", t.id.split(':').next().unwrap_or(""), b.own) } else { asked.to_string() };
    t.assigns.iter().find(|a| a.id == id)
}

/// Whether the lease of an assignment is still a grant of its worker's model, as this device's table says.
fn grant_live(node: &mut Node, board: &str, worker: &str, a: &Assign) -> Result<()> {
    rpc::tick(node, &[board])?;
    let holder = format!("{board}/{worker}");
    let held = node.leases.table.find(&a.lease).is_some_and(|l| {
        l.state == State::Held && l.holder == holder && l.resources.iter().any(|r| matches!(r, Resource::ModelSlot { model, .. } if *model == a.model))
    });
    if held { Ok(()) } else { denied("the resource lease is not an active managed grant") }
}

fn drop_lease(node: &mut Node, board: &str, id: &str) -> Result<()> {
    rpc::tick(node, &[board])?;
    let Some(l) = node.leases.table.find(id).filter(|l| l.state == State::Held).cloned() else { return Ok(()) };
    let ended = node.leases.table.release(&l.id, &l.holder)?;
    node.leases.save()?;
    rpc::mirror(node, &[Event::Ended(ended, Why::Released)])?;
    rpc::tick(node, &[board])
}

/// The task a worker is working now with a live lease and a live grant, or why it is not.
fn working(node: &mut Node, board: &str, actor: &str, asked: &str) -> Result<(Task, Assign)> {
    let b = fold(node, board)?;
    let t = find(&b, asked)?.clone();
    if t.state != WORKING || t.worker != actor {
        return denied("only the current task worker may change a working task");
    }
    let lease = t.lease.clone().unwrap_or_default();
    if !(lease.active && b.held.contains(&lease.id)) {
        return denied("the task lease expired; it must be recovered before work continues");
    }
    if t.limit("max_wall_s").and_then(Value::as_u64).is_some_and(|m| b.now.saturating_sub(lease.at) > m * 1000) {
        return denied("task wall time budget exceeded");
    }
    let a = assignment(&b, &t, &lease.assign).cloned().ok_or_else(|| Error::Denied("the allocation belongs to another worker or task".into()))?;
    grant_live(node, board, actor, &a)?;
    Ok((t, a))
}

fn shown(node: &mut Node, board: &str, task: &str) -> Result<Value> {
    let b = fold(node, board)?;
    Ok(show::task(&b, find(&b, task)?))
}

fn spec_of(p: &Map<String, Value>, deps: Vec<String>) -> Map<String, Value> {
    let mut spec = Map::new();
    for key in ["title", "description", "acceptance", "capabilities", "limits", "source_key", "assignees", "reviewers"] {
        let default = match key {
            "description" | "source_key" => json!(""),
            "limits" => json!({}),
            _ => json!([]),
        };
        spec.insert(key.into(), p.get(key).cloned().unwrap_or(default));
    }
    spec.insert("deps".into(), json!(deps));
    spec
}

fn create(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let b = fold(node, board)?;
    let deps = super::lines(p, "deps", 32, 128, true)?;
    let ids: Vec<String> = deps.iter().map(|d| find(&b, d).map(|t| t.id.clone())).collect::<Result<_>>().map_err(|_| Error::Invalid("dependencies must be tasks of this board".into()))?;
    let spec = spec_of(p, ids);
    for who in spec["assignees"].as_array().into_iter().flatten().chain(spec["reviewers"].as_array().into_iter().flatten()).filter_map(Value::as_str) {
        if !node.can_receive(board, who)? {
            return invalid("designated identities must be sessions of this board");
        }
    }
    let key = spec["source_key"].as_str().unwrap_or("");
    if let Some(old) = b.tasks.iter().find(|t| !key.is_empty() && t.text("source_key") == key) {
        if old.by != actor || old.spec != spec {
            return invalid("source key already belongs to another task specification");
        }
        return Ok(show::task(&b, old));
    }
    if b.tasks.len() >= MAX_TASKS {
        return Err(Error::Quota("task board capacity reached".into()));
    }
    let mut body = spec;
    body.insert("op".into(), json!("create"));
    let made = write(node, board, &actor, body)?;
    shown(node, board, made["id"].as_str().unwrap_or(""))
}

fn watch(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let on = p.get("on").and_then(Value::as_bool).ok_or_else(|| Error::Invalid("on is true or false".into()))?;
    let who = if text(p, "who").is_empty() { actor.clone() } else { text(p, "who").to_string() };
    let b = fold(node, board)?;
    let t = find(&b, text(p, "task"))?.clone();
    if who != actor && parent_of(node, board, &who)? != actor {
        return denied("task subscription requires the registered project worker parent");
    }
    let was = t.watchers.get(&who).copied().unwrap_or(false);
    let mut body = step("watch", &t);
    body.insert("who".into(), json!(who));
    body.insert("on".into(), json!(on));
    write(node, board, &actor, body)?;
    if was != on {
        let event = if on { "subscribed" } else { "unsubscribed" };
        let note = format!("{who} {event} task {}: {}", t.shown, t.text("title").chars().take(140).collect::<String>());
        for other in watchers(&t, &actor) {
            tell(node, board, &actor, &other, &format!("Task {event}: {}", t.shown), &note)?;
        }
    }
    Ok(json!({"task": t.shown, "subscriber": who, "subscribed": on}))
}

fn assign(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let worker = text(p, "worker").to_string();
    let b = fold(node, board)?;
    let t = find(&b, text(p, "task"))?.clone();
    let parent = parent_of(node, board, &worker)?;
    let live = node.host(board)?.names.get(&worker).is_some_and(|i| !i.retired);
    if !live {
        return invalid("the worker is not a live session of this board");
    }
    let by_parent = parent == actor;
    let by_maker = actor == t.by && t.list("assignees").contains(&worker) && actor != worker;
    let by_self = !by_parent && !by_maker && actor == worker && exercised(node, board, &actor, &t.id)?;
    if !(by_parent || by_maker || by_self) {
        return Err(crate::grants::refused_for(node, token, super::AUTHORITY, &t.id, "only the registered worker parent, the task creator of an explicitly assigned worker or a person assigns a task"));
    }
    if t.state != QUEUED || !eligible(node, board, &worker, &t, &b.mode)? {
        return denied("the worker must be eligible for the queued task");
    }
    rpc::tick(node, &[board])?;
    let holder = format!("{board}/{worker}");
    let lease = node.leases.table.find(text(p, "lease")).filter(|l| l.state == State::Held && l.holder == holder).cloned();
    let Some(model) = lease.as_ref().and_then(|l| l.resources.iter().find_map(|r| match r {
        Resource::ModelSlot { model, .. } => Some(model.clone()),
        _ => None,
    })) else {
        return denied("the resource lease is not an active managed grant of the worker's model");
    };
    let mut body = step("assign", &t);
    body.insert("worker".into(), json!(worker));
    body.insert("lease".into(), json!(text(p, "lease")));
    body.insert("model".into(), json!(model));
    body.insert("capabilities".into(), p.get("capabilities").cloned().unwrap_or(json!([])));
    let made = write(node, board, &actor, body)?;
    Ok(json!({"allocation_id": made["id"], "task": t.shown, "worker": worker, "owner": actor, "lease_id": text(p, "lease"), "model": model,
              "capabilities": p.get("capabilities").cloned().unwrap_or(json!([])), "person_assigned": by_self}))
}

fn claim(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let b = fold(node, board)?;
    let t = find(&b, text(p, "task"))?.clone();
    let a = assignment(&b, &t, text(p, "assign")).filter(|a| a.worker == actor).cloned().ok_or_else(|| Error::Denied("the allocation belongs to another worker or task".into()))?;
    if t.state != QUEUED {
        return denied("the task is already claimed or has an outcome");
    }
    if t.failures > t.retries() {
        return denied("task retry budget exhausted");
    }
    if t.list("deps").iter().any(|d| b.find(d).is_none_or(|d| d.state != super::fold::COMPLETED)) {
        return denied("task dependencies have not been independently accepted");
    }
    if !eligible(node, board, &actor, &t, &b.mode)? {
        return denied("the task requires an authorized registered worker");
    }
    if !t.list("capabilities").iter().all(|c| a.capabilities.contains(c)) {
        return denied("the worker allocation lacks a required capability");
    }
    if t.limit("model").and_then(Value::as_str).is_some_and(|m| m != a.model) {
        return denied("the allocated model does not match the task requirement");
    }
    grant_live(node, board, &actor, &a)?;
    let mut ask = Map::new();
    ask.insert("resources".into(), json!([{"type": "claim", "kind": "task", "name": t.id}]));
    ask.insert("wait".into(), json!(false));
    ask.insert("ttl_s".into(), json!(HEARTBEAT_S));
    let got = rpc::call(node, "lease_acquire", board, token, &ask)?;
    if got["state"] == "busy" {
        return denied("the task is already claimed or has an outcome");
    }
    let lease = got["id"].as_str().unwrap_or("").to_string();
    let mut body = step("claim", &t);
    body.insert("assign".into(), json!(a.id));
    body.insert("lease".into(), json!(lease));
    if let Err(e) = write(node, board, &actor, body) {
        drop_lease(node, board, &lease)?;
        return Err(e);
    }
    announce(node, board, &actor, &t.id, &t.state)?;
    let b = fold(node, board)?;
    Ok(show::task(&b, find(&b, &t.id)?)["lease"].clone())
}

fn beat(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let (t, _) = working(node, board, &actor, text(p, "task"))?;
    let mut renew = Map::new();
    renew.insert("id".into(), json!(t.lease.map(|l| l.id).unwrap_or_default()));
    renew.insert("ttl_s".into(), json!(HEARTBEAT_S));
    rpc::call(node, "lease_renew", board, token, &renew)?;
    Ok(shown(node, board, &t.id)?["lease"].clone())
}

fn checkpoint(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let (t, _) = working(node, board, &actor, text(p, "task"))?;
    let mut body = step("checkpoint", &t);
    for key in ["summary", "progress", "commit", "environment"] {
        if let Some(v) = p.get(key) {
            body.insert(key.into(), v.clone());
        }
    }
    body.insert("lease".into(), json!(t.lease.map(|l| l.id).unwrap_or_default()));
    let made = write(node, board, &actor, body)?;
    let b = fold(node, board)?;
    Ok(find(&b, &t.id)?.checkpoints.iter().find(|c| c["id"] == made["id"]).cloned().unwrap_or(Value::Null))
}

fn submit(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let (t, a) = working(node, board, &actor, text(p, "task"))?;
    let ident = node.host(board)?.names.get(&actor).cloned().ok_or_else(|| Error::Denied("no such session".into()))?;
    let lease = t.lease.clone().unwrap_or_default();
    let device: String = node.fingerprint().chars().take(16).collect();
    let mut body = step("submit", &t);
    for key in ["artifacts", "checks", "usage", "summary", "provenance"] {
        if let Some(v) = p.get(key) {
            body.insert(key.into(), v.clone());
        }
    }
    body.insert("observed".into(), json!({"actor": actor, "actor_source": "authenticated-task-worker", "model": a.model, "model_source": "verified-serving-resource",
        "alias": actor, "alias_source": "registered-session", "allocation_id": a.id, "runtime": ident.harness, "runtime_source": "registered-session-harness",
        "claimed_model": ident.model, "device_id": device}));
    body.insert("lease".into(), json!(lease.id));
    let made = write(node, board, &actor, body)?;
    drop_lease(node, board, &lease.id)?;
    announce(node, board, &actor, &t.id, &t.state)?;
    let b = fold(node, board)?;
    Ok(find(&b, &t.id)?.proposals.iter().find(|x| x["id"] == made["id"]).cloned().unwrap_or(Value::Null))
}

fn review(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let b = fold(node, board)?;
    let t = find(&b, text(p, "task"))?.clone();
    if t.state != REVIEW {
        return invalid("the task has no proposal awaiting independent review");
    }
    if !qualifies(node, board, &actor, &t, &mode_of(&b, &t))? {
        let why = if b.mode == "strict" { "independent review requires person or existing delegated project authority" } else { "independent review requires a person or a registered member other than the worker" };
        return Err(crate::grants::refused_for(node, token, super::AUTHORITY, &t.id, why));
    }
    let accepted = p.get("accepted").and_then(Value::as_bool).ok_or_else(|| Error::Invalid("review requires a boolean accepted decision".into()))?;
    let outcome = if text(p, "outcome").is_empty() { if accepted { "accepted" } else { "rejected" } } else { text(p, "outcome") };
    if (outcome == "accepted") != accepted {
        return invalid("review outcome must agree with accepted");
    }
    let passed: Vec<&str> = p.get("checks").and_then(Value::as_array).into_iter().flatten().filter(|c| c["passed"] == true).filter_map(|c| c["name"].as_str()).collect();
    let all = p.get("checks").and_then(Value::as_array).is_some_and(|c| c.iter().all(|x| x["passed"] == true));
    if accepted && (!all || !t.list("acceptance").iter().all(|a| passed.contains(&a.as_str()))) {
        return invalid("acceptance requires independent passing checks for every criterion");
    }
    let proposal = t.proposal().cloned().unwrap_or(Value::Null);
    let mut body = step("review", &t);
    for key in ["accepted", "reason", "checks", "verified_usage", "quality", "review"] {
        if let Some(v) = p.get(key) {
            body.insert(key.into(), v.clone());
        }
    }
    body.insert("outcome".into(), json!(outcome));
    body.insert("proposal".into(), proposal["id"].clone());
    body.insert("proposal_hash".into(), proposal["proposal_hash"].clone());
    let made = write(node, board, &actor, body)?;
    announce(node, board, &actor, &t.id, &t.state)?;
    let b = fold(node, board)?;
    Ok(find(&b, &t.id)?.reviews.iter().find(|x| x["id"] == made["id"]).cloned().unwrap_or(Value::Null))
}

fn block(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let b = fold(node, board)?;
    let t = find(&b, text(p, "task"))?.clone();
    let kind = if text(p, "kind").is_empty() { "infrastructure" } else { text(p, "kind") };
    let parent = parent_of(node, board, &t.worker)?;
    if actor != t.worker && !(actor == t.by && t.by == parent) && !exercised(node, board, &actor, &t.id)? {
        return Err(crate::grants::refused_for(node, token, super::AUTHORITY, &t.id, "only the worker, person or actual task parent may record blockage"));
    }
    if !matches!(t.state.as_str(), WORKING | REVIEW) {
        return invalid("only an active task may be blocked");
    }
    let mut body = step("block", &t);
    body.insert("reason".into(), json!(text(p, "reason")));
    body.insert("kind".into(), json!(kind));
    write(node, board, &actor, body)?;
    if let Some(l) = &t.lease {
        drop_lease(node, board, &l.id)?;
    }
    announce(node, board, &actor, &t.id, &t.state)?;
    shown(node, board, &t.id)
}

fn ready(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let b = fold(node, board)?;
    let t = find(&b, text(p, "task"))?.clone();
    let expired = p.get("expired").and_then(Value::as_bool).unwrap_or(false);
    if !qualifies(node, board, &actor, &t, &mode_of(&b, &t))? {
        return Err(crate::grants::refused_for(node, token, super::AUTHORITY, &t.id, "independent review requires a person or a registered member other than the worker"));
    }
    let live = t.lease.as_ref().is_some_and(|l| l.active && b.held.contains(&l.id));
    if expired && t.state != WORKING {
        return invalid("only a working task can recover an expired lease");
    }
    if expired && live {
        return denied("a live task lease cannot be recovered");
    }
    if !expired && t.state != BLOCKED {
        return invalid("only an explicitly blocked task can resume");
    }
    if t.failures + u64::from(expired) > t.retries() {
        return denied("task retry budget exhausted");
    }
    let mut body = step("ready", &t);
    body.insert("reason".into(), json!(text(p, "reason")));
    body.insert("expired".into(), json!(expired));
    write(node, board, &actor, body)?;
    if let Some(l) = &t.lease {
        drop_lease(node, board, &l.id)?;
    }
    announce(node, board, &actor, &t.id, &t.state)?;
    shown(node, board, &t.id)
}

fn mode(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let b = fold(node, board)?;
    let asked = text(p, "mode");
    if asked.is_empty() {
        return Ok(json!({"mode": b.mode, "set_by": b.mode_by}));
    }
    if !exercised(node, board, &actor, "mode")? {
        return Err(crate::grants::refused_for(node, token, super::AUTHORITY, "mode", "only a lead or a person sets the task enforcement mode, not a helper"));
    }
    let mut body = Map::new();
    body.insert("op".into(), json!("enforce"));
    body.insert("mode".into(), json!(asked));
    write(node, board, &actor, body)?;
    let detail = format!("{} {}>{asked}", board, b.mode);
    node.host(board)?.board.append(Kind::Audit, &actor, json!({"event": "enforcement.set", "subject": board, "detail": detail}), "")?;
    Ok(json!({"mode": asked, "from": b.mode, "set_by": actor}))
}

/// Whether ``actor`` has a hand in landing ``t``: its worker or the worker's parent, its maker, its reviewer or the person.
fn landing_hand(node: &mut Node, board: &str, actor: &str, t: &Task) -> Result<bool> {
    let verifier = t.review().and_then(|r| r["verifier"].as_str()).unwrap_or("").to_string();
    let parent = parent_of(node, board, &t.worker)?;
    if [&t.worker, &t.by, &verifier, &parent].iter().any(|n| !n.is_empty() && n.as_str() == actor) {
        return Ok(true);
    }
    exercised(node, board, actor, &t.id)
}

/// Whether ``actor`` is a worker this task was assigned to or its parent: before the worker claims, the parent reserves the
/// checkout (the scope) and records the allocation it made, and the worker records its checkout.
fn sponsors(node: &mut Node, board: &str, actor: &str, t: &Task, method: &str, p: &Map<String, Value>) -> Result<bool> {
    let scope = method == "task_scope";
    if !scope && !(method == "task_record" && text(p, "kind") == "allocation") {
        return Ok(false);
    }
    for a in &t.assigns {
        if scope && a.worker == actor || parent_of(node, board, &a.worker)? == actor {
            return Ok(true);
        }
    }
    Ok(false)
}

fn host_step(node: &mut Node, board: &str, token: &str, method: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let b = fold(node, board)?;
    let t = find(&b, text(p, "task"))?.clone();
    if !(landing_hand(node, board, &actor, &t)? || method == "task_scope" && t.list("assignees").contains(&actor) || sponsors(node, board, &actor, &t, method, p)?) {
        return Err(crate::grants::refused_for(node, token, super::AUTHORITY, &t.id, "only the task worker, registered parent, creator or independent reviewer may record its execution scope or integration"));
    }
    let mut body = step(&method["task_".len()..], &t);
    match method {
        "task_scope" => {
            body.insert("scope".into(), p.get("scope").cloned().unwrap_or(Value::Null));
        }
        "task_record" => {
            for key in ["kind", "rid", "record"] {
                body.insert(key.into(), p.get(key).cloned().unwrap_or(Value::Null));
            }
        }
        _ => {
            if t.state != ACCEPTED {
                return invalid("only an accepted task is completed by its landing");
            }
            body.insert("landed".into(), json!(text(p, "landed")));
        }
    }
    write(node, board, &actor, body)?;
    announce(node, board, &actor, &t.id, &t.state)?;
    shown(node, board, &t.id)
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "task_create" => create(node, board, token, p),
        "task_list" => {
            member(node, token, board)?;
            let b = fold(node, board)?;
            let mut tasks: Vec<Value> = b.tasks.iter().map(|t| show::task(&b, t)).collect();
            tasks.sort_by_key(|t| std::cmp::Reverse(t["created_at"].as_u64().unwrap_or(0)));
            Ok(json!({"tasks": tasks, "overview": show::overview(&b), "metrics": show::metrics(&b), "mode": b.mode, "foreign_ignored": b.ignored}))
        }
        "task_get" => {
            member(node, token, board)?;
            shown(node, board, text(p, "task"))
        }
        "task_watchers" => {
            member(node, token, board)?;
            let b = fold(node, board)?;
            let mut w = watchers(find(&b, text(p, "task"))?, "");
            w.sort();
            Ok(json!({"watchers": w}))
        }
        "task_watch" => watch(node, board, token, p),
        "task_assign" => assign(node, board, token, p),
        "task_claim" => claim(node, board, token, p),
        "task_beat" => beat(node, board, token, p),
        "task_checkpoint" => checkpoint(node, board, token, p),
        "task_submit" => submit(node, board, token, p),
        "task_review" => review(node, board, token, p),
        "task_block" => block(node, board, token, p),
        "task_ready" => ready(node, board, token, p),
        "task_mode" => mode(node, board, token, p),
        _ => host_step(node, board, token, method, p),
    }
}
