//! A request that the person's phone sign one act of the pool: the card, and what the node does with the signature.
//!
//! The node never holds a person's key and cannot wait for the phone inside a call (its lock is held while it works), so it makes
//! a request of kind `sign` that carries the exact bytes to sign and the structure they come from, tells the phone, and finishes the
//! work when the phone's decision arrives (`finish`). The phone signs those bytes themselves, not a challenge about them, so the
//! signature is the one every member of the pool checks. A `deny` is signed over the challenge like any other.
//!
//! The text of the card is made here from the structured act, in plain words, with the key's fingerprint in groups of four. A key
//! of a phone added to this node counts as strong: its every signature needs the person's fingerprint on the phone.

use std::time::Duration;

use serde_json::{json, Value};

use crate::approvals::content::{pool_line, utc, IRREVERSIBLE, SENSITIVE};
use crate::approvals::context::{AskFacts, Context};
use crate::approvals::decide::{agent_view, audit, Decision};
use crate::approvals::fatigue::{kind_of, Standing};
use crate::approvals::{request, Approval, DEFAULT_TTL_MS, MOST_PENDING, MOST_PENDING_PER_SESSION, MOST_REQUESTS, PENDING, REQUEST_WINDOW_MS, SIGN};
use crate::error::{Error, Result};
use crate::fsutil::{hex, random_hex};
use crate::gov::act::{Act, ActSig, SigKind};
use crate::gov::alg::Alg;
use crate::gov::body::Body;
use crate::gov::charter::Charter;
use crate::gov::keys::{AdminKey, Role, Strength};
use crate::gov::ops::{class_of, declined, no_pool, not_admin};
use crate::membership::Policy;
use crate::node::Node;
use crate::phone::{words, ACTIVE};
use crate::poolops::wall_ms;

/// What the fatigue ledger and the evidence source know a request to sign an act as.
pub const ACTION: &str = "pool_sign";
const MOST_LABEL: usize = 40;
/// A signature of the form a P-256 key makes, in the place of the acceptance the phone has not made yet, so the rest of the charter can be checked.
const SHAPED: &str = "3006020101020101";

pub const NEEDS_PHONE: &str = "A pool needs a key that asks for you each time it signs, which is your phone; link your phone in the Poolhouse app and tell me again.";

/// ``text`` as the printable ASCII a card is made of: anything else becomes a hyphen.
fn ascii(text: &str, most: usize) -> String {
    text.chars().take(most).map(|c| if c.is_ascii_graphic() || c == ' ' { c } else { '-' }).collect()
}

fn quoted(text: &str) -> String {
    format!("'{}'", ascii(text, MOST_LABEL))
}

/// The key of the phone that signs for this node: the oldest active phone, as its public point in hex and its fingerprint, and its name.
fn phone_key(node: &Node) -> Result<(String, String, String)> {
    let mut active: Vec<_> = node.phones.all().filter(|p| p.state == ACTIVE).collect();
    active.sort_by(|a, b| (a.added_ms, &a.fingerprint).cmp(&(b.added_ms, &b.fingerprint)));
    let phone = active.first().ok_or_else(|| Error::say("denied", NEEDS_PHONE, "device", "ask-owner"))?;
    Ok((phone.key.clone(), phone.fingerprint.clone(), ascii(&phone.name, MOST_LABEL)))
}

/// The bytes the phone's key signs to accept ``charter`` as it lists it: what the key's own acceptance is made over.
fn accept_bytes(charter: &Charter) -> Result<Vec<u8>> {
    let key = charter.admins.first().ok_or_else(|| Error::Invalid("a pool names an admin key".into()))?;
    Ok(key.acceptance_message(&charter.anchor()))
}

/// The bytes the request asks the key to sign, made again from the structure it carries: never read back from what was stored beside it.
pub fn expected(a: &Approval, key: &str) -> Result<Vec<u8>> {
    if a.action["signer"].as_str() != Some(key) {
        return Err(Error::Denied("That is not the key this request asked to sign, so nothing was signed.".into()));
    }
    let bad = || Error::Invalid("That request carries nothing to sign.".into());
    match a.action["step"].as_str() {
        Some("found") => accept_bytes(&serde_json::from_value(a.action["charter"].clone()).map_err(|_| bad())?),
        Some("act") => Ok(serde_json::from_value::<Act>(a.action["act"].clone()).map_err(|_| bad())?.sign_bytes()),
        _ => Err(bad()),
    }
}

struct Card {
    title: String,
    risk: &'static str,
    why: &'static str,
    pool: Option<String>,
    ask: String,
    scope: String,
    key: String,
    /// More lines the card carries after `KEY:`, each with its own label.
    details: Vec<String>,
    who: String,
}

fn card_text(c: &Card, ctx: &Context, expires_ms: u64) -> String {
    let mut out: Vec<String> = c.pool.iter().cloned().collect();
    out.push(format!("RISK: {}. {}", c.risk.to_uppercase(), c.why));
    out.push(ctx.said_line());
    out.push(format!("WHO: {}", c.who));
    out.push(ctx.verdict_line());
    out.push(ctx.taint_line());
    out.push(format!("ASK: {}", c.ask));
    out.push(format!("KEY: {}", c.key));
    out.extend(c.details.iter().cloned());
    out.push(format!("SCOPE: {}", c.scope));
    out.push("CANNOT: This signature is good for this one act and no other: not another pool, device or act. Your agent never holds your key and cannot sign for you.".into());
    out.push("WHY: You asked your agent for this. If you did not, deny it.".into());
    out.push(ctx.deny_line());
    out.push(format!("EXPIRES: This request ends at {}.", utc(expires_ms)));
    out.join("\n")
}

struct Asker {
    board: String,
    name: String,
}

/// The session ``token`` and what keeps it from asking again and again: the ledger of declines, the number it may ask in ten minutes and the
/// number of its requests that wait.
fn asker(node: &mut Node, token: &str, now: u64) -> Result<Asker> {
    let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("A registered session asks for a signature.".into()))?;
    let root = node.host(&holder.board)?.names.root(&holder.name);
    let who = crate::approvals::fatigue::requester(&holder.board, &holder.name, &root);
    if !matches!(node.fatigue.standing(&who, &kind_of(&[ACTION]), now), Standing::Free) {
        return Err(Error::say("denied", "You said no to this a moment ago, so I will wait before asking again.", "time", "wait"));
    }
    let rate = format!("approval {}/{}", holder.board, holder.name);
    if !node.rates.permits(&rate, MOST_REQUESTS, Duration::from_millis(REQUEST_WINDOW_MS)) {
        return Err(Error::Quota(format!("{MOST_REQUESTS} requests in ten minutes is the most one session may make; try again later.")));
    }
    if node.approvals.pending_of(&holder.name) >= MOST_PENDING_PER_SESSION || node.approvals.pending_count() >= MOST_PENDING {
        return Err(Error::Quota("Too many of your requests are waiting; wait for an answer or cancel one.".into()));
    }
    node.rates.record(&rate, Duration::from_millis(REQUEST_WINDOW_MS));
    Ok(Asker { board: holder.board, name: holder.name })
}

/// Make the request: the card shown to the phone, kept until a person decides it.
fn make(node: &mut Node, who: &Asker, card: Card, action: Value, subject: &str, now: u64) -> Result<Value> {
    let same = crate::approvals::fatigue::same(&who.board, &who.name, &[(ACTION, subject)]);
    if let Some(found) = node.approvals.all().iter().find(|a| a.live() && a.same == same).cloned() {
        return Ok(agent_view(node, &found));
    }
    let facts = node.evidence.facts(&who.board, &who.name, &[AskFacts { action: ACTION, resource: subject }]);
    let ctx = Context::from_facts(&facts, 0, Vec::new(), node.fatigue.rules.block_ms);
    let expires_ms = now + DEFAULT_TTL_MS;
    let text = card_text(&card, &ctx, expires_ms);
    if !crate::approvals::challenge::plain_text(&text) {
        return Err(Error::Invalid("That request is too long to show a person.".into()));
    }
    let (urgency, _) = request::classify(node, &format!("{}/{}", who.board, who.name), true, &ctx.prompted, now);
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: SIGN.into(), board: who.board.clone(), session: who.name.clone(), action, text, title: card.title.chars().take(80).collect(),
        risk: card.risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms, state: PENDING.into(), context: ctx, urgency: urgency.into(), same,
        source: node.cert.fingerprint(), ..Approval::empty()
    };
    node.approvals.add(approval.clone())?;
    audit(node, "approval.request", &approval, "pool_sign");
    crate::phone::announce(node, &approval);
    let done = node.approvals.get(&approval.id).cloned().unwrap_or(approval);
    Ok(agent_view(node, &done))
}

fn who_line(node: &Node, who: &Asker) -> String {
    let device = node.members.get(&node.cert.fingerprint()).map(|d| d.name.clone()).unwrap_or_default();
    format!("{} asked for this, on board {}, on this device{}.", ascii(&who.name, 48), ascii(&who.board, 48), if device.is_empty() { String::new() } else { format!(" ({})", ascii(&device, MOST_LABEL)) })
}

fn means(policy: Policy) -> &'static str {
    if policy == Policy::Open { "Devices on the same network join without asking" } else { "Each device needs your code to join" }
}

/// Ask the person's phone to found the pool ``name`` of ``policy`` with the phone's key as its admin; the request, which founds the pool
/// when the phone signs. A device already in a pool, and a node with no phone, are refused in one sentence.
pub fn ask_found(node: &mut Node, token: &str, name: &str, policy: Policy, project: &str) -> Result<Value> {
    if !node.members.is_unfounded() {
        return Err(declined("This device already runs a pool; leave it before making another."));
    }
    let (public, fingerprint, label) = phone_key(node)?;
    let now = wall_ms();
    let admin = AdminKey { key: public.clone(), alg: Alg::P256Ecdsa, role: Role::Admin, strength: Strength::Strong, person: "owner".into(), since: 1, acceptance: String::new(), label };
    let charter = Charter::new(name, policy, project, vec![admin], now);
    let mut probe = charter.clone();
    probe.admins[0].acceptance = SHAPED.into();
    probe.check_form().map_err(|e| declined(&e.to_string()))?;
    let who = asker(node, token, now)?;
    let message = accept_bytes(&charter)?;
    let (shown, kind) = (quoted(name), policy.name());
    let card = Card {
        title: format!("Found the pool {}, {kind}: you will be its first admin", ascii(name, MOST_LABEL)), risk: SENSITIVE,
        why: "It makes a pool and makes your phone its first admin, which decides who joins.",
        pool: Some(pool_line(kind, false)), key: format!("Your phone's key {}", words(&fingerprint)),
        ask: format!("Found the pool {shown}, {kind}. {}. Your phone becomes its first admin, so only you can add or remove devices.", means(policy)),
        scope: "Your phone's key signs that it accepts being the admin of this one new pool, and nothing else.".into(), who: who_line(node, &who), details: Vec::new(),
    };
    let action = json!({"kind": SIGN, "step": "found", "signer": fingerprint, "alg": Alg::P256Ecdsa, "key": public, "message": hex(&message), "charter": charter});
    make(node, &who, card, action, &format!("found:{name}"), now)
}

/// The title, the sentence and the further lines of the card for an admit or an added key, which say whose they are in words that do not
/// depend on what the asker wrote; None for any other act, which the pool's own description of it serves.
fn act_words(parsed: &Body, own: &str, pool: &str) -> Result<Option<(String, String, Vec<String>)>> {
    let place = quoted(pool);
    Ok(match parsed {
        Body::Admit(a) => {
            let (_, fingerprint) = crate::gov::body::cert_of(a)?;
            let mine = fingerprint == own;
            if a.how == "found" && !mine {
                return Err(declined("Only this device's own certificate comes in as the device that founds the pool."));
            }
            let whose = if mine { "THIS device, the computer your agent runs on" } else { "ANOTHER device, not yours" };
            let ask = format!("Admit {whose}, to the pool {place}. It says its name is {} (not checked) and comes in by {}.", quoted(&a.name), ascii(&a.how, MOST_LABEL));
            let device = format!("DEVICE: {}. Its certificate is {}.", if mine { "This computer" } else { "Not this computer: approve only if you can see this certificate on that device" }, words(&fingerprint));
            Some((format!("Admit {} to the pool {place}", if mine { "THIS device" } else { "ANOTHER device, not yours," }).chars().take(80).collect(), ask, vec![device]))
        }
        Body::AdminAdd(a) => {
            let k = &a.entry;
            let ask = format!("Add a NEW key, not one of your phones, as {} of the pool {place}. Whoever asked wrote its person {} and its label {} (not checked), and says it is {}.",
                              if k.role == Role::Recovery { "the recovery key" } else { "an admin" }, quoted(&k.person), quoted(&k.label), if k.strength == Strength::Strong { "strong" } else { "weak" });
            let device = format!("DEVICE: The new key is {} ({}); check these words against the device that holds it.", words(&k.fingerprint()), k.alg.name());
            Some((format!("Add a NEW key, not one of your phones, to the pool {place}").chars().take(80).collect(), ask, vec![device]))
        }
        _ => None,
    })
}

/// Ask the person's phone to sign the act of ``kind`` and ``body`` the pool would take next, when the phone's key is a strong admin of it;
/// the request, which hands the signed act to the pool when the phone signs.
pub fn ask_act(node: &mut Node, token: &str, kind: &str, body: Value) -> Result<Value> {
    class_of(kind)?;
    let (public, fingerprint, label) = phone_key(node)?;
    let body = node.with_cut(kind, body);
    let now = wall_ms();
    let own = node.cert.fingerprint();
    let (act, (title, ask, details), charter_name, policy) = {
        let g = node.members.gov().ok_or_else(no_pool)?;
        let act = g.new_act(kind, body, now)?;
        let parsed = crate::gov::body::parse(&act).map_err(|e| declined(&e.to_string()))?;
        let charter = g.state().chain.state_for(&act).ok_or_else(|| declined("That act follows a charter this device does not hold."))?.charter.clone();
        if !charter.admin(&public).is_some_and(|k| k.role == Role::Admin && charter.is_strong(&public)) {
            return Err(not_admin());
        }
        let words = act_words(&parsed, &own, &charter.name)?.unwrap_or_else(|| {
            let said = ascii(&crate::gov::describe::describe(&act, g), 300);
            (said.chars().take(80).collect(), said, Vec::new())
        });
        (act.clone(), words, charter.name.clone(), node.members.policy.name())
    };
    let who = asker(node, token, now)?;
    let card = Card {
        title, risk: if matches!(kind, "remove" | "admin_remove") { IRREVERSIBLE } else { SENSITIVE },
        why: if matches!(kind, "remove" | "admin_remove") { "A removed device or key does not come back." } else { "It changes who is in or runs this pool." },
        pool: Some(pool_line(policy, false)), key: format!("Your phone {} ({})", quoted(&label), words(&fingerprint)),
        ask, scope: format!("Your phone's key signs this one act for the pool {} and nothing else.", quoted(&charter_name)), who: who_line(node, &who), details,
    };
    let action = json!({"kind": SIGN, "step": "act", "signer": fingerprint, "alg": Alg::P256Ecdsa, "key": public, "message": hex(&act.sign_bytes()), "act": act});
    let subject = format!("act:{}", act.id());
    make(node, &who, card, action, &subject, now)
}

/// The phone signed: do what the request asked. A pool is founded with the key's acceptance; an act is handed to the pool with its signature.
pub fn finish(node: &mut Node, a: &Approval, d: &Decision) -> Result<()> {
    let note = |node: &mut Node, text: String| node.approvals.update(&a.id, |x| x.reason = text);
    match a.action["step"].as_str() {
        Some("found") => {
            let mut charter: Charter = serde_json::from_value(a.action["charter"].clone()).map_err(|_| Error::Invalid("That request carries no charter.".into()))?;
            charter.admins[0].acceptance = d.sig.to_string();
            charter.check_founding().map_err(|e| declined(&e.to_string()))?;
            let found = Act::new(&charter.pool_id(), 1, "found", charter.body(), charter.at_ms, "")?;
            let made = node.pool_found(&serde_json::to_value(found)?)?;
            note(node, format!("founded the pool {}", made["pool"].as_str().unwrap_or("")))
        }
        _ => {
            let mut act: Act = serde_json::from_value(a.action["act"].clone()).map_err(|_| Error::Invalid("That request carries no act.".into()))?;
            let key = a.action["key"].as_str().unwrap_or("").to_string();
            act.add_signature(ActSig { kind: SigKind::Admin, key, alg: Alg::P256Ecdsa, sig: d.sig.to_string() });
            let reply = node.pool_submit(&serde_json::to_value(&act)?)?;
            let status = reply["status"].as_str().unwrap_or("").to_string();
            if matches!(status.as_str(), "void" | "stale") {
                return Err(declined(reply["why"].as_str().unwrap_or("the pool did not take it")));
            }
            note(node, format!("the pool says {status}"))
        }
    }
}
