//! The approval methods of the local API. A session asks, reads what became of its own requests and withdraws them; deciding
//! needs a signature of a person's key, which a session only carries.

use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::approvals::content::{grants_text, Draft, Line, Who};
use crate::approvals::decide::{audit, decide, sweep, view, Decision};
use crate::approvals::flags::{for_grants, Asked};
use crate::approvals::{challenge, Approval, CANCELLED, DEFAULT_TTL_MS, GRANTS, MAX_TTL_MS, MIN_TTL_MS, MOST_PENDING, MOST_PENDING_PER_SESSION, MOST_REQUESTS, PENDING, REQUEST_WINDOW_MS};
use crate::error::{Error, Result};
use crate::fsutil::random_hex;
use crate::grants::spec::{check_resource, grantable};
use crate::node::Node;

pub const METHODS: [&str; 6] = ["approval_request", "approval_status", "approval_cancel", "approvals", "approval_pending", "approval_decide"];
const MOST_NOTE: usize = 160;
const MOST_USES: u64 = 1000;
const MAX_LASTS_MS: u64 = 30 * 24 * 3600 * 1000;
const MIN_LASTS_MS: u64 = 60 * 1000;
const LISTED: usize = 20;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "approval_request" => &["asks", "note", "task", "ttl_s", "expect_policy"],
        "approval_status" | "approval_cancel" => &["id"],
        "approvals" => &["state"],
        "approval_pending" => &[],
        "approval_decide" => &["id", "decision", "key", "sig", "text_sha256"],
        _ => return None,
    })
}

/// The methods that name no board: they carry a signature or read what is pending.
pub fn boardless(method: &str) -> bool {
    matches!(method, "approval_pending" | "approval_decide")
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        Some(Value::String(s)) => Ok(s),
        None => Ok(""),
        _ => Err(Error::Invalid(format!("{key} is text."))),
    }
}

fn plain_error() -> Error {
    Error::Invalid("That text must be plain English letters, digits and punctuation.".into())
}

/// ``text`` as one plain line with quote marks and backslashes made harmless, or the refusal.
fn plain_line(text: &str, most: usize) -> Result<String> {
    let text: String = text.trim().chars().map(|c| if c == '"' { '\'' } else if c == '\\' { '/' } else { c }).collect();
    if text.len() > most || !text.bytes().all(|b| b.is_ascii_graphic() || b == b' ') {
        return Err(plain_error());
    }
    Ok(text)
}

/// One ask as the agent wrote it, checked.
struct Ask {
    action: String,
    resource: String,
    lasts_ms: u64,
    uses: Option<u64>,
    review: bool,
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

fn asks_of(p: &Map<String, Value>) -> Result<Vec<Ask>> {
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

fn ttl_of(p: &Map<String, Value>) -> Result<u64> {
    match p.get("ttl_s") {
        None => Ok(DEFAULT_TTL_MS),
        Some(v) => v.as_u64().and_then(|s| s.checked_mul(1000)).filter(|ms| (MIN_TTL_MS..=MAX_TTL_MS).contains(ms))
            .ok_or_else(|| Error::Invalid("A request lives from one minute to 24 hours.".into())),
    }
}

/// Whether the board has the task ``task`` that the caller's token may read.
fn task_known(node: &mut Node, token: &str, board: &str, task: &str) -> bool {
    let mut p = Map::new();
    p.insert("task".into(), json!(task));
    crate::tasks::calls::call(node, "task_get", board, token, &p).is_ok()
}

/// Make the request of a session: the asks, the text the node wrote for them and the challenge a key signs.
fn request(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    access.of_board(board, "asks for approvals")?;
    let name = access.actor.clone();
    sweep(node)?;
    let asks = asks_of(p)?;
    let (note, task, ttl) = (plain_line(text(p, "note")?, MOST_NOTE)?, plain_line(text(p, "task")?, 40)?, ttl_of(p)?);
    if task.contains(' ') {
        return Err(plain_error());
    }
    let policy = node.members.policy.name();
    let claimed = text(p, "expect_policy")?;
    if !claimed.is_empty() && claimed != policy {
        return Err(Error::Invalid(format!("The pool is {policy}, not {claimed} as you wrote; ask again with what is true.")));
    }
    let key = format!("approval {board}/{name}");
    if !node.rates.permits(&key, MOST_REQUESTS, Duration::from_millis(REQUEST_WINDOW_MS)) {
        return Err(Error::Quota(format!("{MOST_REQUESTS} requests in ten minutes is the most one session may make; try again later.")));
    }
    if node.approvals.pending_of(&name) >= MOST_PENDING_PER_SESSION || node.approvals.pending_count() >= MOST_PENDING {
        return Err(Error::Quota("Too many of your requests are waiting; wait for an answer or cancel one.".into()));
    }
    let now = node.now_ms();
    let (device_fp, device) = (node.cert.fingerprint(), node.members.get(&node.cert.fingerprint()).map(|d| d.name.clone()).unwrap_or_default());
    let unknown = !task.is_empty() && !task_known(node, token, board, &task);
    let ids: Vec<Asked> = asks.iter().map(|a| Asked { action: &a.action, resource: &a.resource, lasts_ms: a.lasts_ms, uses: a.uses }).collect();
    let flags = for_grants(node, board, &name, &ids, policy, unknown)?;
    let (model, harness, parent) = node.host(board)?.names.get(&name).map(|i| (i.model.clone(), i.harness.clone(), i.parent.clone())).unwrap_or_default();
    let expires_ms = now + ttl;
    let draft = Draft {
        who: Who { name: &name, model: &model, harness: &harness, parent: &parent, board, device: &device },
        lines: asks.iter().map(|a| Line { action: &a.action, resource: &a.resource, lasts_ms: a.lasts_ms, uses: a.uses, review: a.review }).collect(),
        policy, flags, note: &note, task: &task, task_known: !unknown, created_ms: now, expires_ms,
    };
    let (title, text, risk) = grants_text(&draft);
    if !challenge::plain_text(&text) {
        return Err(Error::Invalid("That request is too long to show a person; ask for fewer things or shorter names.".into()));
    }
    let mut grants = Vec::new();
    for a in &asks {
        let mut one = json!({"action": a.action, "device": device_fp, "gid": format!("g-{}", random_hex(8)?), "lasts_ms": a.lasts_ms, "resource": a.resource, "review": a.review});
        if let Some(n) = a.uses {
            one["uses"] = json!(n);
        }
        grants.push(one);
    }
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: GRANTS.into(), board: board.into(), session: name.clone(), action: json!({"kind": GRANTS, "grants": grants}),
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms, state: PENDING.into(), decided_ms: 0, decided_by: String::new(),
        reason: String::new(), grants: Vec::new(), seq: 0,
    };
    node.rates.record(&key, Duration::from_millis(REQUEST_WINDOW_MS));
    node.approvals.add(approval.clone())?;
    let kinds: Vec<&str> = asks.iter().map(|a| a.action.as_str()).collect();
    audit(node, "approval.request", &approval, &kinds.join(","));
    crate::phone::announce(node, &approval);
    let done = node.approvals.get(&approval.id).cloned().unwrap_or(approval);
    Ok(view(node, &done))
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
    if !a.pending() {
        return Err(Error::Denied(format!("That request already ended ({}), so it cannot be withdrawn.", a.state)));
    }
    let now = node.now_ms();
    node.approvals.update(id, |x| {
        x.state = CANCELLED.into();
        x.decided_ms = now;
    })?;
    audit(node, "approval.cancelled", &a, "withdrawn by the session");
    let done = node.approvals.get(id).cloned().unwrap_or(a);
    Ok(view(node, &done))
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "approval_request" => request(node, board, token, p),
        "approval_cancel" => cancel(node, board, token, text(p, "id")?),
        "approval_status" => {
            let access = node.access(token, board, false)?;
            sweep(node)?;
            let a = own(node, board, &access.actor, text(p, "id")?)?;
            Ok(view(node, &a))
        }
        "approvals" => {
            let access = node.access(token, board, false)?;
            sweep(node)?;
            let state = text(p, "state")?;
            let mut mine: Vec<&Approval> = node.approvals.all().iter().filter(|a| a.board == board && a.session == access.actor && (state.is_empty() || a.state == state)).collect();
            mine.sort_by_key(|a| std::cmp::Reverse(a.seq));
            let shown: Vec<Value> = mine.into_iter().take(LISTED).map(|a| view(node, a)).collect();
            Ok(json!({"approvals": shown}))
        }
        "approval_pending" => {
            node.tokens.resolve(token).ok_or_else(|| Error::Denied("A registered session reads what is waiting.".into()))?;
            sweep(node)?;
            Ok(json!({"approvals": pending(node)}))
        }
        _ => {
            let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("A registered session carries a decision.".into()))?;
            let id = text(p, "id")?;
            if node.approvals.get(id).is_some_and(|a| a.board == holder.board && a.session == holder.name) {
                return Err(Error::Denied("The session that asked cannot carry the answer to its own request.".into()));
            }
            decide(node, &Decision { id, decision: text(p, "decision")?, key: text(p, "key")?, sig: text(p, "sig")?, text_sha256: text(p, "text_sha256")? })
        }
    }
}

/// Every request waiting for a person, oldest first, as a client reads it.
pub fn pending(node: &Node) -> Vec<Value> {
    node.approvals.all().iter().filter(|a| a.pending()).map(|a| view(node, a)).collect()
}

