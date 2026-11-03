//! Making a request: what is checked, and what the node adds that the agent did not write.
//!
//! Every request is shown to the person when it is made. The same request made again is the same item and returns the open one; a
//! session with a request open that asks for something else is told what waits and that the person is to answer it, so the person is
//! never handed two at once from one asker and the agent never waits on a timer. A session a person blocked is refused with the end of
//! the block named. Nothing else holds a request back: not a decline, not how often it was asked, not how many were pushed.

use serde_json::{json, Map, Value};

use crate::approvals::answered::{same, BLOCK_MS};
use crate::approvals::calls::{asks_of, plain_error, plain_line, text, ttl_of, Ask};
use crate::approvals::content::{grants_text, risk_of, riskiest, utc, Draft, Line, Who, ROUTINE};
use crate::approvals::context::{pick_options, AskFacts, Context};
use crate::approvals::decide::{agent_view, audit, sweep};
use crate::approvals::flags::{for_grants, Asked};
use crate::approvals::{Approval, GRANTS, MOST_PENDING};
use crate::error::{Error, Result};
use crate::fsutil::random_hex;
use crate::grants::authority::Authority;
use crate::identity::ModelState;
use crate::node::Node;

const MOST_NOTE: usize = 160;

/// Whether ``source`` is this device or a member of the pool: a request from anywhere else says so on its card.
fn trusted(node: &Node, source: &str) -> bool {
    source == node.cert.fingerprint() || node.members.get(source).is_some()
}

/// The request that is already waiting and is the same as the one just made: it is asked again, not made again.
fn again(node: &mut Node, found: &Approval) -> Result<Value> {
    node.approvals.update(&found.id, |x| x.retries += 1)?;
    let fresh = node.approvals.get(&found.id).cloned().unwrap_or_else(|| found.clone());
    let mut out = agent_view(node, &fresh);
    out["deduplicated"] = json!(true);
    Ok(out)
}

/// What a session that asks for something while one of its requests waits is told: what waits, where, and what the person does now.
pub fn waiting_words(open: &Approval) -> String {
    format!("You already have a request waiting for the person: \"{}\" ({}). It is on their phone now. Tell them to open the Poolhouse app and answer it, and ask for the next thing after they have; another request from this session is not opened while this one waits.", open.title, open.id)
}

/// The lines that say what looks off, beyond what the session's history says (`flags`): a scope declined before, a request nobody asked
/// for and a request from outside the pool.
fn flags_of(base: Vec<String>, ctx: &Context, trusted: bool) -> Vec<String> {
    let mut out = base;
    if ctx.repeat > 0 {
        out.push(format!("This was declined {} time(s) before. Approving it again takes one more deliberate step, and it is asked on its own.", ctx.repeat));
    }
    if ctx.prompted == "no" {
        out.push("You gave this agent no instruction to do this; it asked on its own.".into());
    }
    if !trusted {
        out.push("This request came from a device that is not a member of your pool.".into());
    }
    out
}

/// Everything a request needs before it is made, checked: the asks, the words and the pool it is made for.
struct Checked {
    asks: Vec<Ask>,
    note: String,
    task: String,
    ttl: u64,
}

fn check(node: &mut Node, p: &Map<String, Value>) -> Result<Checked> {
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
    Ok(Checked { asks, note, task, ttl })
}

/// Make the request of a session that reached this node from the device ``source``: the asks, the text the node wrote for them and the
/// challenge a key signs, and a request the person is told about, or the plain reason there is none.
pub fn request_from(node: &mut Node, source: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    access.of_board(board, "asks for approvals")?;
    let name = access.actor.clone();
    sweep(node)?;
    let Checked { asks, note, task, ttl } = check(node, p)?;
    let authority = Authority::of(node);
    if authority.governed() && !authority.any() {
        return Err(Error::Denied("No admin phone of this pool is added to this device, so there is nobody here to ask: add the admin's phone to this device, then ask again.".into()));
    }
    let scopes: Vec<(&str, &str)> = asks.iter().map(|a| (a.action.as_str(), a.resource.as_str())).collect();
    let same = same(board, &name, &scopes);
    if let Some(found) = node.approvals.all().iter().find(|a| a.pending() && a.same == same).cloned() {
        return again(node, &found);
    }
    if let Some(open) = node.approvals.open_of(GRANTS, board, &name) {
        return Err(Error::Denied(waiting_words(open)));
    }
    let now = node.now_ms();
    if let Some(until) = node.answered.blocked_until(&format!("{board}/{name}"), now) {
        return Err(Error::Denied(format!("A person blocked this session until {} when they answered an earlier request of it, so nothing it asks is shown to them before then. Tell the person in your own words what you need.", utc(until))));
    }
    let (model, harness, parent, verified) = node.host(board)?.names.get(&name)
        .map(|i| (i.model.clone(), i.harness.clone(), i.parent.clone(), i.model_state == ModelState::Verified)).unwrap_or_default();
    let actions: Vec<&str> = asks.iter().map(|a| a.action.as_str()).collect();
    if node.approvals.pending_count() >= MOST_PENDING {
        return Err(crate::limits::floor("As many requests as this device keeps already wait for the person. Tell them to open the Poolhouse app and answer some, then ask again."));
    }
    let repeat = node.answered.declined_before(board, &scopes, now);
    if repeat > 0 && asks.len() > 1 {
        return Err(Error::Invalid("A scope that was declined before is asked for on its own: make one request for it.".into()));
    }
    let unknown = !task.is_empty() && !task_known(node, token, board, &task);
    let ids: Vec<Asked> = asks.iter().map(|a| Asked { action: &a.action, resource: &a.resource, lasts_ms: a.lasts_ms, uses: a.uses }).collect();
    let policy = node.members.policy.name();
    let found = for_grants(node, board, &name, &ids, unknown)?;
    let risk = riskiest(asks.iter().map(|a| risk_of(&a.action).0));
    let (pick_code, options) = if risk == ROUTINE { (String::new(), Vec::new()) } else { pick_options()? };
    let facts = node.evidence.facts(board, &name, &asks.iter().map(|a| AskFacts { action: &a.action, resource: &a.resource }).collect::<Vec<_>>());
    let ctx = Context::from_facts(&facts, repeat, options, BLOCK_MS);
    let friend = trusted(node, source);
    let flags = flags_of(found.flags, &ctx, friend);
    let device_fp = node.cert.fingerprint();
    let device = node.members.get(&device_fp).map(|d| d.name.clone()).unwrap_or_default();
    let expires_ms = now + ttl;
    let draft = Draft {
        who: Who { name: &name, model: &model, harness: &harness, verified, parent: &parent, board, device: &device },
        lines: asks.iter().map(|a| Line { action: &a.action, resource: &a.resource, lasts_ms: a.lasts_ms, uses: a.uses, review: a.review }).collect(),
        policy, flags, notes: found.notes, ctx: &ctx, note: &note, task: &task, task_known: !unknown, created_ms: now, expires_ms,
    };
    let (title, text, risk) = grants_text(&draft);
    if !crate::approvals::challenge::plain_text(&text) {
        return Err(Error::Invalid("That request is too long to show a person; ask for fewer things or shorter names.".into()));
    }
    let grants: Vec<Value> = asks.iter().map(|a| grant_of(a, &device_fp)).collect::<Result<_>>()?;
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: GRANTS.into(), board: board.into(), session: name.clone(), action: json!({"kind": GRANTS, "grants": grants}),
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms,
        context: ctx, pick_code, same, source: source.into(), ..Approval::empty()
    };
    node.approvals.add(approval.clone())?;
    audit(node, "approval.request", &approval, &actions.join(","));
    crate::phone::announce(node, &approval);
    let done = node.approvals.get(&approval.id).cloned().unwrap_or(approval);
    Ok(agent_view(node, &done))
}

fn grant_of(a: &Ask, device_fp: &str) -> Result<Value> {
    let mut one = json!({"action": a.action, "device": device_fp, "gid": format!("g-{}", random_hex(8)?), "lasts_ms": a.lasts_ms, "resource": a.resource, "review": a.review});
    if let Some(n) = a.uses {
        one["uses"] = json!(n);
    }
    Ok(one)
}

/// Whether the board has the task ``task`` that the caller's token may read.
fn task_known(node: &mut Node, token: &str, board: &str, task: &str) -> bool {
    let mut p = Map::new();
    p.insert("task".into(), json!(task));
    crate::tasks::calls::call(node, "task_get", board, token, &p).is_ok()
}
