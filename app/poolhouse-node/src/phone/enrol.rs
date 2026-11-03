//! Adding a phone: the one-time code, and what a phone sends when it scans one.
//!
//! The code is a random secret whose hash the node keeps in its memory (never a file): the first phone's is made when the person says
//! yes to the operating system's own prompt (`presence`), a further phone's when a person's key or a session asks once a key exists,
//! saying who asked. The first phone is enrolled on the strength of the code, the proof that its key made the request, and a time
//! within five minutes; so is a phone whose key the charter of the pool this device is in lists as a strong admin key. Every other
//! later phone waits for a key the node holds to approve it, so a code that someone else saw adds nothing by itself, and a code a
//! session made says so on the request.

use std::net::IpAddr;
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::approvals::content::phone_text;
use crate::approvals::context::{pick_options, Context};
use crate::approvals::decide::audit;
use crate::approvals::{Approval, PENDING as APPROVAL_PENDING, PHONE_ADD, PHONE_REMOVE};
use crate::error::{Error, Result};
use crate::fsutil::{random_hex, sha256_hex};
use crate::gov::act::Act;
use crate::grants::authority::{Authority, LISTED};
use crate::grants::person::{fingerprint_of, verifies_p256, P256};
use crate::grants::spec::SKEW_MS;
use crate::node::Node;
use crate::phone::{admit, words, Phone, Push, ACTIVE, PENDING};
use crate::poolops::POOL_BOARD;
use crate::presence::Purpose;
use crate::row::canonical;

/// How long a code is good for.
pub const OFFER_TTL_MS: u64 = 10 * 60 * 1000;
/// The wrong codes one address may try in ten minutes. A code is 256 random bits, so the count only keeps a noisy address quiet: it
/// never ends the offer, because that would let anyone on the network cancel the code a person is about to scan.
const WRONG_CODES: usize = 10;
const WRONG_WINDOW: Duration = Duration::from_secs(600);
const ADD_DOMAIN: &str = "poolhouse/phone-add/v1\n";
/// How long a phone that waits for approval is kept before it is dropped.
const WAIT_MS: u64 = 15 * 60 * 1000;

fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

/// Standard base64 with the URL alphabet and no padding.
pub fn b64url(bytes: &[u8]) -> String {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |n, (i, b)| n | u32::from(*b) << (16 - 8 * i));
        for i in 0..=chunk.len() {
            out.push(A[(n >> (18 - 6 * i) & 63) as usize] as char);
        }
    }
    out
}

/// A one-time code for a further phone, kept in the node's memory: a program that can write the state directory cannot leave one.
#[derive(Clone)]
pub struct Offer {
    sha256: String,
    at_ms: u64,
    /// Who asked for the code through the node in words, empty when a person did with a key.
    made_by: String,
    /// The session that asked (`board/name`), empty when a person or a phone did.
    by: String,
    /// In a pool, the person whose admin phone the code is for; empty when no one person is.
    person: String,
}

impl Offer {
    /// The person the code is for, empty on a device in no pool.
    pub fn person(&self) -> &str {
        &self.person
    }
}

/// Make a one-time code for the next phone that a person's key asked for (no one is named on the request), and return its secret (the
/// caller puts it in the code a person scans).
pub fn make_offer(node: &mut Node, now_ms: u64) -> Result<String> {
    make_offer_by(node, now_ms, "")
}

/// Make a one-time code that ``made_by`` asked for (a session or a phone; empty for a person): the request to approve the phone it adds says so.
pub fn make_offer_by(node: &mut Node, now_ms: u64, made_by: &str) -> Result<String> {
    make_offer_for(node, now_ms, made_by, "")
}

/// The same for a code a session (`by`, as `board/name`) asked for: a phone that is not approved counts against that session.
pub fn make_offer_for(node: &mut Node, now_ms: u64, made_by: &str, by: &str) -> Result<String> {
    let person = if node.members.gov().is_some() { admit::person_for(node, None) } else { String::new() };
    offer_of(node, now_ms, (made_by, by), person)
}

/// The same for a code the phone ``maker`` (a fingerprint) asked for: it is for the person that phone belongs to.
pub fn make_offer_from_phone(node: &mut Node, now_ms: u64, made_by: &str, maker: &str) -> Result<String> {
    let person = if node.members.gov().is_some() { admit::person_for(node, Some(maker)) } else { String::new() };
    offer_of(node, now_ms, (made_by, ""), person)
}

fn offer_of(node: &mut Node, now_ms: u64, made: (&str, &str), person: String) -> Result<String> {
    let mut raw = [0u8; 32];
    getrandom::fill(&mut raw).map_err(|e| std::io::Error::other(e.to_string()))?;
    let secret = b64url(&raw);
    node.offer = Some(Offer { sha256: sha256_hex(secret.as_bytes()), at_ms: now_ms, made_by: made.0.into(), by: made.1.into(), person });
    Ok(secret)
}

/// Whether a code is waiting to be scanned: one a person is showing is not replaced by one a session asked for.
pub fn offer_is_live(node: &Node, now_ms: u64) -> bool {
    node.offer.as_ref().is_some_and(|o| now_ms >= o.at_ms && now_ms - o.at_ms <= OFFER_TTL_MS)
}

/// A code is open and nobody has scanned it: only the session that made it (``asker``, as `board/name`) gets a new one in its place, so
/// a code a person is about to scan is never swapped under them, and whoever asks is told what to do now. A phone or a person passes no asker.
pub fn offer_open_for_another(node: &Node, asker: &str, now_ms: u64) -> Result<()> {
    match node.offer.as_ref().filter(|_| offer_is_live(node, now_ms)) {
        Some(open) if asker.is_empty() || open.by != asker => Err(Error::Denied("A code for a phone is open and has not been scanned yet. Tell the person to scan that one with the phone now; a new code is made once it is used.".into())),
        _ => Ok(()),
    }
}

/// The refusal for a session a person blocked, with when the block ends: a block is the person's own answer, so it is the one thing that
/// keeps a session from asking, and it says so.
pub fn refuse_blocked(node: &mut Node, asker: &str, now_ms: u64) -> Result<()> {
    match node.answered.blocked_until(asker, now_ms) {
        Some(until) => Err(Error::Denied(format!("A person blocked this session until {} when they answered an earlier request of it, so nothing it asks about phones is shown to them before then.", crate::approvals::content::utc(until)))),
        None => Ok(()),
    }
}

/// Spend the code ``secret`` that ``ip`` sent: it is good once, for ten minutes. A wrong code is counted against the address that sent
/// it and never ends the offer. Whoever made the code is returned, empty for a person. The first phone is added with the secret the
/// operating system's prompt made (`presence`), and with no other.
fn spend_offer(node: &mut Node, ip: IpAddr, secret: &str, now_ms: u64) -> Result<(String, String, String)> {
    let first = node.persons.is_empty();
    let tries = format!("phone code {ip}");
    if !node.rates.permits(&tries, WRONG_CODES, WRONG_WINDOW) {
        return Err(crate::limits::floor("Too many wrong codes came from this address; wait ten minutes."));
    }
    if first {
        if !node.firsts.offered(Purpose::Phone, now_ms) {
            return Err(Error::Denied("No phone is being added right now: run `poolhouse-workspace phone add` and say yes at the prompt of this computer.".into()));
        }
        if !node.firsts.matches(Purpose::Phone, secret, now_ms) {
            node.rates.record(&tries, WRONG_WINDOW);
            return Err(Error::Denied("That code was not made for this device.".into()));
        }
        node.firsts.spend();
        return Ok((String::new(), String::new(), String::new()));
    }
    let offer = node.offer.clone().ok_or_else(|| Error::Denied("No phone is being added right now: ask for a new code.".into()))?;
    if now_ms < offer.at_ms || now_ms - offer.at_ms > OFFER_TTL_MS {
        node.offer = None;
        return Err(Error::Denied("That code has expired; ask for a new one.".into()));
    }
    if secret.is_empty() || !same(&offer.sha256, &sha256_hex(secret.as_bytes())) {
        node.rates.record(&tries, WRONG_WINDOW);
        return Err(Error::Denied("That code was not made for this device.".into()));
    }
    node.offer = None;
    Ok((offer.made_by, offer.by, offer.person))
}

/// At most one phone waits for approval, so the person is shown one at a time; a second is told to have the first answered. Nothing
/// keeps a phone away for what became of an earlier one: a phone that was not approved is asked about again like any other.
fn one_waiting(node: &mut Node, by: &str, now: u64) -> Result<()> {
    if let Some(until) = node.answered.blocked_until(by, now) {
        return Err(Error::Denied(format!("The session that made this code was blocked by a person until {}, so it adds no phone.", crate::approvals::content::utc(until))));
    }
    if node.approvals.all().iter().any(|a| a.kind == PHONE_ADD && a.pending()) {
        return Err(crate::limits::floor("Another phone is already waiting for one of your phones to approve it: answer that on your phone first, then scan again."));
    }
    Ok(())
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    p.get(key).and_then(Value::as_str).ok_or_else(|| Error::Invalid(format!("{key} is text.")))
}

fn plain_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 40 && name.bytes().all(|b| b.is_ascii_graphic() || b == b' ') && name.trim() == name
}

/// The bytes a phone's key signs to say it is the one being added to this node.
pub fn add_bytes(node_fp: &str, p: &Map<String, Value>) -> Vec<u8> {
    let field = |k: &str| p.get(k).cloned().unwrap_or(Value::Null);
    let body = json!({"op": "phone_add", "node": node_fp, "name": field("name"), "key": field("key"), "push": field("p256dh"), "auth": field("auth"), "at_ms": field("at_ms")});
    format!("{ADD_DOMAIN}{}", canonical(&body)).into_bytes()
}

/// Add the phone that ``ip`` sent ``p`` for: the first one at once, a phone whose key the pool's charter lists as a strong admin key at
/// once, every other one after a key the node holds approves it.
pub fn enrol(node: &mut Node, ip: IpAddr, p: &Map<String, Value>) -> Result<Value> {
    let now = node.now_ms();
    let (name, key, p256dh, auth) = (text(p, "name")?, text(p, "key")?.to_ascii_lowercase(), text(p, "p256dh")?.to_ascii_lowercase(), text(p, "auth")?.to_ascii_lowercase());
    let at = p.get("at_ms").and_then(Value::as_u64).filter(|a| a.abs_diff(now) <= SKEW_MS)
        .ok_or_else(|| Error::Invalid("The phone's clock is more than five minutes from this device's; set the phone's time to automatic.".into()))?;
    if !plain_name(name) || auth.len() != 32 || crate::grants::person::p256_point(&p256dh).is_none() {
        return Err(Error::Invalid("A phone sends a plain name, its key, its push key and its push secret.".into()));
    }
    let fingerprint = fingerprint_of(P256, &key)?;
    let (known, listed_key) = (Authority::of(node).any(), Authority::of(node).lists(&fingerprint));
    if node.members.gov().is_some() && !node.persons.is_empty() && !listed_key && !known {
        return Err(Error::Denied(admit::NO_ADMIN_PHONE.into()));
    }
    let (made_by, by, person) = spend_offer(node, ip, text(p, "secret")?, now)?;
    let mut signed = p.clone();
    signed.insert("key".into(), json!(key));
    signed.insert("p256dh".into(), json!(p256dh));
    if !verifies_p256(&key, &add_bytes(&node.cert.fingerprint(), &signed), text(p, "proof")?) {
        return Err(Error::Denied("That phone did not prove it holds its key, so it was not added.".into()));
    }
    if node.persons.get(&fingerprint).is_some() || node.phones.by_key(&fingerprint).is_some_and(|f| f.state != crate::phone::REMOVED) {
        return Err(Error::Denied("That phone is already added.".into()));
    }
    if node.persons.was_removed(&fingerprint) {
        return Err(Error::Denied("That phone's key was removed from this device and is not added again: reinstall the app to make a new key.".into()));
    }
    if node.persons.all().len() >= crate::grants::person::MOST_KEYS {
        return Err(crate::limits::refuse("This device holds as many person keys as it may; remove one first.", crate::grants::person::MOST_KEYS, "MOST_KEYS", "grants/person.rs"));
    }
    let (id, token) = (format!("ph-{}", random_hex(6)?), random_hex(32)?);
    let first = node.persons.is_empty();
    let listed = !first && Authority::of(node).would_hold(&fingerprint, &key);
    let direct = first || listed;
    let admit = if direct || node.members.gov().is_none() || listed_key { None } else { Some(admit::draft(node, &person, name, &key, p.get("acceptance").and_then(Value::as_str).unwrap_or(""))?) };
    if !direct {
        one_waiting(node, &by, now)?;
    }
    let phone = Phone {
        id: id.clone(), name: name.into(), fingerprint: fingerprint.clone(), key: key.clone(), token_sha256: sha256_hex(token.as_bytes()),
        state: if direct { ACTIVE } else { PENDING }.into(), push: Push { p256dh, auth, endpoint: String::new() }, added_ms: at, seen_ms: now,
    };
    node.phones.add(phone)?;
    let (mut approval, mut number) = (String::new(), String::new());
    if direct {
        node.persons.add_phone_key(P256, &key, name, now)?;
        let how = if first { "the first key, added by a code a person asked for at this device" } else { "a strong admin key of this pool, added by a code" };
        let _ = node.record_event("phone.add", &fingerprint, &format!("{name}; {how}"));
    } else {
        (approval, number) = ask_to_add(node, &id, (name, &key, &fingerprint), (&made_by, &by, admit.as_ref()), now)?;
    }
    let me = node.cert.fingerprint();
    let device = node.members.get(&me).map(|d| d.name.clone()).unwrap_or_default();
    Ok(json!({"phone": id, "token": token, "fingerprint": fingerprint, "words": words(&fingerprint), "state": if direct { ACTIVE } else { PENDING },
              "approval": approval, "number": number, "device": me, "name": device, "policy": node.members.policy.name()}))
}

/// The request every phone already added sees for the new one, and the number the new phone is to show on its own screen: the person
/// chooses it among three on the phone that approves, so the approval is tied to the phone in their hand.
fn ask_to_add(node: &mut Node, id: &str, phone: (&str, &str, &str), made: (&str, &str, Option<&Act>), now: u64) -> Result<(String, String)> {
    let (name, key, fingerprint) = phone;
    let (made_by, by, listing) = made;
    let (code, options) = pick_options()?;
    let ctx = Context { pick: options, ..Context::default() };
    let expires = now + WAIT_MS;
    let mut flags = vec!["A phone no key of yours has seen is asking to be added. It must show the same key as below before you approve.".to_string()];
    if !made_by.is_empty() {
        flags.push(format!("The code this phone used was made by {made_by}, not by you at the computer. Approve only if you scanned it yourself just now."));
    }
    let (title, mut text, risk) = phone_text(true, name, &words(fingerprint), &format!("A new phone named {name}"), node.members.policy.name(), &flags, &ctx, expires);
    let mut action = json!({"kind": PHONE_ADD, "phone": id, "name": name, "key": key, "fingerprint": fingerprint});
    if let Some(act) = listing {
        text = admit::card(node, &text, act);
        action["act"] = serde_json::to_value(act)?;
    }
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: PHONE_ADD.into(), board: POOL_BOARD.into(), session: String::new(),
        action,
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms: expires, state: APPROVAL_PENDING.into(),
        source: node.cert.fingerprint(), context: ctx, pick_code: code.clone(), by: by.into(), ..Approval::empty()
    };
    node.approvals.add(approval.clone())?;
    audit(node, "approval.request", &approval, &format!("add the phone {name}"));
    crate::phone::announce(node, &approval);
    Ok((approval.id, code))
}

/// The request to take a phone off the node, which a key the node holds then signs. ``by`` says who asks in words; ``by_key`` is the session
/// (`board/name`) when one does, which a held or blocked session may not, and which the text names.
pub fn ask_to_remove(node: &mut Node, by: &str, by_key: &str, target: &str) -> Result<String> {
    let phone = node.phones.get(target).filter(|p| p.state == ACTIVE).cloned().ok_or_else(|| Error::Invalid("That is not one of this device's phones.".into()))?;
    if Authority::of(node).lists(&phone.fingerprint) {
        return Err(Error::Denied(LISTED.into()));
    }
    let now = node.now_ms();
    if !by_key.is_empty() {
        refuse_blocked(node, by_key, now)?;
    }
    // The same removal again, from the same asker, is the one that waits; another removal from an asker whose first still waits is told
    // what waits; a removal that another asker already put to the person is not put twice.
    let open = node.approvals.all().iter().find(|a| a.pending() && a.kind == PHONE_REMOVE && (a.action.get("phone").and_then(Value::as_str) == Some(target) || (!by_key.is_empty() && a.by == by_key))).cloned();
    if let Some(open) = open {
        let same_target = open.action.get("phone").and_then(Value::as_str) == Some(target);
        return match (same_target, open.by == by_key) {
            (true, true) => Ok(open.id),
            (true, false) => Err(Error::Invalid("Removing that phone is already waiting for a fingerprint on the person's phone.".into())),
            (false, _) => Err(Error::Denied(crate::approvals::request::waiting_words(&open))),
        };
    }
    let mut flags = Vec::new();
    if let Some((board, name)) = by_key.split_once('/') {
        flags.push(format!("The session {name} on board {board} asked for this, not a phone of yours."));
    }
    let expires = now + crate::approvals::DEFAULT_TTL_MS;
    let (title, text, risk) = phone_text(false, &phone.name, &words(&phone.fingerprint), by, node.members.policy.name(), &flags, &Context::default(), expires);
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: PHONE_REMOVE.into(), board: POOL_BOARD.into(), session: String::new(),
        action: json!({"kind": PHONE_REMOVE, "phone": target, "name": phone.name, "fingerprint": phone.fingerprint}),
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms: expires, state: APPROVAL_PENDING.into(),
        source: node.cert.fingerprint(), by: by_key.into(), ..Approval::empty()
    };
    node.approvals.add(approval.clone())?;
    audit(node, "approval.request", &approval, &format!("remove the phone {}", phone.name));
    crate::phone::announce(node, &approval);
    Ok(approval.id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_without_padding_in_the_url_alphabet() {
        assert_eq!(b64url(b""), "");
        assert_eq!(b64url(b"f"), "Zg");
        assert_eq!(b64url(b"fo"), "Zm8");
        assert_eq!(b64url(b"foo"), "Zm9v");
        assert_eq!(b64url(&[0xfb, 0xff, 0xfe]), "-__-");
    }
}
