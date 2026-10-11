//! What a grant says: the fixed registry of actions, the floors no grant reaches, who a grant names, where it
//! applies, and the bytes a person signs.

use serde_json::{Map, Value};

use crate::cert::valid_fingerprint;
use crate::error::{Error, Result};
use crate::row::{canonical, valid_name};

/// The actions of this node that need a grant. Anything else is refused by name.
pub const ACTIONS: [&str; 17] = [
    "member_remove", "set_join_policy", "add_device", "join_pool", "sync_now", "shard_call", "shard_consent", "land_trust", "land_control",
    "task_authority", "note_verify", "quarantine_release", "file_delete", "link_read", "link_write", "link_remove", "source_add",
];
/// The actions another device's grant may cover (when that device is trusted); every other action is this device's alone.
pub const PORTABLE: [&str; 4] = ["task_authority", "note_verify", "sync_now", "shard_call"];
/// Never granted, whatever a board entry says, whoever signed it.
pub const FLOORS: [&str; 14] = [
    "push_main", "merge_main", "release_main", "tag_release", "force_push", "act_as_person", "agent_is_human", "keystore", "second_gpu", "forge",
    "grant_issue", "grant_revoke", "person_enroll", "sudo",
];
/// The longest a grant lasts.
pub const MAX_SPAN_MS: u64 = 366 * 24 * 3600 * 1000;
/// How far a signed time may differ from the node's clock.
pub const SKEW_MS: u64 = 5 * 60 * 1000;
pub const MOST_USES: u64 = 1_000_000;
const MOST_TEXT: usize = 120;
const SIGN_DOMAIN: &str = "poolhouse/grant/v1\n";

fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

/// Whether ``text`` is printable ASCII with no space, within ``most`` bytes.
pub fn plain(text: &str, most: usize) -> bool {
    !text.is_empty() && text.len() <= most && text.bytes().all(|b| b.is_ascii_graphic())
}

/// A grant id: `g-` and 16 to 32 lower-case hex digits.
pub fn valid_gid(gid: &str) -> bool {
    gid.strip_prefix("g-").is_some_and(|h| (16..=32).contains(&h.len()) && h.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
}

/// Whether ``action`` can be named by a grant; the reason when it cannot.
pub fn grantable(action: &str) -> Result<()> {
    if FLOORS.contains(&action) {
        return Err(Error::Denied(format!("{action} is a floor: no grant, from anyone, covers it")));
    }
    if !ACTIONS.contains(&action) {
        let shown: String = action.chars().filter(char::is_ascii_graphic).take(40).collect();
        return Err(Error::Invalid(format!("{shown} is not an action a grant can name")));
    }
    Ok(())
}

/// Who a grant is for.
#[derive(Clone, Debug, PartialEq)]
pub enum Grantee {
    /// One session, by its unique name; with ``subagents``, the subagents of that session (and theirs) too.
    Session { name: String, subagents: bool },
    /// Every session of a model family; `main` leaves out sessions that have a parent, `verified` those whose model is only claimed.
    Class { family: String, main: bool, verified: bool },
    /// Every session whose verified model is exactly ``model``.
    Account { model: String },
}

fn flag(map: &Map<String, Value>, key: &str, default: bool) -> Result<bool> {
    match map.get(key) {
        None => Ok(default),
        Some(Value::Bool(b)) => Ok(*b),
        _ => bad(&format!("{key} is true or false")),
    }
}

fn only(map: &Map<String, Value>, allowed: &[&str]) -> Result<()> {
    match map.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(k) => bad(&format!("unknown field {}", k.chars().filter(char::is_ascii_graphic).take(40).collect::<String>())),
        None => Ok(()),
    }
}

fn word<'a>(map: &'a Map<String, Value>, key: &str, most: usize) -> Result<&'a str> {
    match map.get(key) {
        Some(Value::String(s)) if plain(s, most) => Ok(s),
        _ => bad(&format!("{key} is one plain word of at most {most} characters")),
    }
}

fn object<'a>(map: &'a Map<String, Value>, key: &str) -> Result<&'a Map<String, Value>> {
    map.get(key).and_then(Value::as_object).ok_or_else(|| Error::Invalid(format!("{key} is an object")))
}

fn grantee(map: &Map<String, Value>) -> Result<Grantee> {
    match word(map, "kind", 12)? {
        "session" => {
            only(map, &["kind", "name", "subagents"])?;
            let name = word(map, "name", 48)?;
            if valid_name(name) { Ok(Grantee::Session { name: name.into(), subagents: flag(map, "subagents", false)? }) } else { bad("name is a session name") }
        }
        "class" => {
            only(map, &["kind", "family", "main", "verified"])?;
            Ok(Grantee::Class { family: word(map, "family", 16)?.into(), main: flag(map, "main", true)?, verified: flag(map, "verified", false)? })
        }
        "account" => {
            only(map, &["kind", "model"])?;
            Ok(Grantee::Account { model: word(map, "model", 120)?.into() })
        }
        _ => bad("to is a session, a class or an account"),
    }
}

/// Where a grant applies: the board, the device (a certificate fingerprint, or `*`) and a pattern over the resource.
#[derive(Clone, Debug, PartialEq)]
pub struct Place {
    pub board: String,
    pub device: String,
    pub resource: String,
}

fn place(map: &Map<String, Value>) -> Result<Place> {
    only(map, &["board", "device", "resource"])?;
    let (board, device, resource) = (word(map, "board", 48)?, word(map, "device", 64)?, word(map, "resource", MOST_TEXT)?);
    if !valid_name(board) || !(device == "*" || valid_fingerprint(device)) {
        return bad("scope names a board and a device fingerprint or *");
    }
    if resource.trim_end_matches('*').contains('*') || resource.contains("..") {
        return bad("a resource is an exact name, or a prefix ending in *, with no ..");
    }
    Ok(Place { board: board.into(), device: device.into(), resource: resource.into() })
}

/// Whether ``resource`` is covered by ``pattern``: `*`, an exact match, or a prefix before a final `*`.
pub fn covers(pattern: &str, resource: &str) -> bool {
    if pattern == "*" {
        return true;
    }
    if !resource.bytes().all(|b| b.is_ascii_graphic()) || resource.contains("..") {
        return false;
    }
    match pattern.strip_suffix('*') {
        Some(prefix) => resource.starts_with(prefix),
        None => pattern == resource,
    }
}

/// A grant as a person signed it.
#[derive(Clone, Debug, PartialEq)]
pub struct Issue {
    pub gid: String,
    pub to: Grantee,
    pub action: String,
    pub place: Place,
    pub at_ms: u64,
    pub until_ms: u64,
    pub uses: Option<u64>,
    /// Each use must come with an independent review of what it does.
    pub review: bool,
    /// The fingerprint of the person's key that signed it.
    pub person: String,
}

fn number(map: &Map<String, Value>, key: &str) -> Result<u64> {
    map.get(key).and_then(Value::as_u64).ok_or_else(|| Error::Invalid(format!("{key} is a whole number")))
}

fn conditions(map: &Map<String, Value>) -> Result<bool> {
    match map.get("conditions") {
        None => Ok(false),
        Some(Value::Object(c)) => {
            only(c, &["review"])?;
            flag(c, "review", false)
        }
        _ => bad("conditions is an object"),
    }
}

fn uses(map: &Map<String, Value>) -> Result<Option<u64>> {
    match map.get("uses") {
        None => Ok(None),
        Some(v) => v.as_u64().filter(|n| (1..=MOST_USES).contains(n)).map(Some).ok_or_else(|| Error::Invalid("uses is a whole number from 1".into())),
    }
}

/// Read and check the body of an `issue` entry (its shape, not its signature).
pub fn parse_issue(map: &Map<String, Value>) -> Result<Issue> {
    only(map, &["op", "board", "gid", "to", "action", "scope", "at_ms", "until_ms", "uses", "conditions", "person", "sig"])?;
    let (gid, action, person) = (word(map, "gid", 40)?, word(map, "action", 40)?, word(map, "person", 64)?);
    grantable(action)?;
    let (at_ms, until_ms) = (number(map, "at_ms")?, number(map, "until_ms")?);
    if !valid_gid(gid) || !valid_fingerprint(person) || until_ms <= at_ms || until_ms - at_ms > MAX_SPAN_MS {
        return bad("a grant has a gid and the person's key, and ends after it starts and within a year of it");
    }
    let place = place(object(map, "scope")?)?;
    if word(map, "board", 48)? != place.board {
        return bad("a grant's scope is the board it is on");
    }
    let uses = uses(map)?;
    if uses.is_some() && PORTABLE.contains(&action) && place.device == "*" {
        return bad("a grant with a number of uses on an action another device may use names the one device it is used on");
    }
    Ok(Issue { gid: gid.into(), to: grantee(object(map, "to")?)?, action: action.into(), place, at_ms, until_ms, uses, review: conditions(map)?, person: person.into() })
}

/// What a `revoke` entry names: the grant, the key that signed it and why.
pub struct Revoke {
    pub gid: String,
    pub person: String,
    pub reason: String,
    pub at_ms: u64,
}

/// Read and check the body of a `revoke` entry.
pub fn parse_revoke(map: &Map<String, Value>) -> Result<Revoke> {
    only(map, &["op", "board", "gid", "at_ms", "reason", "person", "sig"])?;
    let at_ms = number(map, "at_ms")?;
    let (gid, person) = (word(map, "gid", 40)?, word(map, "person", 64)?);
    let reason = match map.get("reason") {
        None => "",
        Some(Value::String(s)) if s.len() <= MOST_TEXT && s.bytes().all(|b| b.is_ascii_graphic() || b == b' ') => s,
        _ => return bad("a reason is plain text"),
    };
    if !valid_gid(gid) || !valid_fingerprint(person) {
        return bad("a revocation names a gid and the person's key");
    }
    Ok(Revoke { gid: gid.into(), person: person.into(), reason: reason.into(), at_ms })
}

/// Check the shape of any `grant` entry: an issue, a revoke, or a use.
pub fn check_entry(map: &Map<String, Value>) -> Result<()> {
    match map.get("op").and_then(Value::as_str) {
        Some("issue") => parse_issue(map).map(|_| ()),
        Some("revoke") => parse_revoke(map).map(|_| ()),
        Some("use") => {
            only(map, &["op", "gid", "action", "target", "user"])?;
            let (gid, action, target, user) = (word(map, "gid", 40)?, word(map, "action", 40)?, word(map, "target", MOST_TEXT)?, word(map, "user", 48)?);
            if valid_gid(gid) && ACTIONS.contains(&action) && valid_name(user) && !target.is_empty() { Ok(()) } else { bad("a use names a grant, an action, a target and the session that used it") }
        }
        _ => bad("a grant entry is an issue, a revoke or a use"),
    }
}

/// The bytes the person's key signs: a domain line and the canonical body without its signature.
pub fn signing_bytes(body: &Map<String, Value>) -> Vec<u8> {
    let mut plain = body.clone();
    plain.remove("sig");
    format!("{SIGN_DOMAIN}{}", canonical(&Value::Object(plain))).into_bytes()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pattern_covers_what_it_names_and_never_climbs() {
        assert!(covers("*", "anything") && covers("feat/*", "feat/x") && covers("dev", "dev"));
        assert!(!covers("feat/*", "dev") && !covers("dev", "dev2") && !covers("feat/*", "feat/../dev") && !covers("feat/*", "feat/\u{e9}"));
        assert!(!covers("feat/*", "*"), "no resource named is not every resource");
    }

    #[test]
    fn every_floor_is_refused_by_name_and_no_floor_is_in_the_registry() {
        for floor in FLOORS {
            assert!(matches!(grantable(floor), Err(Error::Denied(_))), "{floor}");
            assert!(!ACTIONS.contains(&floor));
        }
        for action in ACTIONS {
            assert!(grantable(action).is_ok() && !FLOORS.contains(&action), "{action}");
        }
        assert!(PORTABLE.iter().all(|a| ACTIONS.contains(a)));
        assert!(matches!(grantable("format_disk"), Err(Error::Invalid(_))));
    }

    #[test]
    fn a_grant_id_is_a_prefix_and_sixteen_to_thirty_two_hex_digits() {
        assert!(valid_gid("g-0123456789abcdef") && valid_gid(&format!("g-{}", "a".repeat(32))));
        assert!(!valid_gid("g-0123456789abcde") && !valid_gid(&format!("g-{}", "a".repeat(33))) && !valid_gid("x-0123456789abcdef") && !valid_gid("g-0123456789ABCDEF"));
    }
}
