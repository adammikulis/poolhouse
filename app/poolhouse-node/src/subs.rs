//! What reaches a session's inbox: its subscriptions, kept on this device beside the board.
//!
//! A session subscribes to a room or `#announcements`, a thread, a sender, a kind of message, or
//! its mentions, each in a mode: `inbox` (read with the direct messages, wakes `wait`), `digest`
//! (collected for `digest`) or `silent`. A subscription delivers only what arrives after it was
//! made, as a map of origins to the last sequence number seen. With no subscription a mention
//! reaches the inbox and `#announcements` the digest, and nothing else is delivered.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fold::{valid_room, Entry, ANNOUNCE, GENERAL};
use crate::fsutil::write_atomic;
use crate::node::Node;
use crate::row::Kind;

pub const METHODS: [&str; 3] = ["subscribe", "unsubscribe", "subs"];
const TYPES: [&str; 5] = ["board", "thread", "agent", "kind", "mentions"];
const MODES: [&str; 3] = ["inbox", "digest", "silent"];
const MESSAGE_TYPES: [&str; 9] = ["task", "status", "handoff", "question", "answer", "claim", "release", "note", "file"];
const LOUD_FREE: usize = 3;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "subscribe" => &["stype", "target", "mode", "force"],
        "unsubscribe" => &["stype", "target"],
        "subs" => &[],
        _ => return None,
    })
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Sub {
    pub stype: String,
    pub target: String,
    pub mode: String,
    /// For each origin, the last sequence number that existed when the subscription was made.
    pub since: BTreeMap<String, u64>,
}

pub struct Subs {
    path: PathBuf,
    by_name: BTreeMap<String, Vec<Sub>>,
}

impl Subs {
    pub fn open(path: &Path) -> Result<Subs> {
        let by_name = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("subscriptions unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        Ok(Subs { path: path.into(), by_name })
    }

    fn save(&self) -> Result<()> {
        write_atomic(&self.path, &serde_json::to_vec(&self.by_name)?)
    }

    pub fn of(&self, name: &str) -> Vec<Sub> {
        self.by_name.get(name).cloned().unwrap_or_default()
    }
}

/// Whether ``body`` names ``me`` as `@name`: not inside a word, a path or another `@`, and not as the start of a longer name.
pub fn mentions(body: &str, me: &str) -> bool {
    let bytes = body.as_bytes();
    let name_byte = |b: u8| b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b, b'.' | b'_' | b'-');
    let mut from = 0;
    while let Some(at) = body[from..].find('@') {
        let start = from + at;
        let before_ok = start == 0 || !(bytes[start - 1].is_ascii_alphanumeric() || matches!(bytes[start - 1], b'_' | b'/' | b'@'));
        let end = start + 1 + body[start + 1..].bytes().take_while(|b| name_byte(*b)).count();
        let name = body[start + 1..end].trim_end_matches('.');
        let end = start + 1 + name.len();
        if before_ok && name == me && body[end..].bytes().next().is_none_or(|b| b == b'.' || !(name_byte(b) || b == b'/')) {
            return true;
        }
        from = start + 1;
    }
    false
}

/// The subscription that decides how ``e`` reaches ``me`` and the mode it decides: a subscribed thread first,
/// then a mention, a subscribed room, a followed sender and a subscribed kind; `None` and an empty mode when
/// nothing does. `#announcements` is a digest unless the session silenced it.
pub fn route<'a>(e: &Entry, me: &str, subs: &'a [Sub]) -> (Option<&'a Sub>, &'static str) {
    let find = |stype: &str, target: &str| subs.iter().find(|s| s.stype == stype && s.target == target);
    let mode = |s: &Sub| MODES.iter().find(|m| **m == s.mode).copied().unwrap_or("silent");
    if e.channel == ANNOUNCE {
        return match find("board", ANNOUNCE) {
            Some(s) if s.mode == "silent" => (Some(s), "silent"),
            s => (s, "digest"),
        };
    }
    let thread = e.fields.get("thread_id").and_then(Value::as_str).unwrap_or(&e.id);
    if let Some(s) = find("thread", thread) {
        return (Some(s), mode(s));
    }
    if mentions(e.fields.get("body").and_then(Value::as_str).unwrap_or(""), me) {
        return match find("mentions", "") {
            Some(s) => (Some(s), mode(s)),
            None => (None, "inbox"),
        };
    }
    if let Some(s) = find("board", &e.channel) {
        return (Some(s), mode(s));
    }
    if let Some(s) = subs.iter().find(|s| s.stype == "agent" && (e.actor == s.target || e.actor.starts_with(&format!("{}/", s.target)))) {
        return (Some(s), mode(s));
    }
    let kind = e.fields.get("type").and_then(Value::as_str).unwrap_or("");
    match find("kind", kind) {
        Some(s) => (Some(s), mode(s)),
        None => (None, ""),
    }
}

/// Whether ``e`` is delivered to ``me`` in ``wanted`` mode: from someone else, routed so by a subscription
/// that was made before it arrived (mentions and the announcement default need none).
pub fn delivered(e: &Entry, me: &str, subs: &[Sub], wanted: &str) -> bool {
    if e.kind != Kind::Message || e.sender == me {
        return false;
    }
    let (sub, mode) = route(e, me, subs);
    mode == wanted && sub.is_none_or(|s| s.stype == "mentions" || e.seq > s.since.get(&e.origin).copied().unwrap_or(0))
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

fn check_target(node: &mut Node, board: &str, actor: &str, stype: &str, target: &str) -> Result<String> {
    match stype {
        "mentions" => Ok(String::new()),
        "board" => {
            let entries = node.view(board)?;
            let rooms = crate::rooms::fold(&entries);
            let ok = target == GENERAL || target == ANNOUNCE || valid_room(target) && rooms.get(target).is_some_and(|r| r.members.contains(actor));
            if ok { Ok(target.into()) } else { Err(Error::Denied(format!("no room {target} that {actor} belongs to"))) }
        }
        "agent" => {
            if node.can_receive(board, target)? { Ok(target.into()) } else { Err(Error::Invalid(format!("no session called {target}"))) }
        }
        "kind" => {
            if MESSAGE_TYPES.contains(&target) { Ok(target.into()) } else { Err(Error::Invalid(format!("kind is one of {}", MESSAGE_TYPES.join(", ")))) }
        }
        _ => {
            let entries = node.view(board)?;
            let rooms = crate::rooms::fold(&entries);
            let readable: Vec<Entry> = entries.into_iter().filter(|e| e.readable_by(actor) && crate::rooms::may_read(&rooms, e, actor)).collect();
            crate::mail::root_of(node, board, &readable, actor, target)
        }
    }
}

fn subscribe(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    let actor = access.actor.clone();
    let (stype, asked, force) = (text(p, "stype")?, text(p, "target")?, p.get("force").and_then(Value::as_bool).unwrap_or(false));
    let mode = if text(p, "mode")?.is_empty() { "inbox" } else { text(p, "mode")? };
    if !TYPES.contains(&stype) {
        return Err(Error::Invalid(format!("subscribe to one of {}", TYPES.join(", "))));
    }
    if !MODES.contains(&mode) {
        return Err(Error::Invalid(format!("mode must be one of {}", MODES.join(", "))));
    }
    if access.holder.board != board || node.host(board)?.names.get(&actor).is_none_or(|i| !i.parent.is_empty()) {
        return Err(Error::Denied("only a main session of the board subscribes".into()));
    }
    let target = check_target(node, board, &actor, stype, asked)?;
    if (stype, target.as_str()) == ("board", ANNOUNCE) && mode == "inbox" {
        return Err(Error::Invalid(format!("{ANNOUNCE} is a roll-up, never inbox: use digest or silent")));
    }
    let hosted = node.host(board)?;
    let mine = hosted.subs.of(&actor);
    let existing = mine.iter().find(|s| s.stype == stype && s.target == target).cloned();
    let loud = mine.iter().filter(|s| s.mode == "inbox" && s.stype != "mentions").count();
    let mut cost = "";
    if mode == "inbox" && stype != "mentions" && existing.as_ref().is_none_or(|s| s.mode != "inbox") {
        if loud >= LOUD_FREE && !force {
            return Err(crate::limits::refuse(&format!("{actor} already has {loud} inbox subscriptions; every message on {stype} {target} would reach your inbox and your context, and wake `wait`. Use mode digest (a roll-up you read when you choose) or force to accept the cost"), LOUD_FREE, "LOUD_FREE", "subs.rs"));
        }
        if loud >= LOUD_FREE {
            cost = "forced: each new message here now enters your inbox and context";
        }
    }
    if existing.as_ref().is_none_or(|s| s.mode != mode) {
        let since = hosted.board.vector().into_iter().map(|(o, t)| (o, t.seq)).collect();
        let list = hosted.subs.by_name.entry(actor.clone()).or_default();
        list.retain(|s| !(s.stype == stype && s.target == target));
        list.push(Sub { stype: stype.into(), target: target.clone(), mode: mode.into(), since });
        hosted.subs.save()?;
    }
    let mut out = json!({"type": stype, "target": target, "mode": mode});
    if !cost.is_empty() {
        out["cost"] = json!(cost);
    }
    Ok(out)
}

fn unsubscribe(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    let (stype, asked) = (text(p, "stype")?, text(p, "target")?);
    if !TYPES.contains(&stype) {
        return Err(Error::Invalid(format!("unsubscribe from one of {}", TYPES.join(", "))));
    }
    let target = if stype == "mentions" { String::new() } else if stype == "thread" {
        let entries = node.view(board)?;
        crate::mail::root_of(node, board, &entries, &access.actor, asked).unwrap_or_else(|_| asked.to_string())
    } else { asked.to_string() };
    if (stype, target.as_str()) == ("board", ANNOUNCE) {
        return subscribe(node, board, token, &serde_json::from_value(json!({"stype": "board", "target": ANNOUNCE, "mode": "silent"}))?);
    }
    let hosted = node.host(board)?;
    let list = hosted.subs.by_name.entry(access.actor.clone()).or_default();
    let before = list.len();
    list.retain(|s| !(s.stype == stype && s.target == target));
    if list.len() == before {
        return Err(Error::Invalid("no such subscription".into()));
    }
    hosted.subs.save()?;
    Ok(json!({"removed": {"type": stype, "target": target}}))
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "subscribe" => subscribe(node, board, token, p),
        "unsubscribe" => unsubscribe(node, board, token, p),
        _ => {
            let access = node.access(token, board, false)?;
            let mut mine = node.host(board)?.subs.of(&access.actor);
            mine.sort_by(|a, b| (&a.stype, &a.target).cmp(&(&b.stype, &b.target)));
            Ok(json!({"subs": mine.iter().map(|s| json!({"type": s.stype, "target": s.target, "mode": s.mode})).collect::<Vec<_>>()}))
        }
    }
}
