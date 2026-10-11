//! What a session can ask about sessions: `whoami` (and say what model it runs), find the name
//! of a native session, list the sessions of its board, and retire one.

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::identity::Ident;
use crate::lease::table::{Event, Why};
use crate::lineage::{Anchor, Peer, ENFORCED};
use crate::node::Node;
use crate::row::Kind;

pub const METHODS: [&str; 5] = ["whoami", "session_lookup", "agents", "retire", "rebind"];
const REBIND_DOMAIN: &str = "poolhouse/session-rebind/v1\n";

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "whoami" => &["model", "harness"],
        "session_lookup" => &["harness", "session"],
        "agents" => &["retired"],
        "retire" => &["target", "reason"],
        "rebind" => &["harness", "session", "at_ms", "nonce", "anchor", "by", "sig"],
        _ => return None,
    })
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

fn show(name: &str, i: &Ident) -> Value {
    json!({"name": name, "parent": i.parent, "family": i.family, "model": i.model, "model_state": i.model_state,
           "harness": i.harness, "retired": i.retired, "retired_reason": i.retired_reason, "seen_ms": i.seen_ms})
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>, peer: &Peer) -> Result<Value> {
    match method {
        "session_lookup" => lookup(node, board, p),
        "rebind" => rebind(node, board, p, peer),
        "whoami" => whoami(node, board, token, p),
        "agents" => agents(node, board, token, p),
        _ => retire(node, board, token, p),
    }
}

fn lookup(node: &mut Node, board: &str, p: &Map<String, Value>) -> Result<Value> {
    let hosted = node.boards.get(board).ok_or_else(|| Error::Denied("this node does not host that board".into()))?;
    match hosted.names.find(text(p, "harness")?, text(p, "session")?) {
        Some(name) => Ok(json!({"name": name})),
        None => Err(Error::Denied("no session of that harness and id is registered on this board".into())),
    }
}

/// What an enrolled person key signs to move the main session ``name`` of ``board`` to the terminal ``anchor`` names (the
/// command runs in it), on the node ``device``, once.
fn moving(board: &str, name: &str, device: &str, nonce: &str, at_ms: u64, anchor: &Anchor) -> Vec<u8> {
    let body = json!({"op": "rebind", "board": board, "name": name, "node": device, "nonce": nonce, "at_ms": at_ms, "anchor": anchor.body()});
    format!("{REBIND_DOMAIN}{}", crate::row::canonical(&body)).into_bytes()
}

/// Move a main session to the lineage of the process asking, on the signature of an enrolled person key over this
/// request and the terminal it was signed in: the session registers from there from now on, and the tokens it held end.
fn rebind(node: &mut Node, board: &str, p: &Map<String, Value>, peer: &Peer) -> Result<Value> {
    let hosted = node.boards.get(board).ok_or_else(|| Error::Denied("this node does not host that board".into()))?;
    let name = hosted.names.find(text(p, "harness")?, text(p, "session")?).map(str::to_string)
        .ok_or_else(|| Error::Denied("no session of that harness and id is registered on this board".into()))?;
    let lineage = peer.lineage();
    if ENFORCED && lineage.is_empty() {
        return Err(Error::Denied("the process asking could not be read, so the session cannot be moved to it".into()));
    }
    let at = crate::grants::calls::fresh(node, p, "at_ms")?;
    let (by, nonce, anchor) = (text(p, "by")?, text(p, "nonce")?, Anchor::signed(p)?);
    if nonce.len() < 16 || nonce.len() > 64 || !nonce.bytes().all(|b| b.is_ascii_alphanumeric()) {
        return Err(Error::Invalid("a move carries a nonce of 16 to 64 letters and digits".into()));
    }
    if !node.persons.signed(by, &moving(board, &name, &node.cert.fingerprint(), nonce, at, &anchor), text(p, "sig")?) {
        return Err(Error::Denied("an enrolled person key signs the move of a session, for this node and the terminal it is made in".into()));
    }
    if !lineage.contains(&anchor) {
        return Err(Error::Denied("the move was signed in another terminal than the one asking".into()));
    }
    if !node.persons.once(nonce, node.now_ms())? {
        return Err(Error::Denied("that approval was used already".into()));
    }
    node.host(board)?.names.rebind(&name, &lineage)?;
    let ended = node.tokens.revoke_name(board, &name)?;
    let detail = format!("moved by the person key {}: {ended} tokens ended", by.chars().take(16).collect::<String>());
    node.host(board)?.board.append(Kind::Audit, "node", json!({"event": "rebind", "subject": name, "detail": detail}), "")?;
    Ok(json!({"name": name, "moved": true, "tokens_ended": ended}))
}

fn whoami(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let (model, harness) = (text(p, "model")?, text(p, "harness")?);
    if !(model.is_empty() && harness.is_empty()) {
        if access.holder.board != board {
            return Err(Error::Denied("a model is claimed on the session's own board".into()));
        }
        if node.host(board)?.names.claim_model(&access.actor, model, harness)? {
            node.write_identity(board, &access.actor)?;
        }
    }
    let mut out = json!({"name": access.actor, "board": access.holder.board, "target": board});
    if let Some(i) = node.host(board)?.names.get(&access.actor) {
        out["identity"] = show(&access.actor, i);
    }
    Ok(out)
}

fn agents(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, false)?;
    if access.channels.as_ref().is_some_and(|c| !c.iter().any(|x| x == "#identity")) {
        return Err(Error::Denied("identities are not shared with this session".into()));
    }
    let all = p.get("retired").and_then(Value::as_bool).unwrap_or(false);
    let list: Vec<Value> = node.host(board)?.names.all().iter().filter(|(_, i)| all || !i.retired).map(|(n, i)| show(n, i)).collect();
    Ok(json!({"agents": list}))
}

fn retire(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    if access.holder.board != board {
        return Err(Error::Denied("only a session of the board retires its sessions".into()));
    }
    let (caller, asked) = (access.holder.name.clone(), text(p, "target")?);
    let reason = match text(p, "reason")? {
        "" | "done" => "done",
        cause @ ("revoked" | "forged") => cause,
        _ => return Err(Error::Invalid("a session ends done, revoked or forged".into())),
    };
    let target = if asked.is_empty() { caller.clone() } else { asked.to_string() };
    let names = &node.host(board)?.names;
    let ident = names.get(&target).ok_or_else(|| Error::Denied("no such session".into()))?;
    if ident.retired {
        return Err(Error::Denied(format!("{target} is already retired")));
    }
    if target == caller {
        if reason != "done" {
            return Err(Error::Denied("a session cannot revoke or flag itself; its parent does".into()));
        }
        if ident.parent.is_empty() {
            return Err(Error::Denied("only a spawned subagent retires itself; a main session ends with its harness".into()));
        }
    } else if !names.descendants(&caller).contains(&target) {
        return Err(Error::Denied(format!("{target} is not below {caller}; only a parent retires its subagent")));
    }
    let freed = release_leases(node, board, &target)?;
    let revoked = node.tokens.revoke_name(board, &target)?;
    node.host(board)?.names.retire(&target, reason)?;
    node.write_identity(board, &target)?;
    let detail = format!("retired by {caller} ({reason}): {revoked} tokens revoked, {freed} leases released");
    node.host(board)?.board.append(Kind::Audit, &caller, json!({"event": "retire", "subject": target, "detail": detail}), "")?;
    Ok(json!({"name": target, "retired": true, "reason": reason, "by": caller, "tokens_revoked": revoked, "leases_released": freed}))
}

/// End every lease of ``name``, held or queued, and tell the board; how many there were.
fn release_leases(node: &mut Node, board: &str, name: &str) -> Result<usize> {
    let holder = format!("{board}/{name}");
    let ids: Vec<String> = node.leases.table.leases.iter().filter(|l| l.holder == holder).map(|l| l.id.clone()).collect();
    let mut events = Vec::new();
    for id in &ids {
        events.push(Event::Ended(node.leases.table.release(id, &holder)?, Why::Released));
    }
    if !ids.is_empty() {
        node.leases.save()?;
        crate::lease::rpc::mirror(node, &events)?;
        crate::lease::rpc::tick(node, &[board])?;
    }
    Ok(ids.len())
}
