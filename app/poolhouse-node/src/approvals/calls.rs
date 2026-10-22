//! The approval methods of the local API. A session asks, reads what became of its own requests and withdraws them; deciding
//! needs a signature of a person's key, which a session only carries.

use serde_json::{json, Map, Value};

use crate::approvals::decide::{agent_view, audit, count_against, decide, sweep, view, Decision};
use crate::approvals::fatigue::Cause;
use crate::approvals::{challenge, request, Approval, CANCELLED, DEFAULT_TTL_MS, MAX_TTL_MS, MIN_TTL_MS};
use crate::error::{Error, Result};
use crate::grants::authority::Authority;
use crate::grants::spec::{check_resource, grantable};
use crate::node::Node;

pub const METHODS: [&str; 6] = ["approval_request", "approval_status", "approval_cancel", "approvals", "approval_pending", "approval_decide"];
const MOST_USES: u64 = 1000;
const MAX_LASTS_MS: u64 = 30 * 24 * 3600 * 1000;
const MIN_LASTS_MS: u64 = 60 * 1000;
const LISTED: usize = 20;
/// How long a person's signature to read every waiting request is good for.
const LIST_FRESH_MS: u64 = 30_000;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "approval_request" => &["asks", "note", "task", "ttl_s", "expect_policy"],
        "approval_status" | "approval_cancel" => &["id"],
        "approvals" => &["state"],
        "approval_pending" => &["key", "sig", "at_ms"],
        "approval_decide" => &["id", "decision", "key", "sig", "text_sha256", "pick"],
        _ => return None,
    })
}

/// The methods that name no board: they carry a signature or read what is pending.
pub fn boardless(method: &str) -> bool {
    matches!(method, "approval_pending" | "approval_decide")
}

pub(crate) fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        Some(Value::String(s)) => Ok(s),
        None => Ok(""),
        _ => Err(Error::Invalid(format!("{key} is text."))),
    }
}

pub(crate) fn plain_error() -> Error {
    Error::Invalid("That text must be plain English letters, digits and punctuation.".into())
}

/// ``text`` as one plain line with quote marks and backslashes made harmless, or the refusal.
pub(crate) fn plain_line(text: &str, most: usize) -> Result<String> {
    let text: String = text.trim().chars().map(|c| if c == '"' { '\'' } else if c == '\\' { '/' } else { c }).collect();
    if text.len() > most || !text.bytes().all(|b| b.is_ascii_graphic() || b == b' ') {
        return Err(plain_error());
    }
    Ok(text)
}

/// One ask as the agent wrote it, checked.
pub(crate) struct Ask {
    pub action: String,
    pub resource: String,
    pub lasts_ms: u64,
    pub uses: Option<u64>,
    pub review: bool,
}

fn read_ask(v: &Value) -> Result<Ask> {
    let map = v.as_object().ok_or_else(|| Error::Invalid("Each ask is an object with an action, a resource and a time.".into()))?;
    if let Some(k) = map.keys().find(|k| !["action", "resource", "lasts_ms", "uses", "review"].contains(&k.as_str())) {
        return Err(Error::Invalid(format!("An ask has no field {}.", k.chars().filter(char::is_ascii_graphic).take(30).collect::<String>())));
    }
    let action = map.get("action").and_then(Value::as_str).unwrap_or("");
    grantable(action)?;
    let resource = match map.get("resource") {
        None => "*",
        Some(Value::String(s)) => s,
        _ => return Err(Error::Invalid("A resource is text.".into())),
    };
    check_resource(resource)?;
    let lasts_ms = map.get("lasts_ms").and_then(Value::as_u64).filter(|n| (MIN_LASTS_MS..=MAX_LASTS_MS).contains(n))
        .ok_or_else(|| Error::Invalid("An ask lasts from one minute to thirty days.".into()))?;
    let uses = match map.get("uses") {
        None | Some(Value::Null) => None,
        Some(v) => Some(v.as_u64().filter(|n| (1..=MOST_USES).contains(n)).ok_or_else(|| Error::Invalid("Uses is a whole number from 1 to 1000.".into()))?),
    };
    let review = match map.get("review") {
        None => false,
        Some(Value::Bool(b)) => *b,
        _ => return Err(Error::Invalid("Review is true or false.".into())),
    };
    Ok(Ask { action: action.into(), resource: resource.into(), lasts_ms, uses, review })
}

pub(crate) fn asks_of(p: &Map<String, Value>) -> Result<Vec<Ask>> {
    let list = p.get("asks").and_then(Value::as_array).filter(|a| (1..=challenge::MOST_ASKS).contains(&a.len()))
        .ok_or_else(|| Error::Invalid("A request holds one to eight asks.".into()))?;
    let asks = list.iter().map(read_ask).collect::<Result<Vec<_>>>()?;
    for (i, a) in asks.iter().enumerate() {
        if asks[..i].iter().any(|b| b.action == a.action && b.resource == a.resource) {
            return Err(Error::Invalid(format!("{} on {} is asked twice.", a.action, a.resource)));
        }
    }
    Ok(asks)
}

pub(crate) fn ttl_of(p: &Map<String, Value>) -> Result<u64> {
    match p.get("ttl_s") {
        None => Ok(DEFAULT_TTL_MS),
        Some(v) => v.as_u64().and_then(|s| s.checked_mul(1000)).filter(|ms| (MIN_TTL_MS..=MAX_TTL_MS).contains(ms))
            .ok_or_else(|| Error::Invalid("A request lives from one minute to 24 hours.".into())),
    }
}

/// The request ``id`` if the session ``name`` of ``board`` made it.
fn own(node: &Node, board: &str, name: &str, id: &str) -> Result<Approval> {
    let a = node.approvals.get(id).ok_or_else(|| Error::Invalid("There is no request with that id.".into()))?;
    if a.board != board || a.session != name {
        return Err(Error::Denied("That request was made by another session, so you cannot read it.".into()));
    }
    Ok(a.clone())
}

fn cancel(node: &mut Node, board: &str, token: &str, id: &str) -> Result<Value> {
    let access = node.access(token, board, true)?;
    sweep(node)?;
    let a = own(node, board, &access.actor, id)?;
    if !a.live() {
        return Err(Error::Denied(format!("That request already ended ({}), so it cannot be withdrawn.", a.state)));
    }
    let now = node.now_ms();
    node.approvals.update(id, |x| {
        x.state = CANCELLED.into();
        x.decided_ms = now;
    })?;
    audit(node, "approval.cancelled", &a, "withdrawn by the session");
    if a.notified {
        // Withdrawing a request the person was told about is a decline of it, so asking and withdrawing again and again holds the requester.
        count_against(node, &a, Cause::Cancel)?;
    }
    let done = node.approvals.get(id).cloned().unwrap_or(a);
    Ok(agent_view(node, &done))
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "approval_request" => {
            let source = node.cert.fingerprint();
            request::request_from(node, &source, board, token, p)
        }
        "approval_cancel" => cancel(node, board, token, text(p, "id")?),
        "approval_status" => {
            let access = node.access(token, board, false)?;
            sweep(node)?;
            let a = own(node, board, &access.actor, text(p, "id")?)?;
            Ok(agent_view(node, &a))
        }
        "approvals" => {
            let access = node.access(token, board, false)?;
            sweep(node)?;
            let state = text(p, "state")?;
            let mut mine: Vec<&Approval> = node.approvals.all().iter().filter(|a| a.board == board && a.session == access.actor && (state.is_empty() || a.state == state)).collect();
            mine.sort_by_key(|a| std::cmp::Reverse(a.seq));
            let shown: Vec<Value> = mine.into_iter().take(LISTED).map(|a| agent_view(node, a)).collect();
            Ok(json!({"approvals": shown}))
        }
        "approval_pending" => {
            let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("A registered session reads what is waiting.".into()))?;
            sweep(node)?;
            let key = text(p, "key")?;
            if key.is_empty() {
                // A session reads its own requests and nobody else's: what another asked, its note and its number are not for it.
                let mine: Vec<Value> = node.approvals.all().iter().filter(|a| a.pending() && a.board == holder.board && a.session == holder.name).map(|a| agent_view(node, a)).collect();
                return Ok(json!({"approvals": mine}));
            }
            let (at, now) = (p.get("at_ms").and_then(Value::as_u64).unwrap_or(0), node.now_ms());
            let signed = at.abs_diff(now) <= LIST_FRESH_MS && node.persons.signed(key, &challenge::list_bytes(&node.cert.fingerprint(), at), text(p, "sig")?);
            if signed && !Authority::of(node).decides(key) {
                return Err(Error::Denied("That key is not an admin key of this pool, so it reads no requests.".into()));
            }
            if !signed {
                return Err(Error::Denied("Only the signature of a person's key, made just now, reads every request that waits.".into()));
            }
            Ok(json!({"approvals": pending(node)}))
        }
        _ => {
            let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("A registered session carries a decision.".into()))?;
            let id = text(p, "id")?;
            if node.approvals.get(id).is_some_and(|a| a.board == holder.board && a.session == holder.name) {
                return Err(Error::Denied("The session that asked cannot carry the answer to its own request.".into()));
            }
            decide(node, &Decision { id, decision: text(p, "decision")?, key: text(p, "key")?, sig: text(p, "sig")?, text_sha256: text(p, "text_sha256")?, pick: text(p, "pick")? })
        }
    }
}

/// Every request waiting for a person, oldest first, as a client reads it.
pub fn pending(node: &Node) -> Vec<Value> {
    node.approvals.all().iter().filter(|a| a.pending()).map(|a| view(node, a)).collect()
}

