//! What a key signs to decide a request, and the grant entries an approved request writes.
//!
//! The signed bytes are a domain line and the canonical JSON (`row::canonical`) of the challenge: the node, the board, the
//! requesting session, the asks, the plain-words text, a nonce, the times, the decision and the key. A grant entry written by an
//! approval carries the asks and the text in its `approval` object, so any device that holds the key rebuilds the bytes from the
//! entry and checks the one signature the person made.

use serde_json::{json, Map, Value};

use crate::approvals::{Approval, GRANTS};
use crate::cert::valid_fingerprint;
use crate::error::{Error, Result};
use crate::grants::spec::{Issue, SKEW_MS};
use crate::row::canonical;

pub const DOMAIN: &str = "poolhouse/approval/v1\n";
/// The most bytes of text a request carries, and the most asks.
pub const MAX_TEXT: usize = 6000;
pub const MOST_ASKS: usize = 8;
const APPROVAL_KEYS: [&str; 8] = ["id", "node", "nonce", "created_ms", "expires_ms", "text", "grants", "index"];
const ASK_KEYS: [&str; 7] = ["action", "device", "gid", "lasts_ms", "resource", "review", "uses"];

/// The challenge of a request on the node whose certificate fingerprint is ``node``: everything but the decision and the key.
pub fn object(a: &Approval, node: &str) -> Value {
    json!({"v": 1, "op": "approval", "id": a.id, "node": node, "board": a.board, "session": a.session, "action": a.action,
           "text": a.text, "nonce": a.nonce, "created_ms": a.created_ms, "expires_ms": a.expires_ms})
}

/// The bytes the key ``key`` signs to give ``decision`` (`approve` or `deny`) on ``challenge``.
pub fn bytes(challenge: &Value, decision: &str, key: &str) -> Vec<u8> {
    let mut whole = challenge.clone();
    whole["decision"] = json!(decision);
    whole["key"] = json!(key);
    format!("{DOMAIN}{}", canonical(&whole)).into_bytes()
}

/// Whether ``text`` is what a request may carry: printable ASCII and line breaks, within `MAX_TEXT`.
pub fn plain_text(text: &str) -> bool {
    !text.is_empty() && text.len() <= MAX_TEXT && text.bytes().all(|b| b.is_ascii_graphic() || b == b' ' || b == b'\n')
}

/// The bytes the person's key signed for the grant entry ``map``, which an approval wrote: rebuilt from the entry alone.
pub fn grant_bytes(map: &Map<String, Value>) -> Vec<u8> {
    let rebuilt = || -> Option<Vec<u8>> {
        let ap = map.get("approval")?.as_object()?;
        let session = map.get("to")?.get("name")?.as_str()?;
        let challenge = json!({"v": 1, "op": "approval", "id": ap.get("id")?, "node": ap.get("node")?, "board": map.get("board")?, "session": session,
                               "action": {"kind": GRANTS, "grants": ap.get("grants")?}, "text": ap.get("text")?, "nonce": ap.get("nonce")?,
                               "created_ms": ap.get("created_ms")?, "expires_ms": ap.get("expires_ms")?});
        Some(bytes(&challenge, "approve", map.get("person")?.as_str()?))
    };
    rebuilt().unwrap_or_else(|| b"an approval entry that cannot be read".to_vec())
}

fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

fn hex_of(value: &Value, len: usize) -> bool {
    value.as_str().is_some_and(|s| s.len() == len && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
}

/// Check that the grant entry ``map`` (already read as ``issue``) is one of the asks of the approval it names, and that its times fall
/// inside the request's life. The signature is checked where every grant's is.
pub fn check_entry(map: &Map<String, Value>, issue: &Issue) -> Result<()> {
    let Some(ap) = map.get("approval").and_then(Value::as_object) else { return bad("approval is an object") };
    if ap.len() != APPROVAL_KEYS.len() || !APPROVAL_KEYS.iter().all(|k| ap.contains_key(*k)) {
        return bad("an approval names its id, node, nonce, times, text, asks and which ask this is");
    }
    let id_ok = ap["id"].as_str().and_then(|s| s.strip_prefix("ap-")).is_some_and(|h| hex_of(&Value::String(h.into()), 16));
    let (created, expires) = (ap["created_ms"].as_u64(), ap["expires_ms"].as_u64());
    let text_ok = ap["text"].as_str().is_some_and(plain_text);
    if !id_ok || !ap["node"].as_str().is_some_and(valid_fingerprint) || !hex_of(&ap["nonce"], 32) || !text_ok || created.is_none() || expires.is_none() {
        return bad("an approval's id, node, nonce, times and text are malformed");
    }
    let (created, expires) = (created.unwrap_or(0), expires.unwrap_or(0));
    let asks = ap["grants"].as_array().filter(|a| (1..=MOST_ASKS).contains(&a.len()));
    let Some(ask) = asks.zip(ap["index"].as_u64()).and_then(|(a, i)| a.get(i as usize)).and_then(Value::as_object) else {
        return bad("an approval carries one to eight asks and names one of them");
    };
    if ask.keys().any(|k| !ASK_KEYS.contains(&k.as_str())) {
        return bad("an ask is an action, a device, a gid, a time, a resource, a review flag and a count");
    }
    let uses = ask.get("uses").and_then(Value::as_u64);
    let same = ask.get("gid").and_then(Value::as_str) == Some(issue.gid.as_str()) && ask.get("action").and_then(Value::as_str) == Some(issue.action.as_str())
        && ask.get("resource").and_then(Value::as_str) == Some(issue.place.resource.as_str()) && ask.get("device").and_then(Value::as_str) == Some(issue.place.device.as_str())
        && ask.get("lasts_ms").and_then(Value::as_u64) == Some(issue.until_ms - issue.at_ms) && ask.get("review").and_then(Value::as_bool) == Some(issue.review)
        && uses == issue.uses && ask.get("uses").is_none_or(Value::is_u64);
    let to_ok = map.get("to").and_then(|t| t.get("name")).and_then(Value::as_str).is_some() && map["to"].get("subagents").is_none_or(|s| s == &json!(false));
    if !same || !to_ok {
        return bad("the grant is not the ask the approval names");
    }
    if issue.at_ms < created || issue.at_ms > expires + SKEW_MS {
        return bad("the grant starts outside the life of the request that approved it");
    }
    Ok(())
}

/// The asks of ``a`` written as the `issue` entries it approves, starting at ``at_ms`` and signed by ``signer`` with ``sig``.
pub fn grant_entries(a: &Approval, node: &str, at_ms: u64, signer: &str, sig: &str) -> Vec<Map<String, Value>> {
    let asks = a.asks();
    asks.iter().enumerate().filter_map(|(index, ask)| {
        let lasts = ask["lasts_ms"].as_u64()?;
        let mut body = Map::new();
        body.insert("op".into(), json!("issue"));
        body.insert("board".into(), json!(a.board));
        body.insert("gid".into(), ask["gid"].clone());
        body.insert("to".into(), json!({"kind": "session", "name": a.session}));
        body.insert("action".into(), ask["action"].clone());
        body.insert("scope".into(), json!({"board": a.board, "device": ask["device"], "resource": ask["resource"]}));
        body.insert("at_ms".into(), json!(at_ms));
        body.insert("until_ms".into(), json!(at_ms + lasts));
        if let Some(uses) = ask.get("uses").filter(|u| u.is_u64()) {
            body.insert("uses".into(), uses.clone());
        }
        if ask["review"] == json!(true) {
            body.insert("conditions".into(), json!({"review": true}));
        }
        body.insert("person".into(), json!(signer));
        body.insert("approval".into(), json!({"id": a.id, "node": node, "nonce": a.nonce, "created_ms": a.created_ms, "expires_ms": a.expires_ms,
                                              "text": a.text, "grants": asks, "index": index}));
        body.insert("sig".into(), json!(sig));
        Some(body)
    }).collect()
}
