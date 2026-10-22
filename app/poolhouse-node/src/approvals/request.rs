//! Making a request: what is checked, what the node adds that the agent did not write, and how loudly the person is told.
//!
//! The same request made again is the same item. A requester that is blocked, suspended or too soon after a decline is refused; one that
//! is held has what it asks kept back, unseen. Everything else is a request the person is told about, as loudly as the caps allow.

use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::approvals::calls::{asks_of, plain_error, plain_line, text, ttl_of, Ask};
use crate::approvals::content::{grants_text, risk_of, riskiest, Draft, Line, Who, ROUTINE};
use crate::approvals::context::{pick_options, AskFacts, Context};
use crate::approvals::decide::{agent_view, audit, sweep};
use crate::approvals::fatigue::{self, kind_of, same, Delivery, Standing, SILENT, URGENT};
use crate::approvals::flags::{for_grants, Asked};
use crate::approvals::{summary, Approval, CANCELLED, EXPIRED, GRANTS, HELD, MOST_PENDING, MOST_PENDING_PER_SESSION, MOST_REQUESTS, PENDING, REQUEST_WINDOW_MS};
use crate::error::{Error, Result};
use crate::fsutil::random_hex;
use crate::grants::authority::Authority;
use crate::identity::ModelState;
use crate::node::Node;

const MOST_NOTE: usize = 160;

/// How loudly a request of the requester ``asker`` (`board/name`, empty when no session asked) is delivered, and whether it is only quiet
/// because a cap was reached. A phone's addition or removal goes through the same caps as an agent's request.
pub fn classify(node: &mut Node, asker: &str, friend: bool, prompted: &str, now: u64) -> (&'static str, bool) {
    let live: Vec<&Approval> = node.approvals.all().iter().filter(|a| a.pending() && a.urgency == URGENT).collect();
    let mine = if asker.is_empty() { 0 } else { live.iter().filter(|a| a.asker() == asker).count() };
    let (all_urgent, by) = (live.len(), if asker.is_empty() { String::new() } else { format!("session:{asker}") });
    let delivery = Delivery { trusted: friend, prompted, requester_urgent: mine, all_urgent, pushes: node.fatigue.pushes_this_hour(now),
                              requester_pushes: if by.is_empty() { 0 } else { node.fatigue.pushes_by(&by, now) } };
    fatigue::urgency(&node.fatigue.rules, &delivery)
}

/// Whether ``source`` is this device or a member of the pool: a request from anywhere else never interrupts the person.
fn trusted(node: &Node, source: &str) -> bool {
    source == node.cert.fingerprint() || node.members.get(source).is_some()
}

fn wait_words(until_ms: u64, now: u64) -> String {
    let secs = until_ms.saturating_sub(now).div_ceil(1000);
    if secs >= 3600 { format!("{} hours", secs.div_ceil(3600)) } else if secs >= 60 { format!("{} minutes", secs.div_ceil(60)) } else { format!("{secs} seconds") }
}

/// The request that is already waiting and is the same as the one just made: it is asked again, not made again.
fn again(node: &mut Node, found: &Approval) -> Result<Value> {
    node.approvals.update(&found.id, |x| x.retries += 1)?;
    if found.state == HELD {
        summary::raise(node, &found.held_by, "held", asked_of(node, &found.held_by))?;
    }
    let fresh = node.approvals.get(&found.id).cloned().unwrap_or_else(|| found.clone());
    let mut out = agent_view(node, &fresh);
    out["deduplicated"] = json!(true);
    Ok(out)
}

/// How many times the requests kept back at ``key`` were asked, counting each time again.
fn asked_of(node: &Node, key: &str) -> u64 {
    node.approvals.all().iter().filter(|a| a.state == HELD && a.held_by == key).map(|a| 1 + a.retries).sum()
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

/// Everything a request needs before it is made, checked: the asks, the words, the limits and the standing of the requester.
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
/// challenge a key signs, and either a request the person is told about, one that is kept back, or a refusal.
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
    if let Some(found) = node.approvals.all().iter().find(|a| a.live() && a.same == same).cloned() {
        return again(node, &found);
    }
    let now = node.now_ms();
    let (model, harness, parent, verified) = node.host(board)?.names.get(&name)
        .map(|i| (i.model.clone(), i.harness.clone(), i.parent.clone(), i.model_state == ModelState::Verified)).unwrap_or_default();
    let who = fatigue::requester(board, &name, &node.host(board)?.names.root(&name));
    let actions: Vec<&str> = asks.iter().map(|a| a.action.as_str()).collect();
    let held_key = match node.fatigue.standing(&who, &kind_of(&actions), now) {
        Standing::Refused { until_ms, cause } => {
            return Err(Error::Denied(if cause == "blocked" { format!("A person blocked this agent, so it cannot ask for {} more.", wait_words(until_ms, now)) }
                                     else { format!("This agent is suspended for {} more after asking again and again; a person can lift that from their phone.", wait_words(until_ms, now)) }));
        }
        Standing::Backoff { .. } => {
            return Err(crate::limits::floor("That kind of request was declined a moment ago; wait a little before asking for it again."));
        }
        Standing::Held { key, .. } => Some(key),
        Standing::Free => None,
    };
    let reask = node.fatigue.rules.reask_ms;
    let repeated = node.approvals.all().iter().any(|a| a.same == same && matches!(a.state.as_str(), CANCELLED | EXPIRED) && now.saturating_sub(a.decided_ms) < reask);
    let rate = format!("approval {board}/{name}");
    if !node.rates.permits(&rate, MOST_REQUESTS, Duration::from_millis(REQUEST_WINDOW_MS)) {
        return Err(crate::limits::floor("One session has made as many requests as it may; try again later."));
    }
    let kept = held_key.as_ref().map_or(0, |k| node.approvals.all().iter().filter(|a| a.state == HELD && a.held_by == *k).count());
    if kept >= node.fatigue.rules.most_held {
        return Err(crate::limits::floor("Your requests are being held back and as many as will be kept are waiting; ask again when a person has answered."));
    }
    if node.approvals.pending_of(&name) >= MOST_PENDING_PER_SESSION || node.approvals.pending_count() >= MOST_PENDING {
        return Err(crate::limits::floor("Too many of your requests are waiting; wait for an answer or cancel one."));
    }
    let repeat = node.fatigue.declined_before(board, &scopes, now);
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
    let ctx = Context::from_facts(&facts, repeat, options, node.fatigue.rules.block_ms);
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
    let (urgency, overflow) = match &held_key {
        Some(_) => (SILENT, false),
        None => match classify(node, &format!("{board}/{name}"), friend, &ctx.prompted, now) {
            // The same request again soon after it was withdrawn or ran out is listed, not pushed again.
            (URGENT, _) if repeated => (SILENT, false),
            other => other,
        },
    };
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: GRANTS.into(), board: board.into(), session: name.clone(), action: json!({"kind": GRANTS, "grants": grants}),
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms, state: if held_key.is_some() { HELD } else { PENDING }.into(),
        context: ctx, pick_code, urgency: urgency.into(), same, source: source.into(), held_by: held_key.clone().unwrap_or_default(), ..Approval::empty()
    };
    node.rates.record(&rate, Duration::from_millis(REQUEST_WINDOW_MS));
    node.approvals.add(approval.clone())?;
    let kinds = actions.join(",");
    match &held_key {
        Some(key) => {
            audit(node, "approval.kept_back", &approval, &format!("{kinds}; held at {key}"));
            summary::raise(node, key, "held", asked_of(node, key))?;
        }
        None => {
            audit(node, "approval.request", &approval, &kinds);
            crate::phone::announce(node, &approval);
            if overflow {
                let waiting = node.approvals.all().iter().filter(|a| a.pending() && a.urgency == SILENT).count() as u64;
                summary::raise(node, summary::FLOOD, "flood", waiting)?;
            }
        }
    }
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
