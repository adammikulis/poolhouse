//! The one quiet notice of a hold, a suspension or a flood, and what the person's answer to it does.
//!
//! A requester that is held has what it asks kept back, shown to nobody. The person is told once, quietly, and chooses: let one of
//! them through as a normal request (`Allow once`), leave them held (`Keep holding`), or refuse everything from it for a day (`Block`).
//! For a flood (more requests than the phone is meant to hear about in an hour) the notice only says that they wait in the list.
//! The notice is an approval of kind `hold` that no session can read or withdraw, decided by a key like any other.

use serde_json::json;

use crate::approvals::content::{span, utc};
use crate::approvals::context::Context;
use crate::approvals::decide::{audit, requester_of};
use crate::approvals::fatigue::{QUIET, URGENT};
use crate::approvals::{Approval, CANCELLED, EXPIRED, GRANTS, HELD, HOLD, MAX_TTL_MS, PENDING};
use crate::error::Result;
use crate::fsutil::random_hex;
use crate::node::Node;
use crate::poolops::POOL_BOARD;

/// The ledger key of the flood notice: a node has one.
pub const FLOOD: &str = "flood";

/// A ledger key as the person reads it: `session:demo/claude-a` is "the session claude-a on board demo".
pub fn label_of(key: &str) -> String {
    match key.split_once(':') {
        Some(("session", rest)) => rest.split_once('/').map_or_else(|| rest.to_string(), |(b, n)| format!("the session {n} on board {b}")),
        Some(("agent", rest)) => rest.split_once('/').map_or_else(|| rest.to_string(), |(b, a)| format!("the agent {a} on board {b}")),
        Some(("pool", board)) => format!("the agents of board {board}"),
        _ => "the node".into(),
    }
}

fn text(cause: &str, key: &str, queued: u64, until_ms: u64, block_ms: u64) -> (String, String) {
    let who = label_of(key);
    let mut lines = Vec::new();
    let title = if cause == "flood" {
        "Requests are waiting in your list".to_string()
    } else {
        let what = if cause == "suspended" { "is suspended" } else { "is held" };
        lines.push(format!("FLAG: {who} keeps asking after you declined or let its requests run out."));
        format!("{who} {what}").chars().take(80).collect()
    };
    lines.push("RISK: ROUTINE. It approves nothing by itself: it only settles what happens to requests that were kept back.".into());
    lines.push(format!("WHO: The node, about {who}."));
    match cause {
        "flood" => {
            lines.push(format!("ASK: Tell you that at least {queued} requests wait in your list because too many came too fast."));
            lines.push("SCOPE: None of them was pushed to your phone. Open the app to read them. This notice comes once an hour at most.".into());
            lines.push("CANNOT: Nothing here approves anything; each waiting request still needs its own fingerprint.".into());
            lines.push("WHY: Too many requests in an hour would keep your phone buzzing. The node stops the buzzing, not the requests.".into());
            lines.push("DENY: Approve or deny only closes this notice.".into());
        }
        _ => {
            let kept = if cause == "suspended" { "It is refused until" } else { "What it asks is kept back until" };
            lines.push(if cause == "suspended" { format!("ASK: Decide whether {who} stays suspended after it kept asking again and again.") }
                       else { format!("ASK: Decide what happens to the at least {queued} requests {who} made since you were last asked.") });
            lines.push(format!("SCOPE: {kept} {}. None of them was shown to you or sent to your phone. Allow once {}. Keep holding leaves it as it is. Block refuses everything from it for {}.",
                               utc(until_ms), if cause == "suspended" { "lifts the suspension" } else { "shows you one of them as a normal request" }, span(block_ms)));
            lines.push("CANNOT: Nothing here approves a grant: a request you let through still needs its own text and its own fingerprint.".into());
            lines.push("WHY: Asking again and again after a decline wears a person down, so the node keeps those requests back and tells you once.".into());
            lines.push("DENY: In this notice approve means Allow once, deny means Keep holding and block means Block.".into());
        }
    }
    (title, lines.join("\n"))
}

/// The summary of what is kept back at the ledger level ``key`` (or of the flood), made the first time and, silently, again when the
/// count has doubled. Only the first of a hold pushes, quietly. A hold a person already answered gets no more.
pub fn raise(node: &mut Node, key: &str, cause: &str, queued: u64) -> Result<()> {
    let (old, was, answered) = node.fatigue.summary_of(key);
    if answered || (!old.is_empty() && queued < was.saturating_mul(2)) {
        return Ok(());
    }
    let now = node.now_ms();
    if cause == "flood" && old.is_empty() {
        node.fatigue.begin(key, now + 3_600_000)?;
    }
    let until = node.fatigue.until(key).max(now + 60_000).min(now + MAX_TTL_MS);
    let block_ms = if cause == "flood" { 0 } else { node.fatigue.rules.block_ms };
    if node.approvals.get(&old).is_some_and(Approval::pending) {
        node.approvals.update(&old, |x| {
            x.state = CANCELLED.into();
            x.reason = "A newer notice replaced this one.".into();
            x.decided_ms = now;
        })?;
    }
    let (title, text) = text(cause, key, queued, until, block_ms);
    let ctx = Context { block_ms, ..Context::default() };
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: HOLD.into(), board: POOL_BOARD.into(), action: json!({"kind": HOLD, "cause": cause, "key": key, "queued": queued}),
        text, title, risk: "routine".into(), nonce: random_hex(16)?, created_ms: now, expires_ms: until, context: ctx, urgency: QUIET.into(), source: node.cert.fingerprint(),
        ..Approval::empty()
    };
    node.approvals.add(approval.clone())?;
    node.fatigue.set_summary(key, &approval.id, queued)?;
    audit(node, "approval.summary", &approval, &format!("{cause}; {queued} kept back"));
    if old.is_empty() {
        crate::phone::announce(node, &approval);
    }
    Ok(())
}

/// Let one request kept back at ``key`` through: the oldest becomes a normal request and is pushed. Whether there was one.
fn release_one(node: &mut Node, key: &str) -> Result<bool> {
    let Some(id) = node.approvals.all().iter().filter(|a| a.state == HELD && a.held_by == key).min_by_key(|a| a.seq).map(|a| a.id.clone()) else { return Ok(false) };
    node.approvals.update(&id, |x| {
        x.state = PENDING.into();
        x.urgency = URGENT.into();
    })?;
    if let Some(a) = node.approvals.get(&id).cloned() {
        audit(node, "approval.released", &a, "a person let one held request through");
        crate::phone::announce(node, &a);
    }
    Ok(true)
}

/// End what was kept back at ``key``: every held request of it expires, and its summary is withdrawn.
pub fn let_go(node: &mut Node, key: &str, why: &str) -> Result<()> {
    let now = node.now_ms();
    let ids: Vec<String> = node.approvals.all().iter().filter(|a| (a.state == HELD && a.held_by == key) || (a.kind == HOLD && a.pending() && a.action["key"] == json!(key))).map(|a| a.id.clone()).collect();
    for id in ids {
        node.approvals.update(&id, |x| {
            (x.state, x.decided_ms, x.reason) = (if x.kind == HOLD { CANCELLED } else { EXPIRED }.into(), now, why.into());
        })?;
    }
    Ok(())
}

/// Withdraw the requests of the requester at ``key`` that still wait for an answer: a block or a suspension ends them too, and nothing
/// that was asked before it can be approved after it.
pub fn cancel_pending(node: &mut Node, key: &str, why: &str) -> Result<()> {
    let now = node.now_ms();
    let waiting: Vec<Approval> = node.approvals.all().iter().filter(|a| a.kind == GRANTS && a.pending()).cloned().collect();
    for a in waiting {
        if requester_of(node, &a).keys.iter().any(|k| k == key) {
            node.approvals.update(&a.id, |x| {
                (x.state, x.decided_ms, x.reason) = (CANCELLED.into(), now, why.into());
            })?;
        }
    }
    Ok(())
}

/// What a person's answer to a summary does: ``decision`` is `approve`, `deny` or `block`.
pub fn apply(node: &mut Node, a: &Approval, decision: &str) -> Result<()> {
    let (key, cause) = (a.action["key"].as_str().unwrap_or("").to_string(), a.action["cause"].as_str().unwrap_or("").to_string());
    let now = node.now_ms();
    let hour = 3_600_000;
    match (cause.as_str(), decision) {
        ("flood", _) => node.fatigue.keep(&key, now, 0)?,
        (_, "block") => {
            let ms = node.fatigue.rules.block_ms;
            node.fatigue.block(&key, now, ms)?;
            let_go(node, &key, "A person blocked it.")?;
            cancel_pending(node, &key, "A person blocked it.")?;
        }
        ("held", "approve") => {
            release_one(node, &key)?;
            node.fatigue.keep(&key, now, 0)?;
        }
        ("suspended", "approve") => node.fatigue.lift(&key)?,
        _ => node.fatigue.keep(&key, now, hour)?,
    }
    Ok(())
}
