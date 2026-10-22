//! The grant methods of the local API. Who may issue or revoke is decided by a signature a person's key made, never by
//! the session that carries the request.

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::lineage::{Anchor, Peer};
use crate::node::Node;
use crate::poolops::POOL_BOARD;
use crate::row::{canonical, is_line, Kind, Row};
use crate::rules::held_back;

use super::authority::{Authority, LISTED};
use super::book::{standing, Grant};
use super::person::verifies;
use super::spec::{self, signing_bytes, ACTIONS, DEVICE_LOCAL, FLOORS, PORTABLE, SKEW_MS};

pub const METHODS: [&str; 8] = ["grant_issue", "grant_revoke", "grant_distrust", "grants", "grant_log", "person_enroll", "person_unenroll", "person_keys"];
const MOST_LISTED: usize = 200;
const MOST_LOGGED_PER_ORIGIN: usize = 500;
const ENROLL_DOMAIN: &str = "poolhouse/person-enroll/v1\n";
const APPROVE_DOMAIN: &str = "poolhouse/person-approve/v1\n";
const UNENROLL_DOMAIN: &str = "poolhouse/person-unenroll/v1\n";

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "grant_issue" => &["gid", "to", "action", "scope", "at_ms", "until_ms", "uses", "conditions", "person", "sig"],
        "grant_revoke" => &["gid", "at_ms", "reason", "person", "sig"],
        "grant_distrust" => &["key", "since", "pool", "person", "at_ms", "reason", "sig"],
        "grants" => &[],
        "grant_log" => &["limit"],
        "person_enroll" => &["public", "about", "at_ms", "proof", "by", "approval", "approved_ms", "nonce", "anchor", "secret"],
        "person_unenroll" => &["fingerprint", "at_ms", "by", "nonce", "anchor", "sig"],
        "person_keys" => &[],
        _ => return None,
    })
}

/// The methods that name no board.
pub fn boardless(method: &str) -> bool {
    matches!(method, "person_enroll" | "person_unenroll" | "person_keys" | "grant_distrust")
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        Some(Value::String(s)) => Ok(s),
        None => Ok(""),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

/// The time a person signed, from the param ``key``, when it is within five minutes of this node's clock.
pub(crate) fn fresh(node: &Node, p: &Map<String, Value>, key: &str) -> Result<u64> {
    let at = p.get(key).and_then(Value::as_u64).ok_or_else(|| Error::Invalid(format!("{key} is the time the person signed, in milliseconds")))?;
    if at.abs_diff(node.now_ms()) > SKEW_MS {
        return Err(Error::Invalid("the signed time is more than five minutes from this node's clock".into()));
    }
    Ok(at)
}

/// The body a person signed: the operation, the board and the named params, without the signature.
fn signed_body(op: &str, board: &str, p: &Map<String, Value>, keys: &[&str]) -> Map<String, Value> {
    let mut body = Map::new();
    body.insert("op".into(), json!(op));
    body.insert("board".into(), json!(board));
    for k in keys {
        if let Some(v) = p.get(*k).filter(|v| !v.is_null()) {
            body.insert((*k).into(), v.clone());
        }
    }
    body
}

fn carrier(node: &mut Node, token: &str, board: &str) -> Result<String> {
    let access = node.access(token, board, true)?;
    if access.holder.board != board {
        return Err(Error::Denied("only a session of this board carries its grants".into()));
    }
    Ok(access.actor)
}

fn write(node: &mut Node, board: &str, actor: &str, mut body: Map<String, Value>, sig: &str) -> Result<crate::row::Row> {
    body.insert("sig".into(), json!(sig));
    let body = Value::Object(body);
    crate::fold::check_local(Kind::Grant, actor, &body, "")?;
    node.host(board)?.board.append_person(Kind::Grant, actor, body)
}

fn issue(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = carrier(node, token, board)?;
    let body = signed_body("issue", board, p, &["gid", "to", "action", "scope", "at_ms", "until_ms", "uses", "conditions", "person"]);
    let sig = text(p, "sig")?;
    let mut whole = body.clone();
    whole.insert("sig".into(), json!(sig));
    let parsed = spec::parse_issue(&whole)?;
    fresh(node, p, "at_ms")?;
    if Authority::of(node).pool_wide(&parsed.action) {
        return Err(Error::Denied("In a pool your phone gives standing grants: ask for one and tap approve.".into()));
    }
    if !node.persons.signed(&parsed.person, &signing_bytes(&body), sig) {
        return Err(Error::Denied("a grant counts only when a person key enrolled on this node signed it".into()));
    }
    if standing(node, board)?.grants.iter().any(|g| g.status != "ignored" && g.issue.gid == parsed.gid) {
        return Err(Error::Invalid("that grant id is already on the board".into()));
    }
    let row = write(node, board, &actor, body, sig)?;
    Ok(json!({"gid": parsed.gid, "action": parsed.action, "until_ms": parsed.until_ms, "entry": row.id()}))
}

fn revoke(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = carrier(node, token, board)?;
    let body = signed_body("revoke", board, p, &["gid", "at_ms", "reason", "person"]);
    let sig = text(p, "sig")?;
    let mut whole = body.clone();
    whole.insert("sig".into(), json!(sig));
    let named = spec::parse_revoke(&whole)?;
    fresh(node, p, "at_ms")?;
    let bytes = signing_bytes(&body);
    let authority = Authority::of(node);
    let narrowing = authority.narrows(&named.person, &bytes, sig);
    if !narrowing && !node.persons.signed(&named.person, &bytes, sig) {
        return Err(Error::Denied("a revocation counts only when a person key enrolled on this node signed it".into()));
    }
    let found = standing(node, board)?.grants.iter().find(|g| g.issue.gid == named.gid).map(|g| (g.status == "revoked", g.issue.action.clone()));
    match found {
        None => return Err(Error::Invalid("no grant of that id is on the board".into())),
        Some((true, _)) => return Ok(json!({"gid": named.gid, "revoked": true, "entry": ""})),
        Some((false, action)) if authority.pool_wide(&action) && !narrowing => return Err(Error::Denied("Only an admin key of this pool ends a grant of the pool.".into())),
        Some(_) => {}
    }
    let row = write(node, board, &actor, body, sig)?;
    Ok(json!({"gid": named.gid, "revoked": true, "entry": row.id()}))
}

/// Cut a key the pool lists off from granting and deciding: an admin-role key signs the row, any registered session carries it, and the node
/// writes it to the pool board. The same row again changes nothing.
fn distrust(node: &mut Node, token: &str, p: &Map<String, Value>) -> Result<Value> {
    node.tokens.resolve(token).ok_or_else(|| Error::Denied("a registered session carries a distrust".into()))?;
    let body = signed_body("distrust", POOL_BOARD, p, &["key", "since", "pool", "person", "at_ms", "reason"]);
    let sig = text(p, "sig")?;
    let mut whole = body.clone();
    whole.insert("sig".into(), json!(sig));
    spec::parse_distrust(&whole)?;
    fresh(node, p, "at_ms")?;
    let gov = node.members.gov().ok_or_else(|| Error::Denied("A device in no pool has no key to distrust.".into()))?;
    let key = super::distrust::subject(gov, &whole).map_err(|why| Error::Denied(format!("That distrust counts for nothing: {why}.")))?;
    if super::distrust::set(node).contains(&key) {
        return Ok(json!({"key": key, "distrusted": true, "entry": ""}));
    }
    let row = write(node, POOL_BOARD, "node", body, sig)?;
    let floor = node.members.grants_floor_ms;
    let pool = node.host(POOL_BOARD)?;
    pool.book.refresh(&pool.board, floor);
    Ok(json!({"key": key, "distrusted": true, "entry": row.id()}))
}

fn shown(g: &Grant, mine: bool) -> Value {
    let body = &g.row.body;
    json!({"gid": g.issue.gid, "action": g.issue.action, "to": body["to"], "scope": body["scope"], "at_ms": g.issue.at_ms, "until_ms": g.issue.until_ms,
           "uses": g.issue.uses, "used": g.used, "review": g.issue.review, "status": g.status, "why": g.why, "person": g.issue.person,
           "entry": g.row.id(), "carried_by": g.row.actor, "mine": mine})
}

fn listing(node: &mut Node, board: &str, token: &str) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let st = standing(node, board)?;
    let registry = &node.host(board)?.names;
    let rows: Vec<Value> = st.grants.iter().take(MOST_LISTED).map(|g| shown(g, super::names(registry, &access.actor, &g.issue.to))).collect();
    let ignored: Vec<Value> = st.ignored.iter().take(50).map(|(id, why)| json!({"entry": id, "why": why})).collect();
    let keys: Vec<&String> = node.persons.all().keys().collect();
    let authority = Authority::of(node);
    let may_issue: Vec<&String> = keys.iter().copied().filter(|_| !authority.governed()).collect();
    let may_revoke: Vec<&String> = keys.iter().copied().filter(|f| authority.may_revoke(f)).collect();
    Ok(json!({"grants": rows, "ignored": ignored, "persons": keys, "now_ms": node.now_ms(), "governance": if authority.governed() { "governed" } else { "unfounded" },
              "authority": authority.authority(), "distrusted": super::distrust::set(node), "may_issue": may_issue, "may_revoke": may_revoke,
              "registry": {"actions": ACTIONS, "floors": FLOORS, "portable": PORTABLE, "device_local": DEVICE_LOCAL}, "device": node.cert.fingerprint()}))
}

fn log(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    node.access(token, board, false)?;
    let limit = p.get("limit").and_then(Value::as_u64).map_or(50, |n| (n as usize).clamp(1, MOST_LISTED));
    let now = node.now_ms();
    let hosted = node.host(board)?;
    let own = hosted.board.origin().to_string();
    let mut rows: Vec<&Row> = hosted.board.origins().iter().flat_map(|o| {
        let newest = hosted.board.rows(o).iter().rev().filter(|r| r.kind == Kind::Grant && (r.origin == own || !held_back(r, now)));
        newest.take(MOST_LOGGED_PER_ORIGIN).collect::<Vec<&Row>>()
    }).collect();
    rows.sort_by(|a, b| a.order().cmp(&b.order()));
    let out: Vec<Value> = rows.iter().rev().take(limit).map(|r| {
        let b = &r.body;
        json!({"entry": r.id(), "at_ms": r.hlc.0, "foreign": r.origin != own, "by": r.actor, "op": b["op"], "gid": b["gid"], "action": b["action"],
               "target": b["target"], "user": b["user"], "reason": b["reason"], "person": b["person"]})
    }).collect();
    Ok(json!({"log": out}))
}

fn enrolment(public: &str, about: &str, at_ms: u64) -> Vec<u8> {
    format!("{ENROLL_DOMAIN}{}", canonical(&json!({"op": "enroll", "public": public, "about": about, "at_ms": at_ms}))).into_bytes()
}

/// What an enrolled key signs to approve a new key on one node, once, in one terminal: the new key's proof, who approves, the
/// node's own certificate fingerprint, the time, a nonce and the terminal the approval was signed in.
fn approval(proof: &Map<String, Value>, by: &str, node: &str, anchor: &Anchor) -> Vec<u8> {
    let field = |k: &str| proof.get(k).cloned().unwrap_or(Value::Null);
    let body = json!({"op": "approve", "public": field("public"), "about": field("about"), "at_ms": field("at_ms"), "approved_ms": field("approved_ms"),
                      "by": by, "node": node, "nonce": field("nonce"), "anchor": anchor.body()});
    format!("{APPROVE_DOMAIN}{}", canonical(&body)).into_bytes()
}

/// Whether the process asking is in the terminal ``anchor`` names.
fn in_terminal(peer: &Peer, anchor: &Anchor) -> Result<()> {
    if peer.lineage().contains(anchor) {
        Ok(())
    } else {
        Err(Error::Denied("that signature was made in another terminal than the one asking".into()))
    }
}

/// A nonce of 16 to 64 letters and digits.
fn nonce_of<'a>(p: &'a Map<String, Value>, what: &str) -> Result<&'a str> {
    let nonce = text(p, "nonce")?;
    if nonce.len() < 16 || nonce.len() > 64 || !nonce.bytes().all(|b| b.is_ascii_alphanumeric()) {
        return Err(Error::Invalid(format!("{what} carries a nonce of 16 to 64 letters and digits")));
    }
    Ok(nonce)
}

/// Enrol a person key. The key signs its own enrolment. The first key also presents the one-time secret a person's command
/// at a terminal left on this device; once a key is enrolled, an enrolled key approves each new one, for this node, once, in
/// the terminal it signed in.
fn enroll(node: &mut Node, token: &str, p: &Map<String, Value>, peer: &Peer) -> Result<Value> {
    let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("a registered session carries a person key to enrol".into()))?;
    let (public, about) = (text(p, "public")?, text(p, "about")?);
    let at = fresh(node, p, "at_ms")?;
    if about.len() > 60 || !is_line(about) || !about.is_ascii() {
        return Err(Error::Invalid("about is one plain line of at most 60 characters".into()));
    }
    let message = enrolment(public, about, at);
    if !verifies(public, &message, text(p, "proof")?) {
        return Err(Error::Denied("a person key signs its own enrolment".into()));
    }
    if node.persons.is_empty() {
        if !node.firsts.take(crate::presence::Purpose::Key, text(p, "secret")?, text(p, "public")?, node.now_ms()) {
            return Err(Error::Denied("the first person key is enrolled by `poolhouse-workspace grants-setup`, with the one-time secret the operating system's prompt makes when the person says yes".into()));
        }
    } else {
        let by = text(p, "by")?;
        if !node.persons.all().contains_key(by) {
            return Err(Error::Denied("an enrolled person key approves each new one, for this node".into()));
        }
        fresh(node, p, "approved_ms")?;
        let (nonce, anchor) = (nonce_of(p, "an approval")?, Anchor::signed(p)?);
        if !node.persons.signed(by, &approval(p, by, &node.cert.fingerprint(), &anchor), text(p, "approval")?) {
            return Err(Error::Denied("an enrolled person key approves each new one, for this node and the terminal it is made in".into()));
        }
        in_terminal(peer, &anchor)?;
        if !node.persons.once(nonce, node.now_ms())? {
            return Err(Error::Denied("that approval was used already".into()));
        }
    }
    let fingerprint = node.persons.add(public, about, node.now_ms())?;
    node.record_event("person.enroll", &fingerprint, &format!("{about}; carried by {}/{}", holder.board, holder.name))?;
    Ok(json!({"fingerprint": fingerprint, "keys": node.persons.all().len()}))
}

/// What an enrolled key signs to remove a key from one node, once, in one terminal: the key, who removes it, the time, the node's own
/// certificate fingerprint, a nonce and the terminal the removal was signed in.
fn unenrolment(fingerprint: &str, by: &str, at_ms: u64, device: &str, nonce: &str, anchor: &Anchor) -> Vec<u8> {
    let body = json!({"op": "unenroll", "fingerprint": fingerprint, "by": by, "at_ms": at_ms, "node": device, "nonce": nonce, "anchor": anchor.body()});
    format!("{UNENROLL_DOMAIN}{}", canonical(&body)).into_bytes()
}

/// Remove a person key: an enrolled key signs the removal, for this node and once. The last key stays; a key that is removed
/// is not enrolled again and signs no grant that counts.
fn unenroll(node: &mut Node, token: &str, p: &Map<String, Value>, peer: &Peer) -> Result<Value> {
    let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("a registered session carries the removal of a person key".into()))?;
    let (fingerprint, by) = (text(p, "fingerprint")?, text(p, "by")?);
    if Authority::of(node).lists(fingerprint) {
        return Err(Error::Denied(LISTED.into()));
    }
    let at = fresh(node, p, "at_ms")?;
    let (nonce, anchor) = (nonce_of(p, "a removal")?, Anchor::signed(p)?);
    if !node.persons.signed(by, &unenrolment(fingerprint, by, at, &node.cert.fingerprint(), nonce, &anchor), text(p, "sig")?) {
        return Err(Error::Denied("an enrolled person key signs the removal of a key, for this node and the terminal it is made in".into()));
    }
    in_terminal(peer, &anchor)?;
    if !node.persons.once(nonce, node.now_ms())? {
        return Err(Error::Denied("that removal was used already".into()));
    }
    let key = node.persons.remove(fingerprint, node.now_ms())?;
    node.record_event("person.unenroll", fingerprint, &format!("{}; signed by {by}; carried by {}/{}", key.label, holder.board, holder.name))?;
    Ok(json!({"fingerprint": fingerprint, "keys": node.persons.all().len()}))
}

fn keys(node: &Node) -> Value {
    let keys: Vec<Value> = node.persons.all().iter().map(|(f, k)| json!({"fingerprint": f, "about": k.label, "at_ms": k.at_ms, "alg": k.alg})).collect();
    json!({"keys": keys})
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>, peer: &Peer) -> Result<Value> {
    match method {
        "grant_issue" => issue(node, board, token, p),
        "grant_revoke" => revoke(node, board, token, p),
        "grant_distrust" => distrust(node, token, p),
        "grants" => listing(node, board, token),
        "grant_log" => log(node, board, token, p),
        "person_enroll" => enroll(node, token, p, peer),
        "person_unenroll" => unenroll(node, token, p, peer),
        _ => Ok(keys(node)),
    }
}
