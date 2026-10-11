//! The local API: one request value in, one response value out. No socket here.
//!
//! A request is `{v, id, method, board, token, params}`. Who is asking comes from the token
//! alone; a request that carries a sender, name, parent or label of its own is refused.

use std::collections::BTreeMap;
use std::sync::atomic::Ordering;
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fold::Entry;
use crate::lineage::{limit_key, Asking, Peer};
use crate::node::{Native, Node, READ_DEFAULT, READ_MAX};
use crate::row::{valid_name, Kind};

pub const API_VERSION: u64 = 1;
/// How many links and how many projects one lineage makes in a minute.
const MOST_LINKS_PER_MINUTE: usize = 6;
const MOST_PROJECTS_PER_MINUTE: usize = 10;
const MINUTE: Duration = Duration::from_secs(60);
const ENVELOPE: [&str; 6] = ["v", "id", "method", "board", "token", "params"];
const FORBIDDEN: [&str; 9] = ["sender", "name", "parent", "label", "from", "author", "actor", "identity", "as"];

fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "hello" | "status" | "shutdown" | "links" | "lineage" => &[],
        "register" => &["model", "harness", "session"],
        "post" => &["kind", "idem", "fields"],
        "read" => &["since", "kind", "channel", "by", "limit", "inbox", "with", "thread", "digest", "mentions", "head", "files"],
        "link" => &["to", "channels", "mode"],
        "unlink" => &["id"],
        "project_add" | "source_add" => &["id", "kind", "path"],
        "project_list" => &[],
        "project_resolve" => &["path"],
        m => return crate::poolapi::params(m).or_else(|| crate::lease::rpc::params_for(m)).or_else(|| crate::sessions::params_for(m))
            .or_else(|| crate::claims::params_for(m)).or_else(|| crate::events::params_for(m)).or_else(|| crate::landing::params_for(m)).or_else(|| crate::notes::params_for(m))
            .or_else(|| crate::mail::params_for(m)).or_else(|| crate::quarantine::params_for(m))
            .or_else(|| crate::tasks::params_for(m)).or_else(|| crate::rooms::params_for(m)).or_else(|| crate::subs::params_for(m)).or_else(|| crate::files::params_for(m)).or_else(|| crate::scratch::params_for(m))
            .or_else(|| crate::grants::calls::params_for(m)).or_else(|| crate::approvals::calls::params_for(m)).or_else(|| crate::phone::calls::params_for(m)).or_else(|| crate::presence::params_for(m)),
    })
}

fn refuse_identity(map: &Map<String, Value>) -> Result<()> {
    match map.keys().find(|k| FORBIDDEN.contains(&k.to_lowercase().as_str())) {
        Some(k) => Err(Error::Denied(format!("`{k}` is not a field: the node knows who you are from your token"))),
        None => Ok(()),
    }
}

fn str_of<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        Some(Value::String(s)) => Ok(s),
        None => Ok(""),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

pub fn entry_json(e: &Entry) -> Value {
    json!({"id": e.id, "origin": e.origin, "seq": e.seq, "hlc": [e.hlc.0, e.hlc.1], "kind": e.kind, "sender": e.sender,
           "foreign": e.foreign, "channel": e.channel, "fields": e.fields})
}

fn read(node: &mut Node, token: &str, board: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let mut cursor: BTreeMap<String, u64> = match p.get("since") {
        None => BTreeMap::new(),
        Some(v) => serde_json::from_value(v.clone()).map_err(|_| Error::Invalid("since maps origins to sequence numbers".into()))?,
    };
    let presented = cursor.clone();
    let limit = p.get("limit").and_then(Value::as_u64).map_or(READ_DEFAULT, |n| (n as usize).clamp(1, READ_MAX));
    let (kind, channel, sender, with) = (str_of(p, "kind")?, str_of(p, "channel")?, str_of(p, "by")?, str_of(p, "with")?);
    let flag = |k: &str| p.get(k).and_then(Value::as_bool).unwrap_or(false);
    let (inbox, digest, mentioned, files) = (flag("inbox"), flag("digest"), flag("mentions"), flag("files"));
    let me = access.actor.as_str();
    if inbox && access.holder.board == board {
        node.host(board)?.names.note_inbox_read(me, &presented)?;
    }
    let entries = node.view(board)?;
    if flag("head") {
        return Ok(json!({"entries": [], "cursor": node.host(board)?.board.vector().into_iter().map(|(o, t)| (o, t.seq)).collect::<BTreeMap<String, u64>>()}));
    }
    let rooms = crate::rooms::fold(&entries);
    let subs = node.host(board)?.subs.of(me);
    let thread = match str_of(p, "thread")? {
        "" => None,
        reference => Some(crate::mail::root_of(node, board, &entries, me, reference)?),
    };
    let mut out = Vec::new();
    for e in entries {
        let kind_name = serde_json::to_value(e.kind)?;
        let to = e.fields.get("to").and_then(Value::as_str).unwrap_or("");
        let direct = e.channel == crate::fold::DIRECT;
        let wanted = e.seq > cursor.get(&e.origin).copied().unwrap_or(0)
            && access.channels.as_ref().is_none_or(|c| c.contains(&e.channel)) && e.readable_by(me) && crate::rooms::may_read(&rooms, &e, me)
            && (!inbox || direct && to == me || crate::subs::delivered(&e, me, &subs, "inbox"))
            && (!digest || crate::subs::delivered(&e, me, &subs, "digest"))
            && (!files || e.kind == Kind::Message && e.fields.contains_key("file"))
            && (!mentioned || e.kind == Kind::Message && e.sender != me && crate::subs::mentions(e.fields.get("body").and_then(Value::as_str).unwrap_or(""), me))
            && (with.is_empty() || direct && (e.sender == with && to == me || e.sender == me && to == with))
            && (kind.is_empty() || kind_name == kind) && (channel.is_empty() || e.channel == channel)
            && (sender.is_empty() || e.sender == sender) && thread.as_ref().is_none_or(|t| crate::mail::in_thread(&e, t));
        if wanted && out.len() < limit {
            cursor.insert(e.origin.clone(), e.seq);
            out.push(entry_json(&e));
        }
    }
    if files {
        let gone = crate::files::gone(node, board);
        for v in &mut out {
            v["gone"] = json!(v["fields"]["file"]["id"].as_str().is_some_and(|h| gone.contains(h)));
        }
    }
    Ok(json!({"entries": out, "cursor": cursor}))
}

fn post(node: &mut Node, token: &str, board: &str, p: &Map<String, Value>) -> Result<Value> {
    let kind = match str_of(p, "kind")? {
        "message" => Kind::Message,
        "note" => Kind::Note,
        _ => return Err(Error::Invalid("post writes a message or a note".into())),
    };
    let fields = p.get("fields").and_then(Value::as_object).ok_or_else(|| Error::Invalid("fields is an object".into()))?;
    refuse_identity(fields)?;
    let mut fields = fields.clone();
    if kind == Kind::Message && !fields.contains_key("to") {
        fields.insert("to".into(), json!(crate::fold::GENERAL));
    }
    if kind == Kind::Message {
        crate::mail::link_reply(node, token, board, &mut fields)?;
    }
    if kind == Kind::Note {
        crate::notes::resolve_supersedes(node, board, &mut fields)?;
    }
    node.post(token, board, kind, &fields, str_of(p, "idem")?)
}

/// The refusal for a lineage that did ``what`` as often as it may.
fn too_often(node: &mut Node, key: &str, most: usize, what: &str) -> Result<()> {
    if node.rates.permits(key, most, MINUTE) {
        return Ok(());
    }
    Err(Error::Quota(format!("this process tree {what} as fast as it may: try again in a minute")))
}

/// Make or end a link. What is done is checked first, the grant is spent once it was done, and the lineage's count goes up
/// only for a link that was made.
fn link(node: &mut Node, token: &str, board: &str, p: &Map<String, Value>, make: bool, peer: &Peer) -> Result<Value> {
    let access = node.access(token, board, true)?;
    if access.holder.board != board {
        return Err(Error::Denied("only a session of the sharing board makes or revokes its links".into()));
    }
    let by = format!("{}/{}", board, access.actor);
    let key = format!("link {}", limit_key(&peer.lineage()));
    let (done, holder, action, resource) = if make {
        let channels: Vec<String> = serde_json::from_value(p.get("channels").cloned().unwrap_or(json!([])))
            .map_err(|_| Error::Invalid("channels is a list of #names".into()))?;
        let write = match str_of(p, "mode")? { "ro" | "" => false, "rw" => true, _ => return Err(Error::Invalid("mode is ro or rw".into())) };
        let to = str_of(p, "to")?;
        if !valid_name(to) || !node.boards.contains_key(to) {
            return Err(Error::Invalid("a link goes to a board this node hosts".into()));
        }
        node.links.room_for(board)?;
        let (action, resource) = (if write { "link_write" } else { "link_read" }, format!("{board}>{to}"));
        let holder = crate::poolapi::require(node, token, action, &resource)?;
        too_often(node, &key, MOST_LINKS_PER_MINUTE, "makes links")?;
        (node.links.add(board, to, channels, write, &by)?, holder, action, resource)
    } else {
        let id = str_of(p, "id")?;
        let holder = crate::poolapi::require(node, token, "link_remove", id)?;
        (node.links.revoke(board, id)?, holder, "link_remove", id.to_string())
    };
    if let Err(e) = crate::poolapi::spend(node, token, &holder, action, &resource) {
        if make {
            let _ = node.links.revoke(board, &done.id);
        }
        return Err(e);
    }
    if make {
        node.rates.record(&key, MINUTE);
    }
    let detail = format!("{}>{} {} {}", done.from, done.to, if done.write { "rw" } else { "ro" }, done.channels.join(","));
    let event = if make { "link" } else { "unlink" };
    for side in [&done.from, &done.to] {
        let actor = if side == board { access.actor.clone() } else { format!("{}@{board}", access.actor) };
        node.host(side)?.board.append(Kind::Audit, &actor, json!({"event": event, "subject": done.id, "detail": detail}), "")?;
    }
    Ok(json!(done))
}

/// Add a project, or a place to one. A place that is not a git repository needs the grant `source_add` on the project; what is
/// added is checked first, the grant is spent once it was added, and the lineage's count goes up only for a project that was made.
fn project(node: &mut Node, method: &str, token: &str, p: &Map<String, Value>, peer: &Peer) -> Result<Value> {
    let (id, kind, path) = (str_of(p, "id")?, str_of(p, "kind")?, str_of(p, "path")?);
    let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("a registered session adds a project or a place to one".into()))?;
    if holder.board != id {
        return Err(Error::Denied("only a session of a board adds its project or a place to it".into()));
    }
    let key = format!("project {}", limit_key(&peer.lineage()));
    let made = method == "project_add";
    let granted = (!made || (kind != "git" && crate::registry::KINDS.contains(&kind))).then(|| crate::poolapi::require(node, token, "source_add", id)).transpose()?;
    if made {
        too_often(node, &key, MOST_PROJECTS_PER_MINUTE, "takes projects")?;
    }
    let done = if made { node.projects.add(id, kind, path)? } else { node.projects.add_source(id, kind, path)? };
    if let Some(holder) = granted {
        crate::poolapi::spend(node, token, &holder, "source_add", id)?;
    }
    if made {
        node.rates.record(&key, MINUTE);
    }
    node.host(id)?;
    Ok(json!(done))
}

fn status(node: &mut Node, token: &str, board: &str) -> Result<Value> {
    let mut out = json!({"v": API_VERSION, "pid": std::process::id(), "version": env!("CARGO_PKG_VERSION"),
                         "boards": node.boards.len(), "uptime_s": node.started.elapsed().as_secs()});
    if !token.is_empty() {
        let access = node.access(token, board, false)?;
        let mut counts: BTreeMap<String, u64> = BTreeMap::new();
        for e in node.view(board)? {
            *counts.entry(serde_json::to_value(e.kind)?.as_str().unwrap_or("").to_string()).or_default() += 1;
        }
        let h = node.host(board)?;
        out["board"] = json!({"id": board, "origin": h.board.origin(), "sessions": h.names.len(), "damaged": h.board.damaged(),
                              "rejected": h.board.rejected().len(), "name": access.actor, "entries": counts});
    }
    Ok(out)
}

fn dispatch(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>, peer: &Peer) -> Result<Value> {
    match method {
        "hello" => Ok(json!({"v": API_VERSION, "node": "poolhouse-node", "version": env!("CARGO_PKG_VERSION"),
                             "pid": std::process::id(), "fingerprint": node.fingerprint(), "device": node.cert.fingerprint(),
                             "build": node.build.id, "build_seq": node.build.seq, "wire": node.build.wire})),
        "register" => {
            let native = Native { model: str_of(p, "model")?, harness: str_of(p, "harness")?, session: str_of(p, "session")? };
            node.register(board, (!token.is_empty()).then_some(token), native, peer)
        }
        "status" => status(node, token, board),
        "post" => post(node, token, board, p),
        "read" => read(node, token, board, p),
        "link" | "unlink" => link(node, token, board, p, method == "link", peer),
        "project_add" | "source_add" => project(node, method, token, p, peer),
        "lineage" => Ok(json!({"lineage": peer.lineage()})),
        "project_list" => Ok(json!(node.projects.all())),
        "project_resolve" => node.projects.resolve(str_of(p, "path")?).map(|p| json!({"board": p.id})),
        m if crate::poolapi::handles(m) => crate::poolapi::dispatch(node, m, token, p),
        m if crate::lease::rpc::METHODS.contains(&m) => crate::lease::rpc::call(node, m, board, token, p),
        m if crate::sessions::METHODS.contains(&m) => crate::sessions::call(node, m, board, token, p, peer),
        m if crate::claims::METHODS.contains(&m) => crate::claims::call(node, m, board, token, p),
        m if crate::events::METHODS.contains(&m) => crate::events::call(node, m, board, token, p),
        m if crate::landing::METHODS.contains(&m) => crate::landing::call(node, m, board, token, p),
        m if crate::notes::METHODS.contains(&m) => crate::notes::call(node, m, board, token, p),
        m if crate::mail::METHODS.contains(&m) => crate::mail::call(node, m, board, token, p),
        m if crate::tasks::METHODS.contains(&m) => crate::tasks::calls::call(node, m, board, token, p),
        m if crate::rooms::METHODS.contains(&m) => crate::rooms::call(node, m, board, token, p),
        m if crate::subs::METHODS.contains(&m) => crate::subs::call(node, m, board, token, p),
        m if crate::files::METHODS.contains(&m) => crate::files::call(node, m, board, token, p),
        m if crate::scratch::METHODS.contains(&m) => crate::scratch::call(node, m, board, token, p),
        m if crate::quarantine::METHODS.contains(&m) => crate::quarantine::call(node, m, board, token, p),
        m if crate::grants::calls::METHODS.contains(&m) => crate::grants::calls::call(node, m, board, token, p, peer),
        m if crate::approvals::calls::METHODS.contains(&m) => crate::approvals::calls::call(node, m, board, token, p),
        m if crate::phone::calls::METHODS.contains(&m) => crate::phone::calls::call(node, m, token, p),
        m if crate::presence::METHODS.contains(&m) => crate::presence::call(node, m, token, p),
        "links" => node.access(token, board, false).map(|_| json!(node.links.all().iter().filter(|l| l.from == board || l.to == board).collect::<Vec<_>>())),
        "shutdown" => node.access(token, board, false).map(|_| {
            node.stop.store(true, Ordering::SeqCst);
            json!({"stopping": true})
        }),
        _ => Err(Error::Invalid("unknown method".into())),
    }
}

/// Answer one request made by the process ``peer``.
pub fn handle(node: &mut Node, request: &Value, peer: &Peer) -> Value {
    respond(request, run(node, request, peer))
}

/// The reply to ``request`` carrying ``result``.
pub fn respond(request: &Value, result: Result<Value>) -> Value {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    match result {
        Ok(result) => json!({"v": API_VERSION, "id": id, "ok": true, "result": result}),
        Err(e) => {
            let mut error = json!({"code": e.code(), "message": e.to_string()});
            if let Error::NoGrant { grant, .. } = &e {
                error["grant"] = grant.clone();
            }
            if let Error::Say { say, needs, then, .. } = &e {
                (error["say"], error["needs"], error["then"]) = (json!(say), json!(needs), json!(then));
            }
            json!({"v": API_VERSION, "id": id, "ok": false, "error": error})
        }
    }
}

/// A request that passed the envelope checks.
pub struct Call {
    pub method: String,
    pub board: String,
    pub token: String,
    pub params: Map<String, Value>,
}

/// Check the envelope, the version, the method and its params, and refuse any field that names a sender.
pub fn parse(request: &Value) -> Result<Call> {
    let map = request.as_object().ok_or_else(|| Error::Invalid("a request is an object".into()))?;
    refuse_identity(&map.keys().map(|k| (k.clone(), Value::Null)).collect())?;
    if let Some(k) = map.keys().find(|k| !ENVELOPE.contains(&k.as_str())) {
        return Err(Error::Invalid(format!("unknown request field {}", k.chars().take(40).collect::<String>())));
    }
    if map.get("v").and_then(Value::as_u64) != Some(API_VERSION) {
        return Err(Error::Invalid(format!("this node speaks version {API_VERSION}")));
    }
    let method = map.get("method").and_then(Value::as_str).unwrap_or("");
    let allowed = params_for(method).ok_or_else(|| Error::Invalid("unknown method".into()))?;
    let params = map.get("params").map_or(Ok(Map::new()), |p| p.as_object().cloned().ok_or_else(|| Error::Invalid("params is an object".into())))?;
    refuse_identity(&params)?;
    if let Some(k) = params.keys().find(|k| !allowed.contains(&k.as_str())) {
        return Err(Error::Invalid(format!("unknown param {}", k.chars().take(40).collect::<String>())));
    }
    let (board, token) = (str_of(map, "board")?, str_of(map, "token")?);
    if board.is_empty() && !matches!(method, "hello" | "status" | "lineage" | "project_add" | "source_add" | "project_list" | "project_resolve") && !crate::poolapi::handles(method) && !crate::grants::calls::boardless(method)
        && !crate::approvals::calls::boardless(method) && !crate::phone::calls::boardless(method) && !crate::presence::boardless(method) {
        return Err(Error::Invalid("this method names a board".into()));
    }
    Ok(Call { method: method.into(), board: board.into(), token: token.into(), params })
}

fn run(node: &mut Node, request: &Value, peer: &Peer) -> Result<Value> {
    let c = parse(request)?;
    node.asking = Asking::of(&c.token, peer);
    let done = dispatch(node, &c.method, &c.board, &c.token, &c.params, peer);
    node.asking = Asking::default();
    let done = done?;
    if let Some(holder) = node.tokens.resolve(&c.token).cloned() {
        // a retired session's token is gone by now, so it is never marked active again
        let hosted = node.host(&holder.board)?;
        let now = hosted.board.now_ms();
        hosted.names.touch(&holder.name, now)?;
    }
    Ok(done)
}
