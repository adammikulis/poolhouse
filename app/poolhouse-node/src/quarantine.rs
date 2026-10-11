//! Flagged inbound text, held out of sight until a person releases it.
//!
//! The node does not judge text. A reading client that matched an injection pattern in an entry
//! reports it with `quarantine_hold` and shows its reader a placeholder; the node keeps one record
//! per entry on this device (never synced: what a person here released is not released for another
//! device), and `quarantine_release` marks it shown, under the grant `quarantine_release`, which
//! only a person's session may hold once grants are owner-only.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fold::Entry;
use crate::grants::Scope;
use crate::fsutil::write_atomic;
use crate::node::Node;
use crate::row::{is_line, Kind};

pub const METHODS: [&str; 3] = ["quarantine_hold", "quarantine_list", "quarantine_release"];
/// The grant a session needs to release a held entry.
pub const RELEASE: &str = "quarantine_release";
const MAX_ITEMS: usize = 5000;
const MAX_PER_CALL: usize = 100;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "quarantine_hold" => &["entries"],
        "quarantine_list" => &[],
        "quarantine_release" => &["qid"],
        _ => return None,
    })
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Item {
    pub qid: String,
    pub entry: String,
    pub flags: Vec<String>,
    pub held_ms: u64,
    /// When a person released it; 0 while it is held.
    pub released_ms: u64,
    pub released_by: String,
}

pub struct Quarantine {
    path: PathBuf,
    items: BTreeMap<String, Item>,
}

impl Quarantine {
    pub fn open(path: &Path) -> Result<Quarantine> {
        let items = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("quarantine unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        Ok(Quarantine { path: path.into(), items })
    }

    fn save(&self) -> Result<()> {
        write_atomic(&self.path, &serde_json::to_vec(&self.items)?)
    }

    /// Hold ``entry`` (once: an entry already held or released keeps its record).
    fn hold(&mut self, entry: &str, flags: Vec<String>, now: u64) -> Result<&Item> {
        if !self.items.contains_key(entry) {
            if self.items.len() >= MAX_ITEMS {
                return Err(Error::Quota("this device already holds as many flagged entries as it keeps".into()));
            }
            let qid = format!("q{}", self.items.len() + 1);
            self.items.insert(entry.into(), Item { qid, entry: entry.into(), flags, held_ms: now, released_ms: 0, released_by: String::new() });
            self.save()?;
        }
        Ok(&self.items[entry])
    }
}

fn shown(i: &Item, e: &Entry) -> Value {
    let chars = e.fields.get("body").and_then(Value::as_str).map_or(0, |b| b.chars().count());
    json!({"qid": i.qid, "id": i.entry, "state": if i.released_ms == 0 { "quarantined" } else { "released" }, "flags": i.flags,
           "sender": e.sender, "chars": chars, "held_ms": i.held_ms, "released_ms": i.released_ms, "released_by": i.released_by})
}

fn flags_of(v: &Value) -> Result<Vec<String>> {
    match v {
        Value::Array(list) if !list.is_empty() && list.len() <= 16 && list.iter().all(|f| f.as_str().is_some_and(|s| !s.is_empty() && s.len() <= 40 && is_line(s))) => {
            Ok(list.iter().filter_map(|f| f.as_str().map(String::from)).collect())
        }
        _ => Err(Error::Invalid("flags are a short list of marker names".into())),
    }
}

fn hold(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let asked = p.get("entries").and_then(Value::as_array).filter(|a| a.len() <= MAX_PER_CALL).ok_or_else(|| Error::Invalid("entries is a list of at most 100".into()))?;
    let entries = node.view(board)?;
    let now = node.host(board)?.board.now_ms();
    let mut out = Vec::new();
    for a in asked {
        let (id, flags) = (a.get("id").and_then(Value::as_str).unwrap_or(""), flags_of(a.get("flags").unwrap_or(&Value::Null))?);
        let e = entries.iter().find(|e| e.id == id && matches!(e.kind, Kind::Message | Kind::Note))
            .ok_or_else(|| Error::Invalid("no such entry".into()))?;
        if !e.readable_by(&access.actor) || access.channels.as_ref().is_some_and(|c| !c.contains(&e.channel)) {
            return Err(Error::Denied("that entry is not readable by this session".into()));
        }
        let item = node.host(board)?.quarantine.hold(id, flags, now)?.clone();
        out.push(shown(&item, e));
    }
    Ok(json!({"items": out}))
}

fn list(node: &mut Node, board: &str, token: &str) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let entries = node.view(board)?;
    let hosted = node.host(board)?;
    let out: Vec<Value> = entries.iter().filter(|e| e.readable_by(&access.actor) && access.channels.as_ref().is_none_or(|c| c.contains(&e.channel)))
        .filter_map(|e| hosted.quarantine.items.get(&e.id).map(|i| shown(i, e))).collect();
    Ok(json!({"items": out}))
}

fn release(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    access.of_board(board, "releases a held entry")?;
    let qid = p.get("qid").and_then(Value::as_str).unwrap_or("");
    let message = "this session may not release a held entry";
    if !crate::grants::holds(node, &access.holder, RELEASE, &Scope::on(qid))? {
        return Err(crate::grants::refused_for(node, token, RELEASE, qid, message));
    }
    if !node.host(board)?.quarantine.items.values().any(|i| i.qid == qid) {
        return Err(Error::Invalid(format!("nothing is held as {qid}")));
    }
    if !crate::grants::check(node, &access.holder, RELEASE, &Scope::on(qid))? {
        return Err(crate::grants::refused_for(node, token, RELEASE, qid, message));
    }
    let entries = node.view(board)?;
    let hosted = node.host(board)?;
    let now = hosted.board.now_ms();
    let item = hosted.quarantine.items.values_mut().find(|i| i.qid == qid).ok_or_else(|| Error::Invalid(format!("nothing is held as {qid}")))?;
    if item.released_ms == 0 {
        item.released_ms = now.max(1);
        item.released_by = access.actor.clone();
    }
    let done = item.clone();
    hosted.quarantine.save()?;
    let e = entries.iter().find(|e| e.id == done.entry).ok_or_else(|| Error::Invalid("the held entry is no longer on the board".into()))?;
    hosted.board.append(Kind::Audit, &access.actor, json!({"event": "quarantine.release", "subject": done.entry, "detail": done.qid}), "")?;
    Ok(json!({"item": shown(&done, e)}))
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "quarantine_hold" => hold(node, board, token, p),
        "quarantine_list" => list(node, board, token),
        _ => release(node, board, token, p),
    }
}
