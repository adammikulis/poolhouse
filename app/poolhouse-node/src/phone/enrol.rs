//! Adding a phone: the one-time code, and what a phone sends when it scans one.
//!
//! The code is a random secret whose hash a person's command leaves beside `persons.json` (`phone_offer.json`), as `grants-setup` does
//! for the first key, or that the node leaves when asked once a key exists. The first phone is enrolled on the strength of the code,
//! the proof that its key made the request, and a time within five minutes. Every later phone waits for a key the node holds to
//! approve it, so a code that someone else saw adds nothing by itself.

use std::path::Path;
use std::time::Duration;

use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::approvals::content::phone_text;
use crate::approvals::decide::audit;
use crate::approvals::{Approval, PENDING as APPROVAL_PENDING, PHONE_ADD};
use crate::error::{Error, Result};
use crate::fsutil::{random_hex, sha256_hex, write_atomic};
use crate::grants::person::{fingerprint_of, verifies_p256, P256};
use crate::grants::spec::SKEW_MS;
use crate::node::Node;
use crate::phone::{words, Phone, Push, ACTIVE, PENDING};
use crate::poolops::POOL_BOARD;
use crate::row::canonical;

pub const OFFER_FILE: &str = "phone_offer.json";
/// How long a code is good for.
pub const OFFER_TTL_MS: u64 = 10 * 60 * 1000;
const WRONG_CODES: usize = 30;
const WRONG_WINDOW: Duration = Duration::from_secs(600);
const ADD_DOMAIN: &str = "poolhouse/phone-add/v1\n";
/// How long a phone that waits for approval is kept before it is dropped.
const WAIT_MS: u64 = 15 * 60 * 1000;

#[derive(Deserialize)]
struct Offer {
    sha256: String,
    at_ms: u64,
}

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

/// Leave a one-time code for the next phone and return its secret (the caller puts it in the code a person scans).
pub fn make_offer(dir: &Path, now_ms: u64) -> Result<String> {
    let mut raw = [0u8; 32];
    getrandom::fill(&mut raw).map_err(|e| std::io::Error::other(e.to_string()))?;
    let secret = b64url(&raw);
    write_atomic(&dir.join(OFFER_FILE), &serde_json::to_vec(&json!({"sha256": sha256_hex(secret.as_bytes()), "at_ms": now_ms}))?)?;
    Ok(secret)
}

/// Spend the code ``secret``: it is good once, for ten minutes. A wrong code is counted, and enough of them end the offer.
fn spend_offer(node: &mut Node, secret: &str, now_ms: u64) -> Result<()> {
    let file = node.dir.join(OFFER_FILE);
    let offer = std::fs::read(&file).ok().and_then(|b| serde_json::from_slice::<Offer>(&b).ok())
        .ok_or_else(|| Error::Denied("No phone is being added right now: ask for a new code.".into()))?;
    if now_ms < offer.at_ms || now_ms - offer.at_ms > OFFER_TTL_MS {
        let _ = std::fs::remove_file(&file);
        return Err(Error::Denied("That code has expired; ask for a new one.".into()));
    }
    if !node.rates.permits("phone code", WRONG_CODES, WRONG_WINDOW) {
        let _ = std::fs::remove_file(&file);
        return Err(Error::Denied("Too many wrong codes were tried, so that code was cancelled; ask for a new one.".into()));
    }
    if secret.is_empty() || !same(&offer.sha256, &sha256_hex(secret.as_bytes())) {
        node.rates.record("phone code", WRONG_WINDOW);
        return Err(Error::Denied("That code was not made for this device.".into()));
    }
    let _ = std::fs::remove_file(&file);
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

/// Add the phone that sent ``p``: the first one at once, every later one after a key the node holds approves it.
pub fn enrol(node: &mut Node, p: &Map<String, Value>) -> Result<Value> {
    let now = node.now_ms();
    let (name, key, p256dh, auth) = (text(p, "name")?, text(p, "key")?.to_ascii_lowercase(), text(p, "p256dh")?.to_ascii_lowercase(), text(p, "auth")?.to_ascii_lowercase());
    let at = p.get("at_ms").and_then(Value::as_u64).filter(|a| a.abs_diff(now) <= SKEW_MS)
        .ok_or_else(|| Error::Invalid("The phone's clock is more than five minutes from this device's; set the phone's time to automatic.".into()))?;
    if !plain_name(name) || auth.len() != 32 || crate::grants::person::p256_point(&p256dh).is_none() {
        return Err(Error::Invalid("A phone sends a plain name, its key, its push key and its push secret.".into()));
    }
    let fingerprint = fingerprint_of(P256, &key)?;
    spend_offer(node, text(p, "secret")?, now)?;
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
    if node.persons.all().len() >= 8 {
        return Err(Error::Quota("This device holds as many person keys as it will; remove one first.".into()));
    }
    let (id, token) = (format!("ph-{}", random_hex(6)?), random_hex(32)?);
    let first = node.persons.is_empty();
    let phone = Phone {
        id: id.clone(), name: name.into(), fingerprint: fingerprint.clone(), key: key.clone(), token_sha256: sha256_hex(token.as_bytes()),
        state: if first { ACTIVE } else { PENDING }.into(), push: Push { p256dh, auth, endpoint: String::new() }, added_ms: at, seen_ms: now,
    };
    node.phones.add(phone)?;
    let mut approval = String::new();
    if first {
        node.persons.add_key(P256, &key, name, now)?;
        let _ = node.record_event("phone.add", &fingerprint, &format!("{name}; the first key, added by a code a person asked for at this device"));
    } else {
        approval = ask_to_add(node, &id, name, &key, &fingerprint, now)?;
    }
    let me = node.cert.fingerprint();
    let device = node.members.get(&me).map(|d| d.name.clone()).unwrap_or_default();
    Ok(json!({"phone": id, "token": token, "fingerprint": fingerprint, "words": words(&fingerprint), "state": if first { ACTIVE } else { PENDING },
              "approval": approval, "device": me, "name": device, "policy": node.members.policy.name()}))
}

/// The request every phone already added sees for the new one.
fn ask_to_add(node: &mut Node, id: &str, name: &str, key: &str, fingerprint: &str, now: u64) -> Result<String> {
    let expires = now + WAIT_MS;
    let flags = vec!["A phone no key of yours has seen is asking to be added. It must show the same key as below before you approve.".to_string()];
    let (title, text, risk) = phone_text(true, name, &words(fingerprint), &format!("A new phone named {name}"), node.members.policy.name(), &flags, expires);
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: PHONE_ADD.into(), board: POOL_BOARD.into(), session: String::new(),
        action: json!({"kind": PHONE_ADD, "phone": id, "name": name, "key": key, "fingerprint": fingerprint}),
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms: expires, state: APPROVAL_PENDING.into(), decided_ms: 0,
        decided_by: String::new(), reason: String::new(), grants: Vec::new(), seq: 0,
    };
    node.approvals.add(approval.clone())?;
    audit(node, "approval.request", &approval, &format!("add the phone {name}"));
    crate::phone::announce(node, &approval);
    Ok(approval.id)
}

/// The request a phone makes to take a phone off the node, which a key the node holds then signs.
pub fn ask_to_remove(node: &mut Node, by: &str, target: &str) -> Result<String> {
    let phone = node.phones.get(target).filter(|p| p.state == ACTIVE).cloned().ok_or_else(|| Error::Invalid("That is not one of this device's phones.".into()))?;
    if node.approvals.all().iter().any(|a| a.pending() && a.action.get("phone").and_then(Value::as_str) == Some(target) && a.kind == crate::approvals::PHONE_REMOVE) {
        return Err(Error::Invalid("Removing that phone is already waiting for a fingerprint.".into()));
    }
    let now = node.now_ms();
    let expires = now + crate::approvals::DEFAULT_TTL_MS;
    let (title, text, risk) = phone_text(false, &phone.name, &words(&phone.fingerprint), &format!("The phone {by}"), node.members.policy.name(), &[], expires);
    let approval = Approval {
        id: format!("ap-{}", random_hex(8)?), kind: crate::approvals::PHONE_REMOVE.into(), board: POOL_BOARD.into(), session: String::new(),
        action: json!({"kind": crate::approvals::PHONE_REMOVE, "phone": target, "name": phone.name, "fingerprint": phone.fingerprint}),
        text, title, risk: risk.into(), nonce: random_hex(16)?, created_ms: now, expires_ms: expires, state: APPROVAL_PENDING.into(), decided_ms: 0,
        decided_by: String::new(), reason: String::new(), grants: Vec::new(), seq: 0,
    };
    node.approvals.add(approval.clone())?;
    audit(node, "approval.request", &approval, &format!("remove the phone {}", phone.name));
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
