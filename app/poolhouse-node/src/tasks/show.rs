//! A task as clients read it: its spec, its lease, what was proposed and decided, and what has happened to it lately.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use super::fold::{Board, Task, BLOCKED, COMPLETED, QUEUED, WORKING};
use super::HEARTBEAT_S;

fn lease(board: &Board, t: &Task) -> Value {
    let Some(l) = &t.lease else { return Value::Null };
    let deadline = board.deadlines.get(&l.id).copied().unwrap_or(0);
    let active = l.active && board.held.contains(&l.id);
    let beat = if deadline > 0 { deadline.saturating_sub(HEARTBEAT_S * 1000).max(l.at) } else { 0 };
    json!({"id": l.id, "task": t.shown, "worker": l.worker, "allocation_id": l.assign, "at": l.at, "heartbeat_at": beat, "deadline": deadline,
           "active": active, "resource": {"model": l.model, "capabilities": l.capabilities, "allocation_id": l.assign}})
}

fn artifacts(t: &Task) -> Vec<Value> {
    t.proposals.iter().flat_map(|p| {
        p["artifacts"].as_object().into_iter().flatten().map(|(name, sha)| json!({"id": format!("{}:{name}", p["id"].as_str().unwrap_or("")), "task": t.shown,
            "proposal_id": p["id"], "name": name, "sha256": sha, "at": p["at"], "worker": p["worker"]})).collect::<Vec<_>>()
    }).collect()
}

fn records(t: &Task, kind: &str) -> Vec<Value> {
    t.records.iter().filter(|r| r["kind"] == kind).map(|r| {
        let mut one = r["record"].as_object().cloned().unwrap_or_default();
        one.insert("id".into(), r["rid"].clone());
        one.insert("task".into(), json!(t.shown));
        Value::Object(one)
    }).collect()
}

fn waiting(board: &Board, t: &Task) -> Vec<Value> {
    t.list("deps").iter().filter_map(|d| board.find(d)).filter(|d| d.state != COMPLETED).map(|d| json!({"id": d.shown, "title": d.text("title"), "state": d.state})).collect()
}

/// What the task has been doing: when it last changed, whether its worker is alive and how its checks stand.
pub fn activity(board: &Board, t: &Task, lease: &Value) -> Value {
    let checks = t.review().map(|r| r["checks"].as_array().cloned().unwrap_or_default()).unwrap_or_default();
    let passed = checks.iter().filter(|c| c["passed"] == true).count();
    let beat = lease["heartbeat_at"].as_u64().filter(|b| *b > 0);
    json!({"updated_at": t.changed_at.max(t.at), "age_seconds": board.now.saturating_sub(t.at) / 1000,
           "worker_active": t.state == WORKING && lease["active"] == true && lease["deadline"].as_u64().unwrap_or(0) > board.now,
           "heartbeat_age_seconds": beat.map(|b| board.now.saturating_sub(b) / 1000), "waiting_on": waiting(board, t),
           "verified_checks": {"passed": passed, "total": checks.len()}, "eta_seconds": null, "eta_reason": "No verified numeric progress measurements."})
}

fn blocked_ms(board: &Board, t: &Task) -> u64 {
    t.blocked_ms + t.blocked_at.map_or(0, |b| board.now.saturating_sub(b))
}

/// One task in full.
pub fn task(board: &Board, t: &Task) -> Value {
    let l = lease(board, t);
    let mut out = Map::new();
    for (k, v) in &t.spec {
        out.insert(k.clone(), v.clone());
    }
    let deps: Vec<Value> = t.list("deps").iter().map(|d| json!(board.find(d).map_or(d.as_str(), |x| x.shown.as_str()))).collect();
    let dependencies: Vec<Value> = t.list("deps").iter().filter_map(|d| board.find(d)).map(|d| json!({"id": d.shown, "title": d.text("title"), "state": d.state})).collect();
    let more = json!({"id": t.shown, "entry": t.id, "created_by": t.by, "created_at": t.at, "state": t.state, "failures": t.failures, "deps": deps,
        "blocked_seconds": t.blocked_ms / 1000, "blocked_ms": t.blocked_ms, "blocked_at": t.blocked_at, "blocked_reason": t.blocked_reason,
        "blocked_kind": t.blocked_kind, "worker": t.worker, "started_at": t.started_at, "lease": l, "dependencies": dependencies, "proposal": t.proposal(),
        "review": t.review(), "checkpoints": t.checkpoints, "artifacts": artifacts(t), "reviews": t.reviews, "scope": t.scope, "landed_commit": t.landed,
        "resumed_by": t.resumed_by, "resume_reason": t.resume_reason, "integrations": records(t, "integration"), "integration_attempts": records(t, "attempt"),
        "integration_events": records(t, "event"), "allocations": records(t, "allocation"), "test_evidence": records(t, "evidence"), "assignments": t.assigns.iter().map(|a| json!({"id": a.id, "by": a.by, "at": a.at, "worker": a.worker,
        "lease": a.lease, "model": a.model, "capabilities": a.capabilities})).collect::<Vec<_>>(),
        "watchers": t.watchers.iter().filter(|(_, on)| **on).map(|(w, _)| w.as_str()).collect::<Vec<_>>(), "pending_block_ms": blocked_ms(board, t),
        "activity": activity(board, t, &l)});
    out.extend(more.as_object().cloned().unwrap_or_default());
    Value::Object(out)
}

/// The counts a board shows over all its tasks at one moment.
pub fn overview(board: &Board) -> Value {
    let mut states: BTreeMap<&str, u64> = BTreeMap::new();
    for t in &board.tasks {
        *states.entry(t.state.as_str()).or_default() += 1;
    }
    let count = |s: &str| states.get(s).copied().unwrap_or(0);
    let remaining = board.tasks.iter().filter(|t| !matches!(t.state.as_str(), COMPLETED | "rejected")).count();
    let workers: Vec<Value> = board.tasks.iter().filter(|t| t.state == WORKING && !t.worker.is_empty()).map(|t| {
        let a = activity(board, t, &lease(board, t));
        json!({"id": t.worker, "task": t.shown, "title": t.text("title"), "active": a["worker_active"], "heartbeat_age_seconds": a["heartbeat_age_seconds"]})
    }).collect();
    json!({"as_of": board.now, "total": board.tasks.len(), "remaining": remaining, "queued": count(QUEUED), "running": count(WORKING), "blocked": count(BLOCKED),
           "review": count("review"), "awaiting_integration": count("accepted"), "done": count(COMPLETED), "failed": count("rejected"), "workers": workers,
           "eta_seconds": null, "eta_reason": "No verified numeric progress measurements."})
}

pub fn metrics(board: &Board) -> Value {
    let done: Vec<&Task> = board.tasks.iter().filter(|t| t.state == COMPLETED).collect();
    let mut states: BTreeMap<&str, u64> = BTreeMap::new();
    for t in &board.tasks {
        *states.entry(t.state.as_str()).or_default() += 1;
    }
    json!({"states": states, "verified_outcomes": done.len(), "failures": board.tasks.iter().map(|t| t.failures).sum::<u64>(),
           "accepted_artifacts": done.iter().map(|t| t.proposal().and_then(|p| p["artifacts"].as_object()).map_or(0, Map::len)).sum::<usize>(),
           "blocked_seconds": board.tasks.iter().map(|t| blocked_ms(board, t)).sum::<u64>() / 1000})
}
