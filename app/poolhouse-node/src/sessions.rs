//! What a session can ask about sessions: `whoami` (and say what model it runs), find the name
//! of a native session, list the sessions of its board (marking the idle ones), retire one, and take one over (`rebind`).

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::identity::Ident;
use crate::lease::table::{Event, Why};
use crate::lineage::{Anchor, Peer, ENFORCED};
use crate::node::Node;
use crate::row::Kind;

pub const METHODS: [&str; 5] = ["whoami", "session_lookup", "agents", "retire", "rebind"];

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "whoami" => &["model", "harness"],
        "session_lookup" => &["harness", "session"],
        "agents" => &["retired"],
        "retire" => &["target", "reason"],
        "rebind" => &["harness", "session"],
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

fn show(name: &str, i: &Ident, idle: bool) -> Value {
    json!({"name": name, "parent": i.parent, "family": i.family, "model": i.model, "model_state": i.model_state,
           "harness": i.harness, "retired": i.retired, "retired_reason": i.retired_reason, "person": i.person, "seen_ms": i.seen_ms, "idle": idle})
}

/// Whether ``i`` is idle: not retired and neither heard from lately nor holding a live lease.
fn idle_now(node: &mut Node, board: &str, name: &str, i: &Ident) -> Result<bool> {
    let now = node.host(board)?.board.now_ms();
    Ok(!i.retired && !crate::writes::is_live(node, board, name, i, now))
}

/// The sessions of every board this node keeps that run in the process tree ``here`` (the lineage of a request, read by the peer id of its
/// socket): the registered, not retired sessions that have a process in common with ``here`` that still runs. The process at the top of a
/// session's own lineage is the shell of the terminal it was started in, which stays after the agent has gone, so a session counts for that
/// one process alone only while the session is live. A caller that none of them runs in is a process of no agent the node knows: a
/// terminal, or a process that left its tree. Each is its board, name, parent (empty for a main session), harness and whether it is a person's session; nothing else.
pub fn running_in(node: &mut Node, here: &[Anchor]) -> Result<Vec<Value>> {
    if !ENFORCED || here.is_empty() {
        return Ok(Vec::new());
    }
    let boards: Vec<String> = node.boards.keys().cloned().collect();
    let mut found = Vec::new();
    for board in boards {
        let kept: Vec<(String, Ident)> = node.host(&board)?.names.all().iter().filter(|(_, i)| !i.retired).map(|(n, i)| (n.clone(), i.clone())).collect();
        for (name, ident) in kept {
            let top = ident.lineage.last().copied();
            let shared: Vec<&Anchor> = ident.lineage.iter().filter(|a| here.contains(a) && crate::lease::live::is_alive(a.pid, Some(a.start))).collect();
            let beyond_top = shared.iter().any(|a| Some(**a) != top);
            if beyond_top || (!shared.is_empty() && !idle_now(node, &board, &name, &ident)?) {
                found.push(json!({"board": board, "name": name, "parent": ident.parent, "harness": ident.harness, "person": ident.person}));
            }
        }
    }
    Ok(found)
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
        Some(name) => {
            let ident = hosted.names.get(name).expect("found above");
            Ok(json!({"name": name, "incarnation": ident.incarnation, "parent": ident.parent}))
        }
        None => Err(Error::Denied("no session of that harness and id is registered on this board".into())),
    }
}

/// Take a main session over now: it is registered from the lineage of the process asking from now on and the tokens it held end, even
/// when a process of the lineage it had still runs. Nothing requires this: a session registers from any process by itself (a lineage
/// that ended moves it, a live sibling gets a token of its own, `Registry::admit`) and a client whose token ended registers again. It is for
/// the one case where a live sibling is to give way at once: the terminal the person is at takes the session's grants from the one that runs it.
/// The move is written to the board.
fn rebind(node: &mut Node, board: &str, p: &Map<String, Value>, peer: &Peer) -> Result<Value> {
    let hosted = node.boards.get(board).ok_or_else(|| Error::Denied("this node does not host that board".into()))?;
    let name = hosted.names.find(text(p, "harness")?, text(p, "session")?).map(str::to_string)
        .ok_or_else(|| Error::Denied("no session of that harness and id is registered on this board".into()))?;
    let lineage = peer.lineage();
    if ENFORCED && lineage.is_empty() {
        return Err(Error::Denied("the process asking could not be read, so the session cannot be moved to it".into()));
    }
    carry_out(node, board, &name, &lineage, "a command run in the terminal it moves to")
}

/// Register the main session ``name`` from ``lineage`` from now on, end the tokens it held and say so on the board; ``by`` is who moved it.
pub fn carry_out(node: &mut Node, board: &str, name: &str, lineage: &[Anchor], by: &str) -> Result<Value> {
    let eligible = node.host(board)?.names.get(name).is_some_and(|i| !i.retired && i.parent.is_empty());
    if !eligible {
        return Err(Error::Denied("only a main session that has not retired is moved; a retired one comes back when its harness registers it".into()));
    }
    let ended = node.tokens.revoke_name(board, name)?;
    node.host(board)?.names.rebind(name, lineage)?;
    node.regroup(board)?;
    let detail = format!("moved by {by}: {ended} tokens ended");
    node.host(board)?.board.append_person(Kind::Audit, "node", json!({"event": "rebind", "subject": name, "detail": detail}))?;
    Ok(json!({"name": name, "moved": true, "tokens_ended": ended}))
}

fn whoami(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let (model, harness) = (text(p, "model")?, text(p, "harness")?);
    if !(model.is_empty() && harness.is_empty()) {
        if access.holder.board != board {
            return Err(Error::Denied("a model is claimed on the session's own board".into()));
        }
        let before = node.host(board)?.names.get(&access.actor).map(|i| (i.model.clone(), i.harness.clone()));
        if node.host(board)?.names.claim_model(&access.actor, model, harness)? {
            let after = node.host(board)?.names.get(&access.actor).map(|i| (i.model.clone(), i.harness.clone()));
            if before != after {
                node.write_identity(board, &access.actor)?;
            }
        }
    }
    let mut out = json!({"name": access.actor, "board": access.holder.board, "target": board});
    if let Some(i) = node.host(board)?.names.get(&access.actor).cloned() {
        let idle = idle_now(node, board, &access.actor, &i)?;
        out["identity"] = show(&access.actor, &i, idle);
    }
    Ok(out)
}

fn agents(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, false)?;
    if access.channels.as_ref().is_some_and(|c| !c.iter().any(|x| x == "#identity")) {
        return Err(Error::Denied("identities are not shared with this session".into()));
    }
    let all = p.get("retired").and_then(Value::as_bool).unwrap_or(false);
    crate::idle::sweep(node, board)?;
    let kept: Vec<(String, Ident)> = node.host(board)?.names.all().iter().filter(|(_, i)| all || !i.retired).map(|(n, i)| (n.clone(), i.clone())).collect();
    let mut list = Vec::new();
    for (name, ident) in &kept {
        list.push(show(name, ident, idle_now(node, board, name, ident)?));
    }
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
    let main = ident.parent.is_empty();
    if target == caller {
        if reason != "done" {
            return Err(Error::Denied("a session cannot revoke or flag itself; its parent does".into()));
        }
    } else if !names.descendants(&caller).contains(&target) {
        return Err(Error::Denied(format!("{target} is not below {caller}; only a parent retires its subagent")));
    }
    let (revoked, freed) = end(node, board, &target, &caller, reason)?;
    let below = if main { end_below(node, board, &target, &caller, reason)? } else { 0 };
    Ok(json!({"name": target, "retired": true, "reason": reason, "by": caller, "tokens_revoked": revoked, "leases_released": freed,
              "subagents_retired": below}))
}

/// End ``target`` and say so on the board with one identity entry that names who ended it and what that released: its tokens, its leases,
/// its subscriptions and its name; how many of each of the first two there were. The entry is skipped when the log has no room for it: the
/// registry is the record of a session.
pub fn end(node: &mut Node, board: &str, target: &str, by: &str, reason: &str) -> Result<(usize, usize)> {
    let freed = release_leases(node, board, target)?;
    let revoked = node.tokens.revoke_name(board, target)?;
    let parent = node.host(board)?.names.get(target).map(|i| i.parent.clone()).unwrap_or_default();
    node.host(board)?.names.retire(target, reason)?;
    node.host(board)?.subs.drop_for(target)?;
    let detail = format!("retired by {by} ({reason}): {revoked} tokens revoked, {freed} leases released");
    match node.write_ended(board, target, (by, &detail)) {
        Ok(()) | Err(Error::Quota(_)) => {}
        Err(e) => return Err(e),
    }
    if reason == "idle" && !parent.is_empty() {
        let said = format!("{target} was retired for idleness: it held no lease and the node heard nothing from it for {} hours, so its resources were freed. \
                            Its token no longer works; it registers again under you at its next use, and nothing else is needed.", node.writes.idle_retire_s / 3600);
        crate::notice::tell(node, board, &parent, "subagent retired", &said)?;
    }
    Ok((revoked, freed))
}

/// End every subagent below the main session ``top`` that is not retired; how many there were.
fn end_below(node: &mut Node, board: &str, top: &str, by: &str, reason: &str) -> Result<usize> {
    let names = &node.host(board)?.names;
    let live: Vec<String> = names.descendants(top).into_iter().filter(|n| names.get(n).is_some_and(|i| !i.retired)).collect();
    for name in &live {
        end(node, board, name, by, reason)?;
    }
    Ok(live.len())
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
