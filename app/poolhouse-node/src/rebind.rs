//! Moving a main session in a pool. A phone of an admin key approves the move, the person picks among three numbers the one the
//! terminal shows, and the same command then carries it out once, from the terminal the request names.

use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::approvals::content::rebind_text;
use crate::approvals::context::{pick_options, Context};
use crate::approvals::decide::{agent_view, audit, sweep};
use crate::approvals::fatigue::SILENT;
use crate::approvals::request::classify;
use crate::approvals::{summary, Approval, APPROVED, HELD, PENDING, REBIND, USED};
use crate::error::{Error, Result};
use crate::fsutil::random_hex;
use crate::grants::authority::Authority;
use crate::lineage::Anchor;
use crate::node::Node;

/// How long a request waits for a tap, and how long after the tap the terminal may carry the move out.
const ASK_MS: u64 = 10 * 60 * 1000;
const TAPPED_MS: u64 = 10 * 60 * 1000;
const MOST_ASKS_PER_HOUR: usize = 3;

fn denied<T>(why: &str) -> Result<T> {
    Err(Error::Denied(why.into()))
}

/// Ask for the move of ``name`` to the terminal ``anchor`` names, or, with an `approval` id, carry out the move that request got approved.
pub fn by_phone(node: &mut Node, (board, name): (&str, &str), p: &Map<String, Value>, lineage: &[Anchor]) -> Result<Value> {
    let anchor = Anchor::signed(p)?;
    if !lineage.contains(&anchor) {
        return denied("the move was asked for in another terminal than the one asking");
    }
    match p.get("approval").and_then(Value::as_str).unwrap_or("") {
        "" => ask(node, board, name, &anchor),
        id => finish(node, (board, name), &anchor, id, lineage),
    }
}

fn ask(node: &mut Node, board: &str, name: &str, anchor: &Anchor) -> Result<Value> {
    if !Authority::of(node).any() {
        return denied("No admin phone of this pool is added to this device, so a session cannot be moved here: add your phone first.");
    }
    sweep(node)?;
    let live = node.approvals.all().iter().find(|a| a.kind == REBIND && a.live() && a.board == board && a.session == name).cloned();
    if let Some(waiting) = live {
        return if waiting.action["anchor"] == anchor.body() { Ok(agent_view(node, &waiting)) } else { denied("A move of that session already waits for a tap: answer it or wait for it to end.") };
    }
    let (key, now) = (format!("rebind {board}/{name}"), node.now_ms());
    if !node.rates.permits(&key, MOST_ASKS_PER_HOUR, Duration::from_secs(3600)) {
        return Err(crate::limits::floor("That session was asked to move as often as it may be this hour; try again later."));
    }
    let (code, options) = pick_options()?;
    let ctx = Context { pick: options, ..Context::default() };
    let expires = now + ASK_MS;
    let (title, text, risk) = rebind_text(name, board, node.members.policy.name(), &ctx, expires);
    let (urgency, overflow) = classify(node, &format!("{board}/{name}"), true, "unknown", now);
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: REBIND.into(), board: board.into(), session: name.into(),
        action: json!({"kind": REBIND, "board": board, "session": name, "anchor": anchor.body()}),
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms: expires, state: PENDING.into(),
        source: node.cert.fingerprint(), context: ctx, pick_code: code, urgency: urgency.into(), ..Approval::empty()
    };
    node.approvals.add(approval.clone())?;
    node.rates.record(&key, Duration::from_secs(3600));
    audit(node, "approval.request", &approval, "move the session to a terminal");
    crate::phone::announce(node, &approval);
    if overflow {
        let waiting = node.approvals.all().iter().filter(|a| a.pending() && a.urgency == SILENT).count() as u64;
        summary::raise(node, summary::FLOOD, "flood", waiting)?;
    }
    Ok(agent_view(node, &approval))
}

fn finish(node: &mut Node, (board, name): (&str, &str), anchor: &Anchor, id: &str, lineage: &[Anchor]) -> Result<Value> {
    sweep(node)?;
    let Some(a) = node.approvals.get(id).filter(|a| a.kind == REBIND && a.board == board && a.session == name).cloned() else {
        return denied("There is no move of that session with that id.");
    };
    if a.action["anchor"] != anchor.body() {
        return denied("That move was asked for in another terminal.");
    }
    match a.state.as_str() {
        PENDING | HELD => return Ok(json!({"state": a.state, "moved": false, "expires_ms": a.expires_ms})),
        APPROVED => {}
        USED => return denied("That move was already carried out: ask again to move the session again."),
        other => return denied(&format!("That move ended without a tap ({other}): ask again if the session is still to move.")),
    }
    let now = node.now_ms();
    if now.saturating_sub(a.decided_ms) > TAPPED_MS || !Authority::of(node).decides(&a.decided_by) {
        return denied("That move was approved too long ago, or by a key that is not an admin key of this pool now: ask again.");
    }
    let by = format!("the admin phone key {}", a.decided_by.chars().take(16).collect::<String>());
    let done = crate::sessions::carry_out(node, board, name, lineage, &by)?;
    node.approvals.update(id, |x| x.state = USED.into())?;
    Ok(done)
}
