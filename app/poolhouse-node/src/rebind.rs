//! Moving a main session in a pool. A phone of an admin key approves the move, the person picks among three numbers the one the
//! terminal shows, and the same command then carries it out once, from the terminal the request names.

use serde_json::{json, Map, Value};

use crate::approvals::content::rebind_text;
use crate::approvals::context::{pick_options, Context};
use crate::approvals::decide::{agent_view, audit, sweep};
use crate::approvals::{Approval, APPROVED, CANCELLED, PENDING, REBIND, USED};
use crate::error::{Error, Result};
use crate::fsutil::random_hex;
use crate::grants::authority::Authority;
use crate::lineage::Anchor;
use crate::node::Node;

/// How long a request waits for a tap, and how long after the tap the terminal may carry the move out.
const ASK_MS: u64 = 10 * 60 * 1000;
const TAPPED_MS: u64 = 10 * 60 * 1000;

fn denied<T>(why: &str) -> Result<T> {
    Err(Error::Denied(why.into()))
}

fn binding(node: &mut Node, board: &str, name: &str) -> Result<Value> {
    let ident = node.host(board)?.names.get(name).ok_or_else(|| Error::Denied("no such session".into()))?;
    if ident.retired || !ident.parent.is_empty() {
        return denied("Only a main session that has not retired can be moved.");
    }
    let incarnation = ident.incarnation.clone();
    Ok(json!({"incarnation": incarnation, "authority": crate::approvals::challenge::lift_authority(node)}))
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
    let binding = binding(node, board, name)?;
    let live = node.approvals.open_of(REBIND, board, name).cloned();
    if let Some(waiting) = live {
        if waiting.action["binding"] == binding {
            return if waiting.action["anchor"] == anchor.body() { Ok(agent_view(node, &waiting)) }
                   else { denied("A move of that session to another terminal already waits for the person's tap on their phone: they answer it first, then ask again.") };
        }
        node.approvals.update(&waiting.id, |a| { a.state = CANCELLED.into(); a.reason = "the session or pool authority changed".into(); })?;
    }
    let now = node.now_ms();
    let (code, options) = pick_options()?;
    let ctx = Context { pick: options, ..Context::default() };
    let expires = now + ASK_MS;
    let (title, text, risk) = rebind_text(name, board, node.members.policy.name(), &ctx, expires);
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: REBIND.into(), board: board.into(), session: name.into(),
        action: json!({"kind": REBIND, "board": board, "session": name, "anchor": anchor.body(), "binding": binding}),
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms: expires, state: PENDING.into(),
        source: node.cert.fingerprint(), context: ctx, pick_code: code, ..Approval::empty()
    };
    node.approvals.add(approval.clone())?;
    audit(node, "approval.request", &approval, "move the session to a terminal");
    crate::phone::announce(node, &approval);
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
        PENDING => return Ok(json!({"state": a.state, "moved": false, "expires_ms": a.expires_ms})),
        APPROVED => {}
        USED => return denied("That move was already carried out: ask again to move the session again."),
        other => return denied(&format!("That move ended without a tap ({other}): ask again if the session is still to move.")),
    }
    let now = node.now_ms();
    if a.decided_ms > now || now - a.decided_ms > TAPPED_MS || !Authority::of(node).decides(&a.decided_by) {
        return denied("That move was approved too long ago, or by a key that is not an admin key of this pool now: ask again.");
    }
    if a.action["binding"] != binding(node, board, name)? {
        return denied("The session or pool authority changed since that move was requested: ask again.");
    }
    let by = format!("the admin phone key {}", a.decided_by.chars().take(16).collect::<String>());
    node.approvals.update(id, |x| x.state = USED.into())?;
    crate::sessions::carry_out(node, board, name, lineage, &by)
}
