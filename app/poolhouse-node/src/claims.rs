//! Claims, in terms of leases: `claim` takes one `claim` resource without waiting, `release`
//! gives it back, `claims` lists who holds what. A claim is a lease of one resource, so it is
//! dropped when its holder's process dies or its time runs out, like every other lease.

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::lease::rpc;
use crate::lease::table::Event;
use crate::lease::table::Why;
use crate::lease::types::{Lease, Resource, State, CLAIM_KINDS};
use crate::node::Node;

pub const METHODS: [&str; 4] = ["claim", "reserve", "release", "claims"];
/// Most resources one reserve takes.
pub const MOST_RESERVED: usize = 128;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "claim" => &["kind", "key", "ttl_s", "pid", "local"],
        "reserve" => &["resources", "ttl_s", "pid", "local"],
        "release" => &["kind", "key", "held_by"],
        "claims" => &["kind"],
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

fn kind_of<'a>(p: &'a Map<String, Value>, required: bool) -> Result<&'a str> {
    let kind = text(p, "kind")?;
    if kind.is_empty() && !required || CLAIM_KINDS.contains(&kind) && kind != "task" {
        Ok(kind)
    } else {
        Err(Error::Invalid("a claim kind is worktree, branch, area, port, server or install".into()))
    }
}

/// The name a claim is held under: a branch belongs to its board's repository, so two boards
/// may each claim a branch of the same name; ports, servers and paths belong to the device.
fn held_as(kind: &str, board: &str, key: &str) -> String {
    if kind == "branch" { format!("{board}/{key}") } else { key.to_string() }
}

fn shown_as<'a>(kind: &str, board: &str, name: &'a str) -> &'a str {
    if kind == "branch" { name.strip_prefix(board).and_then(|n| n.strip_prefix('/')).unwrap_or(name) } else { name }
}

fn mine(l: &Lease, kind: &str, name: &str) -> bool {
    l.resources.iter().any(|r| matches!(r, Resource::Claim { kind: k, name: n } if k == kind && n == name))
}

/// One claim of a lease as clients see it.
fn show(node: &Node, l: &Lease, kind: &str, name: &str, board: &str) -> Value {
    let key = shown_as(kind, board, name);
    let now = node.leases.now();
    let left = l.expires_ms.saturating_sub(now) / 1000;
    let own = l.board == board;
    json!({"kind": kind, "key": key, "owner": if own { l.name.as_str() } else { "other-board" }, "lease": if own { l.id.as_str() } else { "" },
           "expires_in_s": left, "expiring_soon": left < 300, "pid": if own { l.pid } else { 0 }, "since_ms": l.granted_ms})
}

/// Take the claim ``kind`` ``key`` for ``holder`` without waiting, or renew the one it already holds: whether it was newly taken, and its lease.
/// A claim the holder keeps past its lifetime is given up and taken again, with a lifetime that starts now.
fn take(node: &mut Node, board: &str, token: &str, holder: &str, (kind, key): (&str, &str), p: &Map<String, Value>) -> Result<(bool, Lease)> {
    let name = held_as(kind, board, key);
    if let Some(l) = node.leases.table.leases.iter().find(|l| l.holder == holder && l.state == State::Held && mine(l, kind, &name)).cloned() {
        if l.lifetime_end_ms().is_some_and(|end| end <= node.leases.now()) {
            give_back(node, board, holder, &[l.id])?;
        } else {
            let ttl = p.get("ttl_s").and_then(Value::as_u64).unwrap_or(l.ttl_s);
            let mut renew = Map::new();
            renew.insert("id".into(), json!(l.id));
            renew.insert("ttl_s".into(), json!(ttl));
            rpc::call(node, "lease_renew", board, token, &renew)?;
            return Ok((false, node.leases.table.find(&l.id).cloned().unwrap_or(l)));
        }
    }
    let mut ask = Map::new();
    ask.insert("resources".into(), json!([{"type": "claim", "kind": kind, "name": name}]));
    ask.insert("wait".into(), json!(false));
    for field in ["ttl_s", "pid", "local"] {
        if let Some(v) = p.get(field) {
            ask.insert(field.into(), v.clone());
        }
    }
    let got = rpc::call(node, "lease_acquire", board, token, &ask)?;
    if got["state"] == "busy" {
        let who = got["blockers"].as_array().and_then(|b| b.first()).and_then(|b| b["holder"].as_str()).map_or("another session of yours", |h| h.rsplit('/').next().unwrap_or(h));
        return Err(Error::Denied(format!("{kind} {key} is held by {who}")));
    }
    let l = node.leases.table.find(got["id"].as_str().unwrap_or("")).cloned().ok_or_else(|| Error::Invalid("the claim ended at once".into()))?;
    Ok((true, l))
}

fn claim(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let (kind, key) = (kind_of(p, true)?, text(p, "key")?);
    let access = node.access(token, board, true)?;
    let holder = format!("{board}/{}", access.holder.name);
    crate::writes::check_send(node, &access.holder, "claims")?;
    rpc::tick(node, &[board])?;
    let (changed, l) = take(node, board, token, &holder, (kind, key), p)?;
    crate::writes::sent(node, &access.holder);
    Ok(json!({"changed": changed, "claim": show(node, &l, kind, &held_as(kind, board, key), board)}))
}

/// The ``kind`` and ``key`` of each resource a reserve names: one to `MOST_RESERVED`, each a valid claim, each once.
fn wanted(p: &Map<String, Value>) -> Result<Vec<(String, String)>> {
    let list = p.get("resources").and_then(Value::as_array).ok_or_else(|| Error::Invalid("resources is a list of kind and key".into()))?;
    if list.is_empty() || list.len() > MOST_RESERVED {
        return Err(Error::Invalid(format!("a reserve names one to {MOST_RESERVED} resources")));
    }
    let mut out: Vec<(String, String)> = Vec::new();
    for item in list {
        let one = item.as_object().ok_or_else(|| Error::Invalid("a resource is an object of kind and key".into()))?;
        let (kind, key) = (kind_of(one, true)?, text(one, "key")?);
        Resource::Claim { kind: kind.into(), name: key.into() }.check()?;
        if !out.iter().any(|(k, n)| k == kind && n == key) {
            out.push((kind.into(), key.into()));
        }
    }
    Ok(out)
}

/// The session that holds something ``kind`` ``key`` is in the way of, other than ``holder``, if any does.
fn in_the_way(node: &Node, board: &str, holder: &str, kind: &str, key: &str) -> Option<String> {
    let wanted = Resource::Claim { kind: kind.into(), name: held_as(kind, board, key) };
    node.leases.table.leases.iter()
        .find(|o| o.state == State::Held && o.holder != holder && o.resources.iter().any(|x| wanted.conflicts(x)))
        .map(|o| if o.board == board { o.name.clone() } else { "other-board".into() })
}

/// Give back claims a reserve took before it was refused.
fn give_back(node: &mut Node, board: &str, holder: &str, taken: &[String]) -> Result<()> {
    let mut events = Vec::new();
    for id in taken {
        events.push(Event::Ended(node.leases.table.release(id, holder)?, Why::Released));
    }
    node.leases.save()?;
    rpc::mirror(node, &events)?;
    rpc::tick(node, &[board])
}

/// Take every resource named or none: the whole set is checked against what others hold before the first is taken, in one call of
/// the node, so no other session sees or races a part of it; a resource the caller already holds is renewed and kept if the rest is refused.
fn reserve(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let list = wanted(p)?;
    let access = node.access(token, board, true)?;
    let holder = format!("{board}/{}", access.holder.name);
    rpc::tick(node, &[board])?;
    if let Some((kind, key, who)) = list.iter().find_map(|(k, n)| in_the_way(node, board, &holder, k, n).map(|who| (k, n, who))) {
        return Err(Error::Denied(format!("{kind} {key} is held by {who}")));
    }
    let (mut taken, mut out) = (Vec::new(), Vec::new());
    for (kind, key) in &list {
        match take(node, board, token, &holder, (kind, key), p) {
            Ok((changed, l)) => {
                if changed {
                    taken.push(l.id.clone());
                }
                out.push(json!({"changed": changed, "claim": show(node, &l, kind, &held_as(kind, board, key), board)}));
            }
            Err(e) => {
                give_back(node, board, &holder, &taken)?;
                return Err(e);
            }
        }
    }
    Ok(json!({"claims": out}))
}

/// The session whose claim a release gives up: the caller's own, or one of its spawned subagents'
/// that the caller names, since a parent may free what its child can no longer release.
fn releasing_for(node: &mut Node, board: &str, caller: &crate::identity::Holder, held_by: &str) -> Result<String> {
    if held_by.is_empty() || held_by == caller.name {
        return Ok(caller.name.clone());
    }
    let child = node.host(board)?.names.get(held_by).map(|i| i.parent.clone());
    if caller.board != board || child.as_deref() != Some(caller.name.as_str()) {
        return Err(Error::Denied(format!("{held_by} is not a subagent of {}; only a parent releases its subagent's claim", caller.name)));
    }
    Ok(held_by.to_string())
}

fn release(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let (kind, key) = (kind_of(p, true)?, text(p, "key")?);
    let name = held_as(kind, board, key);
    let access = node.access(token, board, true)?;
    let holder = format!("{board}/{}", releasing_for(node, board, &access.holder, text(p, "held_by")?)?);
    rpc::tick(node, &[board])?;
    let ids: Vec<String> = node.leases.table.leases.iter().filter(|l| l.holder == holder && l.state == State::Held && mine(l, kind, &name)).map(|l| l.id.clone()).collect();
    if ids.is_empty() {
        let other = node.leases.table.leases.iter().find(|l| l.state == State::Held && l.board == board && mine(l, kind, &name)).map(|l| l.name.clone());
        return match other {
            Some(who) => Err(Error::Denied(format!("{kind} {key} is held by {who}; only the holder releases it"))),
            None => Ok(json!({"kind": kind, "key": key, "released": false})),
        };
    }
    let mut events = Vec::new();
    for id in &ids {
        events.push(Event::Ended(node.leases.table.release(id, &holder)?, Why::Released));
    }
    node.leases.save()?;
    rpc::mirror(node, &events)?;
    rpc::tick(node, &[board])?;
    Ok(json!({"kind": kind, "key": key, "released": true}))
}

fn claims(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let kind = kind_of(p, false)?;
    let access = node.access(token, board, false)?;
    if access.channels.as_ref().is_some_and(|c| !c.iter().any(|x| x == "#leases")) {
        return Err(Error::Denied("claims are not shared with this session".into()));
    }
    rpc::tick(node, &[board])?;
    let mut out = Vec::new();
    for l in node.leases.table.leases.iter().filter(|l| l.state == State::Held && l.board == board) {
        for r in &l.resources {
            if let Resource::Claim { kind: k, name } = r {
                if (kind.is_empty() || kind == k) && k != "task" {
                    out.push(show(node, l, k, name, board));
                }
            }
        }
    }
    Ok(json!({"claims": out}))
}

/// The session of ``board`` holding the claim ``kind`` ``key``, if any does.
pub fn owner(node: &mut Node, board: &str, kind: &str, key: &str) -> Result<Option<String>> {
    let name = held_as(kind, board, key);
    rpc::tick(node, &[board])?;
    Ok(node.leases.table.leases.iter().find(|l| l.state == State::Held && l.board == board && mine(l, kind, &name)).map(|l| l.name.clone()))
}

/// The key of a branch claim ``actor`` holds on ``board``, if it holds one.
pub fn owned_branch(node: &mut Node, board: &str, actor: &str) -> Result<Option<String>> {
    rpc::tick(node, &[board])?;
    for l in node.leases.table.leases.iter().filter(|l| l.state == State::Held && l.board == board && l.name == actor) {
        for r in &l.resources {
            if let Resource::Claim { kind, name } = r {
                if kind == "branch" {
                    return Ok(Some(shown_as(kind, board, name).to_string()));
                }
            }
        }
    }
    Ok(None)
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "claim" => claim(node, board, token, p),
        "reserve" => reserve(node, board, token, p),
        "release" => release(node, board, token, p),
        _ => claims(node, board, token, p),
    }
}
