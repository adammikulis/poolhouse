//! Deciding a request: the checks on the key and the signature, the one change of state, and what an approval does.

use serde_json::{json, Value};

use crate::approvals::{challenge, Approval, APPROVED, CANCELLED, DENIED, EXPIRED, FAILED, GRANTS, PENDING, PHONE_ADD, PHONE_REMOVE};
use crate::error::{Error, Result};
use crate::fsutil::sha256_hex;
use crate::grants::spec::parse_issue;
use crate::node::Node;
use crate::row::Kind;

/// A decision as it arrives: the request, `approve` or `deny`, the signing key's fingerprint, the signature in hex and, when the
/// client says which text it showed, the SHA-256 of that text.
pub struct Decision<'a> {
    pub id: &'a str,
    pub decision: &'a str,
    pub key: &'a str,
    pub sig: &'a str,
    pub text_sha256: &'a str,
}

fn denied<T>(why: &str) -> Result<T> {
    Err(Error::Denied(why.into()))
}

/// The request ``id`` as a client reads it: its state and text, and the challenge a key signs.
pub fn view(node: &Node, a: &Approval) -> Value {
    json!({"id": a.id, "kind": a.kind, "state": a.state, "title": a.title, "risk": a.risk, "board": a.board, "session": a.session, "text": a.text,
           "created_ms": a.created_ms, "expires_ms": a.expires_ms, "decided_ms": a.decided_ms, "decided_by": a.decided_by, "reason": a.reason,
           "grants": a.grants, "challenge": challenge::object(a, &node.cert.fingerprint())})
}

/// Write an audit entry about the request ``a`` to the pool board: its id, the session and what happened, never its text.
pub fn audit(node: &mut Node, event: &str, a: &Approval, detail: &str) {
    let who = if a.session.is_empty() { "a phone".to_string() } else { format!("{}/{}", a.board, a.session) };
    let _ = node.record_event(event, &a.id, &format!("{who}; {detail}"));
}

/// Mark the requests that ran out, and write an entry for each.
pub fn sweep(node: &mut Node) -> Result<()> {
    let now = node.now_ms();
    for a in node.approvals.sweep(now)? {
        audit(node, "approval.expired", &a, "nobody decided it");
        if a.kind == PHONE_ADD {
            crate::phone::drop_pending(node, &a);
        }
    }
    Ok(())
}

fn ended(state: &str) -> String {
    match state {
        APPROVED => "That request was already approved, so it cannot be decided again.".into(),
        DENIED => "That request was already denied, so it cannot be decided again.".into(),
        EXPIRED => "That request ended before it was decided; ask again if it is still needed.".into(),
        CANCELLED => "That request was withdrawn by the session that made it.".into(),
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

/// Decide a request with the signature ``d`` carries. The state changes once, and is on disk before anything is done with it.
pub fn decide(node: &mut Node, d: &Decision) -> Result<Value> {
    sweep(node)?;
    let a = node.approvals.get(d.id).cloned().ok_or_else(|| Error::Invalid("There is no request with that id.".into()))?;
    if a.state != PENDING {
        return denied(&ended(&a.state));
    }
    if d.decision != "approve" && d.decision != "deny" {
        return Err(Error::Invalid("A decision is approve or deny.".into()));
    }
    if node.persons.get(d.key).is_none() {
        return Err(refuse_key(node, d.key));
    }
    if !d.text_sha256.is_empty() && d.text_sha256 != sha256_hex(a.text.as_bytes()) {
        return denied("The text you were shown is not the text of this request, so nothing was signed.");
    }
    let bytes = challenge::bytes(&challenge::object(&a, &node.cert.fingerprint()), d.decision, d.key);
    if !node.persons.signed(d.key, &bytes, d.sig) {
        return denied(if d.decision == "approve" { "That signature does not match this request, so nothing was approved." } else { "That signature does not match this request, so nothing changed." });
    }
    let now = node.now_ms();
    let state = if d.decision == "approve" { APPROVED } else { DENIED };
    node.approvals.update(&a.id, |x| {
        x.state = state.into();
        x.decided_ms = now;
        x.decided_by = d.key.into();
    })?;
    audit(node, if state == APPROVED { "approval.approved" } else { "approval.denied" }, &a, &format!("key {}", &d.key[..12]));
    if state == APPROVED {
        if let Err(e) = apply(node, &a, d, now) {
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
    } else if a.kind == PHONE_ADD {
        crate::phone::drop_pending(node, &a);
    }
    let done = node.approvals.get(&a.id).cloned().unwrap_or(a);
    Ok(view(node, &done))
}

/// Do what an approved request asked.
fn apply(node: &mut Node, a: &Approval, d: &Decision, now: u64) -> Result<()> {
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
            node.approvals.update(&a.id, |x| x.grants = written)
        }
        PHONE_ADD => crate::phone::activate(node, a),
        PHONE_REMOVE => crate::phone::remove(node, a),
        _ => Err(Error::Invalid("That kind of request is not known.".into())),
    }
}
