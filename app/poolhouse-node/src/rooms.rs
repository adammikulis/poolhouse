//! Named rooms of a board: `#name` channels sessions make, join, leave and add each other to.
//!
//! A room is a fold of the board's `room` entries, so every device computes the same members from
//! the same log: the first `create` makes it; an open room is joined by whoever asks; a private one
//! only by the maker's `add`; a member leaves. Members are the names the writers' logs carry
//! (`Entry.actor`), never the labels a device gives a foreign origin. A message to a room is
//! readable, and may be written, only by a member; `#general` and `#announcements` are everyone's.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fold::{valid_room, Entry, ANNOUNCE, GENERAL};
use crate::node::Node;
use crate::row::Kind;

pub const METHODS: [&str; 2] = ["rooms", "room"];
const MADE_PER_SESSION: usize = 5;
const ROOMS_TOTAL: usize = 200;
const JOINED_PER_SESSION: usize = 32;
const MEMBERS_PER_ROOM: usize = 64;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "rooms" => &[],
        "room" => &["op", "room", "who", "title", "private"],
        _ => return None,
    })
}

#[derive(Clone, Debug)]
pub struct Room {
    pub by: String,
    pub title: String,
    pub open: bool,
    pub members: BTreeSet<String>,
    pub created_ms: u64,
}

fn field<'a>(e: &'a Entry, key: &str) -> &'a str {
    e.fields.get(key).and_then(Value::as_str).unwrap_or("")
}

/// The rooms the `room` entries of a board describe, in the order the entries merge.
pub fn fold(entries: &[Entry]) -> BTreeMap<String, Room> {
    let mut rooms: BTreeMap<String, Room> = BTreeMap::new();
    for e in entries.iter().filter(|e| e.kind == Kind::Room) {
        let name = field(e, "room").to_string();
        match field(e, "op") {
            "create" if !rooms.contains_key(&name) => {
                let open = e.fields.get("open").and_then(Value::as_bool).unwrap_or(true);
                let room = Room { by: e.actor.clone(), title: field(e, "title").into(), open, members: BTreeSet::from([e.actor.clone()]), created_ms: e.hlc.0 };
                rooms.insert(name, room);
            }
            "join" => {
                if let Some(r) = rooms.get_mut(&name).filter(|r| r.open) {
                    r.members.insert(e.actor.clone());
                }
            }
            "add" => {
                if let Some(r) = rooms.get_mut(&name).filter(|r| r.by == e.actor) {
                    r.members.insert(field(e, "who").into());
                }
            }
            "leave" => {
                if let Some(r) = rooms.get_mut(&name) {
                    r.members.remove(&e.actor);
                }
            }
            _ => {}
        }
    }
    rooms
}

/// Whether ``viewer`` may read ``e``: anything outside a room, and a room's messages for its members.
pub fn may_read(rooms: &BTreeMap<String, Room>, e: &Entry, viewer: &str) -> bool {
    e.kind != Kind::Message || !valid_room(&e.channel) || rooms.get(&e.channel).is_some_and(|r| r.members.contains(viewer))
}

/// Refuse a message to a room that does not exist or that ``actor`` does not belong to.
pub fn check_post(node: &mut Node, board: &str, actor: &str, fields: &Map<String, Value>) -> Result<()> {
    let to = fields.get("to").and_then(Value::as_str).unwrap_or("");
    if !to.starts_with('#') || to == GENERAL {
        return Ok(());
    }
    if to == ANNOUNCE {
        return Ok(());
    }
    if !valid_room(to) {
        return Err(Error::Invalid("a room name is # then lowercase letters, digits, . _ - (up to 40)".into()));
    }
    let rooms = fold(&node.view(board)?);
    if rooms.get(to).is_some_and(|r| r.members.contains(actor)) {
        Ok(())
    } else {
        Err(Error::Denied(format!("no room {to} that {actor} belongs to; join it first")))
    }
}

fn show(name: &str, r: &Room, viewer: &str, entries: &[Entry]) -> Value {
    let member = r.members.contains(viewer);
    let mine: Vec<&Entry> = if member { entries.iter().filter(|e| e.kind == Kind::Message && e.channel == name).collect() } else { Vec::new() };
    json!({"name": name, "title": r.title, "open": r.open, "created_by": r.by, "created_ms": r.created_ms,
           "members": r.members.iter().collect::<Vec<_>>(), "member": member, "posts": mine.len(),
           "last_ms": mine.last().map_or(r.created_ms, |e| e.hlc.0)})
}

fn everyones(name: &str, entries: &[Entry]) -> Value {
    let mine: Vec<&Entry> = entries.iter().filter(|e| e.kind == Kind::Message && e.channel == name).collect();
    json!({"name": name, "title": "", "open": true, "created_by": "", "created_ms": 0, "members": "everyone", "member": true,
           "posts": mine.len(), "last_ms": mine.last().map_or(0, |e| e.hlc.0)})
}

/// The rooms the caller may see: every open one, a private one only for its members, and the two everyone's.
fn list(node: &mut Node, board: &str, token: &str) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let entries = node.view(board)?;
    let rooms = fold(&entries);
    let mut out = vec![everyones(GENERAL, &entries), everyones(ANNOUNCE, &entries)];
    out.extend(rooms.iter().filter(|(_, r)| r.open || r.members.contains(&access.actor)).map(|(n, r)| show(n, r, &access.actor, &entries)));
    Ok(json!({"rooms": out}))
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

fn admit(rooms: &BTreeMap<String, Room>, name: &str, member: &str) -> Result<()> {
    let r = &rooms[name];
    if r.members.contains(member) {
        return Ok(());
    }
    if r.members.len() >= MEMBERS_PER_ROOM {
        return Err(Error::Quota(format!("{name} has {MEMBERS_PER_ROOM} members already")));
    }
    let joined = rooms.values().filter(|r| r.members.contains(member)).count();
    if joined >= JOINED_PER_SESSION {
        return Err(Error::Quota(format!("{member} belongs to {joined} rooms already")));
    }
    Ok(())
}

fn op(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    let actor = access.actor.clone();
    let (op, name, who, title) = (text(p, "op")?, text(p, "room")?, text(p, "who")?, text(p, "title")?);
    if access.holder.board != board || node.host(board)?.names.get(&actor).is_none_or(|i| !i.parent.is_empty()) {
        return Err(Error::Denied("only a main session of the board makes, joins, leaves or adds to rooms".into()));
    }
    if !valid_room(name) {
        return Err(Error::Invalid("a room name is # then lowercase letters, digits, . _ - (up to 40)".into()));
    }
    let rooms = fold(&node.view(board)?);
    let mut body = json!({"op": op, "room": name});
    match op {
        "create" => {
            if rooms.contains_key(name) {
                return Err(Error::Invalid(format!("{name} exists already")));
            }
            let made = rooms.values().filter(|r| r.by == actor).count();
            if made >= MADE_PER_SESSION || rooms.len() >= ROOMS_TOTAL {
                return Err(Error::Quota(format!("{actor} made {made} rooms already; the limit is {MADE_PER_SESSION} ({ROOMS_TOTAL} in all)")));
            }
            body["title"] = json!(title);
            body["open"] = json!(!p.get("private").and_then(Value::as_bool).unwrap_or(false));
        }
        "join" => {
            let r = rooms.get(name).filter(|r| r.open || r.members.contains(&actor));
            if r.is_none() {
                return Err(Error::Denied(format!("{name} is not a room {actor} can join; ask its maker to add you")));
            }
            admit(&rooms, name, &actor)?;
        }
        "leave" => {
            if rooms.get(name).is_none_or(|r| !r.members.contains(&actor)) {
                return Err(Error::Invalid(format!("{actor} is not a member of {name}")));
            }
        }
        "add" => {
            if rooms.get(name).is_none_or(|r| r.by != actor) {
                return Err(Error::Denied(format!("{actor} cannot add members to {name}; only its maker does")));
            }
            if who.contains('@') || !node.can_receive(board, who)? {
                return Err(Error::Invalid(format!("no session called {who}")));
            }
            admit(&rooms, name, who)?;
            body["who"] = json!(who);
        }
        _ => return Err(Error::Invalid("op is create, join, leave or add".into())),
    }
    crate::fold::check_local(Kind::Room, &actor, &body, "")?;
    let hosted = node.host(board)?;
    hosted.board.append(Kind::Room, &actor, body.clone(), "")?;
    hosted.board.append(Kind::Audit, &actor, json!({"event": format!("board.{op}"), "subject": name, "detail": who}), "")?;
    Ok(json!({"room": name, "op": op, "open": body.get("open").and_then(Value::as_bool).unwrap_or_else(|| rooms.get(name).is_none_or(|r| r.open))}))
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "rooms" => list(node, board, token),
        _ => op(node, board, token, p),
    }
}
