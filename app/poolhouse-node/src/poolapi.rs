//! The local API for the pool: `pool_status`, `member_remove`, the governance calls, and the
//! network ones (`add_device`, `join_pool`, `sync_now`) which `net::Net` answers.
//!
//! Reading the pool needs no token (the socket is the owner's); changing it needs a registered
//! session that holds the grant (`grants::check`), which writes the use to the board.

use serde_json::{json, Map, Value};

use crate::cert::valid_fingerprint;
use crate::error::{Error, Result};
use crate::grants::Scope;
use crate::identity::Holder;
use crate::lineage::{Asking, Peer};
use crate::membership::Policy;
use crate::node::Node;

/// The methods that act on the network and so run without the node lock held.
pub const NETWORK: [&str; 4] = ["add_device", "join_pool", "sync_now", "shard_call"];

/// The params each pool method accepts; None for any other method.
pub fn params(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "pool_status" | "sync_now" => &[],
        "member_remove" => &["fingerprint"],
        "pool_create" => &["title", "policy", "project"],
        "pool_leave" => &[],
        "pool_propose" => &["kind", "body"],
        "pool_cosign" => &["act"],
        "pool_submit" => &["act"],
        "pool_draft" => &["kind", "body"],
        "pool_found" => &["act", "charter"],
        "pool_draft_found" => &["title", "policy", "project", "keys"],
        "add_device" => &["passphrase", "ttl_s", "presigned"],
        "join_pool" => &["host", "port", "passphrase", "fingerprint"],
        "shard_call" => &["device", "op", "args"],
        "shard_consent" => &["enabled", "python", "repo", "allow", "deny"],
        _ => return None,
    })
}

pub fn handles(method: &str) -> bool {
    params(method).is_some()
}

/// The session behind ``token``, if it holds a grant for ``action`` on ``resource``; nothing is spent. For what can still fail
/// once it is allowed: `spend` the use after it was done.
pub fn require(node: &mut Node, token: &str, action: &str, resource: &str) -> Result<Holder> {
    let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("a registered session is needed".into()))?;
    if !crate::grants::holds(node, &holder, action, &Scope::on(resource))? {
        return Err(crate::grants::refused_for(node, token, action, resource, &format!("this session does not hold the grant for {action}")));
    }
    Ok(holder)
}

/// Write the use of the grant ``holder`` holds for ``action`` on ``resource`` to its board.
pub fn spend(node: &mut Node, token: &str, holder: &Holder, action: &str, resource: &str) -> Result<()> {
    if !crate::grants::check(node, holder, action, &Scope::on(resource))? {
        return Err(crate::grants::refused_for(node, token, action, resource, &format!("this session does not hold the grant for {action}")));
    }
    Ok(())
}

/// The session behind ``token``, if it holds a grant for ``action`` on ``resource``; the use is written to its board.
pub fn authorize(node: &mut Node, token: &str, action: &str, resource: &str) -> Result<Holder> {
    let holder = require(node, token, action, resource)?;
    spend(node, token, &holder, action, resource)?;
    Ok(holder)
}

/// `authorize` for a request the process ``peer`` made, outside the dispatch of the API (the methods that run without the node lock held).
pub fn authorize_from(node: &mut Node, token: &str, peer: &Peer, action: &str, resource: &str) -> Result<Holder> {
    node.asking = Asking::of(token, peer);
    let held = authorize(node, token, action, resource);
    node.asking = Asking::default();
    held
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        Some(Value::String(s)) => Ok(s),
        None => Ok(""),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

pub fn dispatch(node: &mut Node, method: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "pool_status" => {
            node.gov_tick()?;
            Ok(node.pool_status())
        }
        "member_remove" => {
            let fingerprint = text(p, "fingerprint")?;
            let who = authorize(node, token, method, fingerprint)?;
            if !valid_fingerprint(fingerprint) {
                return Err(Error::Invalid("a device is named by its fingerprint, 64 hex digits".into()));
            }
            if fingerprint == node.cert.fingerprint() {
                return Err(Error::Denied("a device does not revoke itself; leave by removing its state".into()));
            }
            let device = node.remove_device(fingerprint)?;
            if crate::shard::consent::forget(&node.dir, fingerprint)? {
                node.record_event("shard_allow", fingerprint, &format!("removed: put out of the pool by {}/{}", who.board, who.name))?;
            }
            Ok(json!({"fingerprint": device.fingerprint, "status": device.status}))
        }
        "pool_create" => {
            let (name, policy) = (text(p, "title")?, text(p, "policy")?);
            authorize(node, token, method, name)?;
            node.pool_create(name, if policy.is_empty() { Policy::Secure } else { Policy::parse(policy)? }, text(p, "project")?)
        }
        "pool_leave" => {
            authorize(node, token, method, "*")?;
            node.pool_leave()
        }
        "pool_found" => {
            let act = crate::gov::ops::founding_act(p.get("act"), p.get("charter"))?;
            let name = crate::gov::ops::founding_name(&act)?;
            authorize(node, token, "pool_create", &name)?;
            node.pool_found(&act)
        }
        "pool_draft_found" => node.pool_draft_found(text(p, "title")?, Policy::parse(text(p, "policy")?)?, text(p, "project")?, p.get("keys").unwrap_or(&Value::Null)),
        "pool_draft" => node.pool_draft(text(p, "kind")?, p.get("body").cloned().unwrap_or(Value::Null)),
        "pool_propose" => {
            let kind = text(p, "kind")?;
            authorize(node, token, "admin_manage", kind)?;
            node.pool_propose(kind, p.get("body").cloned().unwrap_or(Value::Null))
        }
        "pool_cosign" => {
            authorize(node, token, "admin_manage", "cosign")?;
            node.pool_cosign(text(p, "act")?)
        }
        "pool_submit" => {
            node.pool_submit(p.get("act").unwrap_or(&Value::Null))
        }
        "shard_consent" => crate::shard::consent_call(node, token, p),
        _ => Err(Error::Invalid("this node's network is not running".into())),
    }
}
