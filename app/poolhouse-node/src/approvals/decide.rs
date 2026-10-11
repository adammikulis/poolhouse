//! Deciding a request: the checks on the key and the signature, the one change of state, and what an approval does.

use serde_json::{json, Value};

use crate::approvals::fatigue::{kind_of, requester, Cause, Outcome, Requester};
use crate::approvals::{challenge, sign, summary, Approval, APPROVED, CANCELLED, DENIED, EXPIRED, FAILED, GRANTS, HELD, HOLD, PENDING, PHONE_ADD, PHONE_REMOVE, SIGN};
use crate::error::{Error, Result};
use crate::fsutil::sha256_hex;
use crate::grants::spec::parse_issue;
use crate::node::Node;
use crate::row::Kind;

/// A decision as it arrives: the request, `approve`, `approve_repeat`, `deny` or `block`, the signing key's fingerprint, the signature in
/// hex, the SHA-256 of the text the client showed (when it says), and the number the person chose when a number had to be matched.
pub struct Decision<'a> {
    pub id: &'a str,
    pub decision: &'a str,
    pub key: &'a str,
    pub sig: &'a str,
    pub text_sha256: &'a str,
    pub pick: &'a str,
}

fn denied<T>(why: &str) -> Result<T> {
    Err(Error::Denied(why.into()))
}

/// The request ``id`` as a client reads it: its state and text, and the challenge a key signs. Never the number to be matched.
pub fn view(node: &Node, a: &Approval) -> Value {
    json!({"id": a.id, "kind": a.kind, "state": a.state, "title": a.title, "risk": a.risk, "board": a.board, "session": a.session, "text": a.text,
           "created_ms": a.created_ms, "expires_ms": a.expires_ms, "decided_ms": a.decided_ms, "decided_by": a.decided_by, "reason": a.reason,
           "grants": a.grants, "urgency": a.urgency, "retries": a.retries, "challenge": challenge::object(a, &node.cert.fingerprint())})
}

/// The request as the session that made it reads it: the number it is to show the person is in it while the request waits.
pub fn agent_view(node: &Node, a: &Approval) -> Value {
    let mut out = view(node, a);
    if a.live() && !a.pick_code.is_empty() {
        out["code"] = json!(a.pick_code);
    }
    out
}

/// Write an audit entry about the request ``a`` to the pool board: its id, the session and what happened, never its text.
pub fn audit(node: &mut Node, event: &str, a: &Approval, detail: &str) {
    let who = if a.session.is_empty() { "a phone".to_string() } else { format!("{}/{}", a.board, a.session) };
    let _ = node.record_event(event, &a.id, &format!("{who}; {detail}"));
}

/// Who made the request ``a``, as the brakes count them.
pub fn requester_of(node: &mut Node, a: &Approval) -> Requester {
    let root = node.host(&a.board).map_or_else(|_| a.session.clone(), |h| h.names.root(&a.session));
    requester(&a.board, &a.session, &root)
}

/// What a hold or a suspension that just began is told to the person and to the pool: an audit entry, an event the pool's other
/// devices read, and the penalty the trust ledger will read.
fn report(node: &mut Node, a: &Approval, out: &Outcome) -> Result<()> {
    if let Some((key, ms)) = &out.held {
        audit(node, "approval.held", a, &format!("held at {key} for {} ms after repeated declines or timeouts", ms));
        let _ = node.record_event("approval.bombing", key, &format!("{} was held after asking again and again; penalty {}", summary::label_of(key), node.fatigue.penalty(key)));
    }
    if let Some(key) = &out.suspended {
        audit(node, "approval.suspended", a, &format!("suspended at {key}"));
        let _ = node.record_event("approval.suspended", key, &format!("{} was suspended for prompt bombing; its grants stop; penalty {}", summary::label_of(key), node.fatigue.penalty(key)));
        summary::let_go(node, key, "It was suspended; ask again if it is still needed.")?;
        summary::cancel_pending(node, key, "It was suspended; ask again if it is still needed.")?;
        summary::raise(node, key, "suspended", 0)?;
    }
    Ok(())
}

/// A request of grants went wrong in the way ``cause`` says (the person declined it, blocked it or chose the wrong number, it ran out
/// unanswered, or its requester withdrew it): count it against its requester. Only a person's own answer makes the scopes it asked for
/// count as declined, and only a person's answer counts against the agent and the pool.
pub fn count_against(node: &mut Node, a: &Approval, cause: Cause) -> Result<()> {
    let now = node.now_ms();
    let who = requester_of(node, a);
    let asks = a.asks();
    let scopes: Vec<(&str, &str)> = asks.iter().map(|k| (k["action"].as_str().unwrap_or(""), k["resource"].as_str().unwrap_or(""))).collect();
    if cause == Cause::Person {
        node.fatigue.note_declined(&a.board, &scopes, now)?;
    }
    let actions: Vec<&str> = if a.kind == SIGN { vec![sign::ACTION] } else { scopes.iter().map(|p| p.0).collect() };
    let out = node.fatigue.decline(&who, &kind_of(&actions), cause, now)?;
    report(node, a, &out)
}

/// A phone's addition was denied or ran out: it counts against the session whose code the phone used, which may ask for no more codes
/// for a day.
pub fn count_against_offer(node: &mut Node, a: &Approval, cause: Cause) -> Result<()> {
    let Some((board, name)) = a.by.split_once('/') else { return Ok(()) };
    let now = node.now_ms();
    let root = node.host(board).map_or_else(|_| name.to_string(), |h| h.names.root(name));
    let out = node.fatigue.decline(&requester(board, name, &root), "phone_add", cause, now)?;
    node.fatigue.block(&format!("offer:{}", a.by), now, crate::approvals::fatigue::DAY_MS)?;
    report(node, a, &out)
}

/// Mark the requests that ran out, and write an entry for each; a request nobody answered counts against its requester as a timeout,
/// never as consent. What a hold kept back is let go when the hold ends.
pub fn sweep(node: &mut Node) -> Result<()> {
    let now = node.now_ms();
    for (a, held) in node.approvals.sweep(now)? {
        audit(node, "approval.expired", &a, if held { "held, and never shown" } else { "nobody decided it" });
        if a.kind == PHONE_ADD {
            crate::phone::drop_pending(node, &a);
            count_against_offer(node, &a, Cause::Timeout)?;
        }
        if (a.kind == GRANTS || a.kind == SIGN) && !held {
            count_against(node, &a, Cause::Timeout)?;
        }
    }
    for key in node.fatigue.ended(now)? {
        summary::let_go(node, &key, "It was held, and the hold ended; ask again if it is still needed.")?;
    }
    Ok(())
}

fn ended(state: &str) -> String {
    match state {
        APPROVED => "That request was already approved, so it cannot be decided again.".into(),
        DENIED => "That request was already denied, so it cannot be decided again.".into(),
        EXPIRED => "That request ended before it was decided; ask again if it is still needed.".into(),
        CANCELLED => "That request was withdrawn by the session that made it.".into(),
        HELD => "That request is being held back: nobody was shown it, and a person lets it through or it ends.".into(),
        _ => "That request already ended, so it cannot be decided again.".into(),
    }
}

fn refuse_key(node: &Node, key: &str) -> Error {
    if node.phones.removed(key) {
        return Error::Denied("That phone was removed from this device and can no longer approve.".into());
    }
    if node.persons.was_removed(key) {
        return Error::Denied("That key was removed from this device and can no longer approve.".into());
    }
    Error::Denied("That key is not one of your keys on this device, so nothing was approved.".into())
}

/// Whether ``a`` can be answered with ``decision``: the plain approval is not enough for a scope that was declined before, only a
/// request that offers a block can be answered with one, and a notice that settles nothing takes no block.
fn allowed(a: &Approval, decision: &str) -> Result<()> {
    let blockable = a.context.block_ms > 0;
    match decision {
        "deny" => Ok(()),
        "approve" if a.kind == GRANTS && a.context.repeat > 0 => denied(&format!(
            "That scope was declined {} time(s) before, so approving it again is a separate, deliberate step: the app asks for it before the fingerprint.", a.context.repeat)),
        "approve" => Ok(()),
        "approve_repeat" if a.kind == GRANTS && a.context.repeat > 0 => Ok(()),
        "approve_repeat" => Err(Error::Invalid("That request was not declined before, so it is approved the usual way.".into())),
        "block" if blockable => Ok(()),
        "block" => Err(Error::Invalid("That request does not offer a block.".into())),
        _ => Err(Error::Invalid("A decision is approve, deny or block.".into())),
    }
}

/// The number a person chose, checked against the one the agent showed; true when no number had to be matched. A choice that is not one of
/// the three offered is no answer at all, and nothing changes.
fn picked(a: &Approval, decision: &str, pick: &str) -> Result<bool> {
    if a.pick_code.is_empty() || !decision.starts_with("approve") {
        return Ok(true);
    }
    if !a.context.pick.iter().any(|p| p == pick) {
        return Err(Error::Invalid("Choose one of the three numbers shown, the one the agent showed you.".into()));
    }
    Ok(pick == a.pick_code)
}

/// Decide a request with the signature ``d`` carries. The state changes once, and is on disk before anything is done with it.
pub fn decide(node: &mut Node, d: &Decision) -> Result<Value> {
    sweep(node)?;
    let a = node.approvals.get(d.id).cloned().ok_or_else(|| Error::Invalid("There is no request with that id.".into()))?;
    if a.state != PENDING {
        return denied(&ended(&a.state));
    }
    allowed(&a, d.decision)?;
    if node.persons.get(d.key).is_none() {
        return Err(refuse_key(node, d.key));
    }
    if !d.text_sha256.is_empty() && d.text_sha256 != sha256_hex(a.text.as_bytes()) {
        return denied("The text you were shown is not the text of this request, so nothing was signed.");
    }
    let bytes = if a.kind == SIGN && d.decision.starts_with("approve") { sign::expected(&a, d.key)? } else { challenge::bytes(&challenge::object(&a, &node.cert.fingerprint()), d.decision, d.key) };
    if !node.persons.signed(d.key, &bytes, d.sig) {
        return denied(if d.decision.starts_with("approve") { "That signature does not match this request, so nothing was approved." } else { "That signature does not match this request, so nothing changed." });
    }
    let matched = picked(&a, d.decision, d.pick)?;
    let now = node.now_ms();
    let state = if d.decision.starts_with("approve") && matched { APPROVED } else { DENIED };
    let mismatch = "The number you chose is not the one the agent showed, so nothing was approved and it counts as a decline.";
    node.approvals.update(&a.id, |x| {
        x.state = state.into();
        x.decided_ms = now;
        x.decided_by = d.key.into();
        if !matched {
            x.reason = mismatch.into();
        } else if d.decision == "block" {
            x.reason = "A person denied it and blocked the agent that asked.".into();
        }
    })?;
    audit(node, match (state, d.decision) {
        (APPROVED, _) => "approval.approved",
        (_, "block") => "approval.blocked",
        _ => "approval.denied",
    }, &a, &format!("key {}{}", &d.key[..12], if matched { "" } else { "; the number chosen did not match" }));
    if !matched {
        count_against(node, &a, Cause::Person)?;
        return denied(mismatch);
    }
    let result = if state == APPROVED { approved(node, &a, d, now) } else { declined(node, &a, d.decision, now) };
    if let Err(e) = result {
        let why = e.to_string();
        node.approvals.update(&a.id, |x| {
            x.state = FAILED.into();
            x.reason = why.clone();
        })?;
        if a.kind == PHONE_ADD {
            crate::phone::drop_pending(node, &a);
        }
        return denied(&format!("The signature was good but it could not be carried out: {why}"));
    }
    let done = node.approvals.get(&a.id).cloned().unwrap_or(a);
    Ok(view(node, &done))
}

/// What a person's deny or block does besides ending the request.
fn declined(node: &mut Node, a: &Approval, decision: &str, now: u64) -> Result<()> {
    match a.kind.as_str() {
        PHONE_ADD => {
            crate::phone::drop_pending(node, a);
            count_against_offer(node, a, Cause::Person)?;
        }
        HOLD => summary::apply(node, a, decision)?,
        GRANTS | SIGN => {
            count_against(node, a, Cause::Person)?;
            if decision == "block" {
                let key = requester_of(node, a).keys[0].clone();
                let ms = node.fatigue.rules.block_ms;
                node.fatigue.block(&key, now, ms)?;
                summary::let_go(node, &key, "A person blocked it.")?;
                summary::cancel_pending(node, &key, "A person blocked it.")?;
            }
        }
        _ => {}
    }
    Ok(())
}

/// Do what an approved request asked.
fn approved(node: &mut Node, a: &Approval, d: &Decision, now: u64) -> Result<()> {
    match a.kind.as_str() {
        GRANTS => {
            // Every entry is checked before the first is written, so a refusal leaves no ask live while the agent is told none is.
            let mut entries = Vec::new();
            for body in challenge::grant_entries(a, &node.cert.fingerprint(), now, d.key, d.sig) {
                parse_issue(&body)?;
                let gid = body.get("gid").and_then(Value::as_str).unwrap_or("").to_string();
                let body = Value::Object(body);
                crate::fold::check_local(Kind::Grant, "node", &body, "")?;
                entries.push((gid, body));
            }
            let mut written = Vec::new();
            for (gid, body) in entries {
                if let Err(e) = node.host(&a.board).and_then(|h| h.board.append(Kind::Grant, "node", body, "").map(drop)) {
                    // A write that fails part-way (the disk) leaves the asks already written live; the request names them.
                    let _ = node.approvals.update(&a.id, |x| x.grants = written.clone());
                    return Err(e);
                }
                written.push(gid);
            }
            let who = requester_of(node, a);
            let asks = a.asks();
            let actions: Vec<&str> = asks.iter().map(|k| k["action"].as_str().unwrap_or("")).collect();
            node.fatigue.approved(&who, &kind_of(&actions))?;
            node.approvals.update(&a.id, |x| x.grants = written)
        }
        HOLD => summary::apply(node, a, "approve"),
        PHONE_ADD => crate::phone::activate(node, a),
        PHONE_REMOVE => crate::phone::remove(node, a),
        SIGN => sign::finish(node, a, d),
        _ => Err(Error::Invalid("That kind of request is not known.".into())),
    }
}
