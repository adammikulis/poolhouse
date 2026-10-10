//! The fold of a board's `task` entries into its tasks: who made each, what state it is in, who holds it and what has
//! been proposed and decided about it. Only this device's own entries and those of a device it trusts count; the
//! others are kept and counted for nothing, as in the landing queue.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::error::Result;
use crate::fold::Entry;
use crate::fsutil::sha256_hex;
use crate::node::Node;
use crate::row::{canonical, Kind};

pub const QUEUED: &str = "queued";
pub const WORKING: &str = "working";
pub const REVIEW: &str = "review";
pub const ACCEPTED: &str = "accepted";
pub const COMPLETED: &str = "completed";
pub const REJECTED: &str = "rejected";
pub const BLOCKED: &str = "blocked";

/// What a worker's claim holds: the node lease of the task and the assignment it rests on.
#[derive(Clone, Debug, Default)]
pub struct Lease {
    pub id: String,
    pub assign: String,
    pub at: u64,
    pub active: bool,
    pub model: String,
    pub capabilities: Vec<String>,
    pub worker: String,
    pub claim: String,
}

#[derive(Clone, Debug)]
pub struct Assign {
    pub id: String,
    pub by: String,
    pub at: u64,
    pub worker: String,
    pub lease: String,
    pub model: String,
    pub capabilities: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Task {
    pub id: String,
    pub shown: String,
    pub by: String,
    pub origin: String,
    pub at: u64,
    pub spec: Map<String, Value>,
    pub state: String,
    pub failures: u64,
    pub blocked_ms: u64,
    pub blocked_at: Option<u64>,
    pub blocked_reason: String,
    pub blocked_kind: String,
    pub worker: String,
    pub started_at: u64,
    pub lease: Option<Lease>,
    pub assigns: Vec<Assign>,
    pub proposals: Vec<Value>,
    pub reviews: Vec<Value>,
    pub proposal_id: String,
    pub review_id: String,
    pub checkpoints: Vec<Value>,
    pub watchers: BTreeMap<String, bool>,
    pub scope: Value,
    pub records: Vec<Value>,
    pub landed: String,
    pub resumed_by: String,
    pub resume_reason: String,
    pub changed_at: u64,
}

impl Task {
    pub fn list(&self, key: &str) -> Vec<String> {
        self.spec.get(key).and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(String::from).collect()
    }

    pub fn text(&self, key: &str) -> &str {
        self.spec.get(key).and_then(Value::as_str).unwrap_or("")
    }

    pub fn limit(&self, key: &str) -> Option<&Value> {
        self.spec.get("limits").and_then(|l| l.get(key))
    }

    /// The most failures the task may have before it stops being retried.
    pub fn retries(&self) -> u64 {
        self.limit("max_retries").and_then(Value::as_u64).unwrap_or(3)
    }

    pub fn proposal(&self) -> Option<&Value> {
        self.proposals.iter().find(|p| p["id"] == self.proposal_id.as_str())
    }

    pub fn review(&self) -> Option<&Value> {
        self.reviews.iter().find(|r| r["id"] == self.review_id.as_str())
    }
}

/// The tasks of one board, the mode they run in, and what was left out of the count.
#[derive(Default)]
pub struct Board {
    pub tasks: Vec<Task>,
    pub mode: String,
    pub mode_by: String,
    pub ignored: usize,
    pub own: String,
    pub now: u64,
    /// Lease ids held right now, by this device's table and by the board's lease entries.
    pub held: Vec<String>,
    /// When each lease of this device's table ends.
    pub deadlines: BTreeMap<String, u64>,
}

fn field<'a>(e: &'a Entry, key: &str) -> &'a str {
    e.fields.get(key).and_then(Value::as_str).unwrap_or("")
}

fn words(e: &Entry, key: &str) -> Vec<String> {
    e.fields.get(key).and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str).map(String::from).collect()
}

fn at(e: &Entry) -> u64 {
    e.fields.get("at").and_then(Value::as_u64).unwrap_or(e.hlc.0)
}

/// ``doc`` with its own hash added under ``key``: the SHA-256 of the document as it was.
fn hashed(mut doc: Value, key: &str) -> Value {
    let hash = sha256_hex(canonical(&doc).as_bytes());
    doc[key] = json!(hash);
    doc
}

impl Board {
    pub fn find(&self, asked: &str) -> Option<&Task> {
        self.tasks.iter().find(|t| t.id == asked || t.shown == asked)
    }

    fn get(&mut self, e: &Entry) -> Option<&mut Task> {
        let asked = field(e, "task");
        self.tasks.iter_mut().find(|t| t.id == asked || t.shown == asked)
    }

    fn create(&mut self, e: &Entry) {
        let mut spec = Map::new();
        for key in ["title", "description", "acceptance", "deps", "capabilities", "limits", "source_key", "assignees", "reviewers"] {
            if let Some(v) = e.fields.get(key) {
                spec.insert(key.into(), v.clone());
            }
        }
        for key in ["description", "source_key"] {
            spec.entry(key).or_insert(json!(""));
        }
        for key in ["deps", "capabilities", "assignees", "reviewers", "acceptance"] {
            spec.entry(key).or_insert(json!([]));
        }
        spec.entry("limits").or_insert(json!({}));
        let shown = if e.origin == self.own { format!("task-{}", e.seq) } else { e.id.clone() };
        self.tasks.push(Task {
            id: e.id.clone(), shown, by: e.sender.clone(), origin: e.origin.clone(), at: at(e), spec, state: QUEUED.into(), failures: 0, blocked_ms: 0,
            blocked_at: None, blocked_reason: String::new(), blocked_kind: String::new(), worker: String::new(), started_at: 0, lease: None, assigns: Vec::new(),
            proposals: Vec::new(), reviews: Vec::new(), proposal_id: String::new(), review_id: String::new(), checkpoints: Vec::new(), watchers: BTreeMap::new(), scope: Value::Null,
            records: Vec::new(), landed: String::new(), resumed_by: String::new(), resume_reason: String::new(), changed_at: at(e),
        });
    }

    fn claim(&mut self, e: &Entry) {
        let when = at(e);
        let Some(t) = self.get(e) else { return };
        let Some(a) = t.assigns.iter().find(|a| a.id == field(e, "assign") && a.worker == e.actor).cloned() else { return };
        if t.state != QUEUED {
            return;
        }
        if let Some(b) = t.blocked_at.take() {
            t.blocked_ms += when.saturating_sub(b);
        }
        t.lease = Some(Lease { id: field(e, "lease").into(), assign: a.id, at: when, active: true, model: a.model, capabilities: a.capabilities, worker: e.sender.clone(), claim: e.id.clone() });
        (t.state, t.worker, t.started_at) = (WORKING.into(), e.sender.clone(), when);
        (t.blocked_reason, t.blocked_kind, t.review_id, t.proposal_id) = (String::new(), String::new(), String::new(), String::new());
    }

    fn submit(&mut self, e: &Entry) {
        let when = at(e);
        let Some(t) = self.get(e) else { return };
        if t.state != WORKING || t.worker != e.sender {
            return;
        }
        let lease = t.lease.as_mut().map(|l| {
            l.active = false;
            l.id.clone()
        });
        let claimed = e.fields.get("provenance").cloned().unwrap_or(json!({}));
        let observed = e.fields.get("observed").cloned().unwrap_or(json!({}));
        let mut provenance = claimed.as_object().cloned().unwrap_or_default();
        provenance.extend(observed.as_object().cloned().unwrap_or_default());
        let doc = json!({"id": e.id, "task": t.shown, "worker": t.worker, "at": when, "lease_id": lease, "artifacts": e.fields["artifacts"], "checks": e.fields["checks"],
                         "usage": e.fields.get("usage").cloned().unwrap_or(json!({})), "summary": field(e, "summary"), "claimed_provenance": claimed,
                         "provenance": provenance});
        t.proposal_id = e.id.clone();
        t.proposals.push(hashed(doc, "proposal_hash"));
        (t.state, t.changed_at) = (REVIEW.into(), when);
    }

    fn review(&mut self, e: &Entry) {
        let when = at(e);
        let Some(t) = self.get(e) else { return };
        let proposal = t.proposal().cloned().unwrap_or(Value::Null);
        if t.state != REVIEW || proposal["id"] != field(e, "proposal") {
            return;
        }
        let accepted = e.fields.get("accepted").and_then(Value::as_bool).unwrap_or(false);
        let outcome = field(e, "outcome");
        let mut doc = json!({"id": e.id, "task": t.shown, "worker": t.worker, "verifier": e.sender, "at": when, "reviewed_at": when, "proposal_id": proposal["id"],
                             "proposal_hash": proposal["proposal_hash"], "accepted": accepted, "outcome": outcome, "reason": field(e, "reason"), "checks": e.fields["checks"]});
        for key in ["verified_usage", "quality", "review"] {
            if let Some(v) = e.fields.get(key) {
                doc[key] = v.clone();
            }
        }
        t.review_id = e.id.clone();
        t.reviews.push(hashed(doc, "review_hash"));
        t.state = match (accepted, outcome) {
            (true, _) if t.scope.is_null() => COMPLETED,
            (true, _) => ACCEPTED,
            (false, "blocked_infrastructure") => BLOCKED,
            _ => REJECTED,
        }
        .into();
        if outcome == "rejected" {
            t.failures += 1;
        }
        if outcome == "blocked_infrastructure" {
            (t.blocked_reason, t.blocked_at, t.blocked_kind) = (field(e, "reason").into(), Some(when), String::new());
        }
        t.changed_at = when;
    }

    fn block(&mut self, e: &Entry) {
        let when = at(e);
        let Some(t) = self.get(e) else { return };
        if !matches!(t.state.as_str(), WORKING | REVIEW) {
            return;
        }
        (t.state, t.blocked_reason, t.blocked_kind, t.blocked_at, t.changed_at) = (BLOCKED.into(), field(e, "reason").into(), field(e, "kind").into(), Some(when), when);
        t.failures += u64::from(field(e, "kind") == "failure");
        if let Some(l) = t.lease.as_mut() {
            l.active = false;
        }
    }

    fn ready(&mut self, e: &Entry) {
        let when = at(e);
        let expired = e.fields.get("expired").and_then(Value::as_bool).unwrap_or(false);
        let Some(t) = self.get(e) else { return };
        if t.state != if expired { WORKING } else { BLOCKED } {
            return;
        }
        t.failures += u64::from(expired);
        if let Some(l) = t.lease.as_mut() {
            l.active = false;
        }
        if let Some(b) = t.blocked_at.take() {
            t.blocked_ms += when.saturating_sub(b);
        }
        let lease = t.lease.as_ref().map(|l| l.id.clone());
        t.checkpoints.push(json!({"id": e.id, "task": t.shown, "worker": t.worker, "at": when, "summary": field(e, "reason"),
                                  "transition": if expired { "recover" } else { "resume" }, "authorized_by": e.sender, "lease_id": lease}));
        (t.state, t.resumed_by, t.resume_reason, t.blocked_reason, t.blocked_kind, t.changed_at) = (QUEUED.into(), e.sender.clone(), field(e, "reason").into(), String::new(), String::new(), when);
    }

    fn apply(&mut self, e: &Entry) {
        match field(e, "op") {
            "create" => self.create(e),
            "enforce" => (self.mode, self.mode_by) = (field(e, "mode").into(), e.sender.clone()),
            "claim" => self.claim(e),
            "submit" => self.submit(e),
            "review" => self.review(e),
            "block" => self.block(e),
            "ready" => self.ready(e),
            op => {
                let when = at(e);
                let Some(t) = self.get(e) else { return };
                t.changed_at = t.changed_at.max(when);
                match op {
                    "watch" => {
                        t.watchers.insert(field(e, "who").into(), e.fields.get("on").and_then(Value::as_bool).unwrap_or(false));
                    }
                    "assign" => t.assigns.push(Assign {
                        id: e.id.clone(), by: e.sender.clone(), at: when, worker: field(e, "worker").into(), lease: field(e, "lease").into(),
                        model: field(e, "model").into(), capabilities: words(e, "capabilities"),
                    }),
                    "checkpoint" if t.state == WORKING && t.worker == e.sender => {
                        let mut doc = json!({"id": e.id, "task": t.shown, "worker": t.worker, "at": when, "lease_id": t.lease.as_ref().map(|l| l.id.clone())});
                        for key in ["summary", "progress", "commit", "environment"] {
                            if !field(e, key).is_empty() {
                                doc[key] = json!(field(e, key));
                            }
                        }
                        t.checkpoints.push(doc);
                    }
                    "scope" => t.scope = e.fields.get("scope").cloned().unwrap_or(Value::Null),
                    "record" => {
                        let (kind, rid) = (field(e, "kind"), field(e, "rid"));
                        let new = e.fields.get("record").cloned().unwrap_or(Value::Null);
                        let same = |r: &Value| r["kind"] == kind && r["rid"] == rid;
                        match t.records.iter_mut().find(|r| same(r)) {
                            Some(old) if kind != "event" => {
                                if let (Some(o), Some(n)) = (old["record"].as_object_mut(), new.as_object()) {
                                    o.extend(n.clone());
                                }
                            }
                            _ => t.records.push(json!({"kind": kind, "rid": rid, "by": e.sender, "at": when, "record": new})),
                        }
                    }
                    "complete" if t.state == ACCEPTED => (t.state, t.landed) = (COMPLETED.into(), field(e, "landed").into()),
                    _ => {}
                }
            }
        }
    }
}

/// The tasks of ``board`` as the entries say, with the leases the board shows held now. The lease table is swept first, so
/// a worker that stopped renewing is seen to have lost its lease.
pub fn fold(node: &mut Node, board: &str) -> Result<Board> {
    crate::lease::rpc::tick(node, &[board])?;
    let entries = node.view(board)?;
    let writers = crate::landtrust::writers(node, board)?;
    let own = node.host(board)?.board.origin().to_string();
    let mut out = Board { own: own.clone(), now: node.leases.now(), mode: "open".into(), ..Board::default() };
    for e in entries.iter().filter(|e| e.kind == Kind::Task) {
        if e.origin == own || writers.trusted.contains(&e.origin) {
            out.apply(e);
        } else {
            out.ignored += 1;
        }
    }
    let view = crate::lease::merge::view(&entries);
    out.held = view.exclusive.values().map(|h| h.lease.clone()).collect();
    for l in node.leases.table.leases.iter().filter(|l| l.state == crate::lease::types::State::Held) {
        out.held.push(l.id.clone());
        out.deadlines.insert(l.id.clone(), l.expires_ms);
    }
    Ok(out)
}
