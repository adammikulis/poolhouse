//! Reviews of commits as board entries. A review names a commit, the branch it was written under and a
//! verdict, and the node stamps it with the reviewer's name and model. A review is advice; the push check asks
//! which reviews of the commit being pushed stand, and a runtime deployment of an unmerged commit asks for an accept.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fold::Entry;
use crate::grants::Scope;
use crate::node::Node;
use crate::row::{is_line, Kind};

pub const METHODS: [&str; 3] = ["land_review", "land_accepts", "land_trust"];
/// The grant a session needs, besides having no parent, to name another device whose entries count.
pub const TRUST: &str = "land_trust";
/// Events of the landing queue that no longer exists; boards still hold them and they count for nothing.
const RETIRED: [&str; 6] = ["request", "cancel", "pause", "resume", "state", "beat"];
const REVIEW_KEYS: [&str; 5] = ["ev", "branch", "sha", "verdict", "standing"];

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "land_review" => &["branch", "sha", "verdict"],
        "land_accepts" => &["sha"],
        "land_trust" => &["device", "trusted"],
        _ => return None,
    })
}

fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

fn word(map: &Map<String, Value>, key: &str, most: usize) -> Result<String> {
    match map.get(key) {
        None => Ok(String::new()),
        Some(Value::String(s)) if s.len() <= most && is_line(s) => Ok(s.trim().to_string()),
        _ => bad(&format!("{key} is one line of at most {most} characters")),
    }
}

fn plain_branch(name: &str) -> bool {
    let first = name.bytes().next().is_some_and(|b| b.is_ascii_alphanumeric());
    first && name.len() <= 128 && name.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'/' | b'-')) && !matches!(name, "main" | "master")
}

fn full_sha(sha: &str) -> bool {
    sha.len() == 40 && sha.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Check the fields of one landing entry, with the rules a peer applies to a row it receives.
pub fn check(map: &Map<String, Value>) -> Result<()> {
    let ev = word(map, "ev", 16)?;
    if RETIRED.contains(&ev.as_str()) || map.contains_key("req") {
        return Ok(());
    }
    if ev != "review" || map.keys().any(|k| !REVIEW_KEYS.contains(&k.as_str())) {
        return bad("a landing entry is a review of a branch, a commit and a verdict");
    }
    if !plain_branch(&word(map, "branch", 128)?) {
        return bad("a review names a plain branch, never main");
    }
    if !full_sha(&word(map, "sha", 64)?) || !matches!(word(map, "verdict", 8)?.as_str(), "accept" | "reject") {
        return bad("a review names the full commit and a verdict, accept or reject");
    }
    match map.get("standing") {
        None => Ok(()),
        Some(Value::Object(st)) if st.len() <= 3 && standing_fits(st) => Ok(()),
        _ => bad("a stamped standing is the sender's name, model and model state"),
    }
}

/// Whether a stamped standing holds only short lines.
fn standing_fits(st: &Map<String, Value>) -> bool {
    st.iter().all(|(k, v)| matches!(k.as_str(), "name" | "model" | "model_state") && v.as_str().is_some_and(|t| t.len() <= 256 && is_line(t)))
}

fn field(e: &Entry, key: &str) -> String {
    e.fields.get(key).and_then(Value::as_str).unwrap_or("").to_string()
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> &'a str {
    p.get(key).and_then(Value::as_str).unwrap_or("")
}

/// What the node knows of ``name`` now, stamped on the entry it writes: the name, the model and how far it is believed.
fn stamp(node: &mut Node, board: &str, name: &str) -> Result<Value> {
    let ident = node.host(board)?.names.get(name).ok_or_else(|| Error::Denied("no such session".into()))?;
    Ok(json!({"name": name, "model": ident.model, "model_state": ident.model_state}))
}

fn write(node: &mut Node, board: &str, actor: &str, body: Value) -> Result<Value> {
    crate::fold::check_local(Kind::Landing, actor, &body, "")?;
    let row = node.host(board)?.board.append(Kind::Landing, actor, body, "")?;
    Ok(json!({"seq": row.seq, "id": row.id()}))
}

fn audit(node: &mut Node, board: &str, actor: &str, event: &str, subject: &str, detail: &str) -> Result<()> {
    let clip = |s: &str| s.chars().filter(|c| !c.is_control()).take(300).collect::<String>();
    node.host(board)?.board.append(Kind::Audit, actor, json!({"event": event, "subject": clip(subject), "detail": clip(detail)}), "")?;
    Ok(())
}

/// The session of this board behind ``token``, live; a retired or linked-in session does not use the reviews.
fn member(node: &mut Node, token: &str, board: &str) -> Result<String> {
    let access = node.access(token, board, true)?;
    let retired = node.host(board)?.names.get(&access.actor).is_none_or(|i| i.retired);
    if access.holder.board != board || retired {
        return Err(Error::Denied("only a live session of this board uses its reviews".into()));
    }
    Ok(access.actor)
}

fn review(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let (branch, sha, verdict) = (text(p, "branch"), text(p, "sha"), text(p, "verdict"));
    let body = json!({"ev": "review", "branch": branch, "sha": sha, "verdict": verdict, "standing": stamp(node, board, &actor)?});
    write(node, board, &actor, body)?;
    audit(node, board, &actor, "land.review", &format!("{branch}@{sha}"), verdict)?;
    Ok(json!({"branch": branch, "sha": sha, "verdict": verdict}))
}

/// The reviews of ``sha`` written on this device under any branch name: the last of each reviewer other than the caller.
fn accepts(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let caller = member(node, token, board)?;
    let sha = text(p, "sha");
    if !full_sha(sha) {
        return bad("accepts names the full commit");
    }
    let own = node.host(board)?.board.origin().to_string();
    let entries = node.view(board)?;
    let mut last: BTreeMap<String, &Entry> = BTreeMap::new();
    for e in entries.iter().filter(|e| e.kind == Kind::Landing && e.origin == own && field(e, "ev") == "review" && !e.fields.contains_key("req")) {
        if field(e, "sha") == sha {
            last.insert(e.sender.clone(), e);
        }
    }
    let reviews: Vec<Value> = last.into_iter()
        .filter(|(by, _)| *by != caller)
        .map(|(by, e)| json!({"by": by, "branch": field(e, "branch"), "verdict": field(e, "verdict"), "ts_ms": e.hlc.0, "standing": e.fields.get("standing").cloned().unwrap_or(Value::Null)}))
        .collect();
    Ok(json!({"sha": sha, "reviews": reviews}))
}

/// Name a device whose entries this device counts, or stop counting them: a session with no parent
/// that holds the grant ``land_trust``, recorded on the board.
fn trust(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = member(node, token, board)?;
    let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("the token is not known".into()))?;
    let top = node.host(board)?.names.get(&actor).is_some_and(|i| i.parent.is_empty());
    let message = "only a session with no parent that holds the grant land_trust names the devices whose entries count";
    let fingerprint = text(p, "device");
    if !top || !crate::grants::holds(node, &holder, TRUST, &Scope::on(fingerprint))? {
        return Err(crate::grants::refused_for(node, token, TRUST, fingerprint, message));
    }
    let on = p.get("trusted").and_then(Value::as_bool).ok_or_else(|| Error::Invalid("trusted is true or false".into()))?;
    let device = node.members.get(fingerprint).cloned();
    if on && !device.as_ref().is_some_and(|d| d.status == crate::membership::Status::Active) {
        return Err(Error::Invalid("only an active member of this pool is trusted".into()));
    }
    if fingerprint == node.cert.fingerprint() {
        return Err(Error::Invalid("this device's own entries always count".into()));
    }
    if !crate::grants::check(node, &holder, TRUST, &Scope::on(fingerprint))? {
        return Err(crate::grants::refused_for(node, token, TRUST, fingerprint, message));
    }
    let pool = node.members.id.clone();
    let changed = node.land_trust.set(&pool, fingerprint, on)?;
    let name = device.map(|d| d.name).unwrap_or_default();
    audit(node, board, &actor, if on { "land.trust" } else { "land.untrust" }, fingerprint, &name)?;
    Ok(json!({"device": fingerprint, "trusted": on, "changed": changed}))
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "land_review" => review(node, board, token, p),
        "land_accepts" => accepts(node, board, token, p),
        _ => trust(node, board, token, p),
    }
}
