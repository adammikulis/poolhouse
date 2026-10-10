//! Messages as conversations: a reply is tied to the thread it answers, a thread reads back with
//! all its replies, and the sender's outbox says which of its messages every device that syncs
//! the board holds.

use serde_json::{json, Map, Value};

use crate::api::entry_json;
use crate::error::{Error, Result};
use crate::fold::Entry;
use crate::node::Node;
use crate::row::Kind;

pub const METHODS: [&str; 1] = ["outbox"];
const OUTBOX_DEFAULT: usize = 50;
const OUTBOX_MAX: usize = 200;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "outbox" => &["limit"],
        _ => return None,
    })
}

/// The id a message reference names: a bare number is a message of this device, anything else an id.
pub fn resolve(board: &str, own_origin: &str, reference: &str) -> String {
    if !reference.is_empty() && reference.bytes().all(|b| b.is_ascii_digit()) {
        format!("{board}:{own_origin}:{reference}")
    } else {
        reference.to_string()
    }
}

fn thread_root(e: &Entry) -> String {
    e.fields.get("thread_id").and_then(Value::as_str).filter(|t| !t.is_empty()).unwrap_or(&e.id).to_string()
}

/// Tie a message that answers another to the root of that thread. The caller never names the
/// thread: the node sets it from the message answered, which must exist and be readable by the sender.
pub fn link_reply(node: &mut Node, token: &str, board: &str, fields: &mut Map<String, Value>) -> Result<()> {
    fields.remove("thread_id");
    let reply = fields.get("reply_id").and_then(Value::as_str).unwrap_or("").to_string();
    if reply.is_empty() {
        return Ok(());
    }
    let access = node.access(token, board, true)?;
    let own = node.host(board)?.board.origin().to_string();
    let id = resolve(board, &own, &reply);
    let parent = node.view(board)?.into_iter().find(|e| e.id == id && e.kind == Kind::Message)
        .ok_or_else(|| Error::Invalid(format!("no message {reply} to reply to")))?;
    if !parent.readable_by(&access.actor) || access.channels.as_ref().is_some_and(|c| !c.contains(&parent.channel)) {
        return Err(Error::Denied("the sender is not a participant in the referenced thread".into()));
    }
    fields.insert("thread_id".into(), json!(thread_root(&parent)));
    Ok(())
}

/// The id of the root of the thread that ``reference`` belongs to, among the messages ``viewer`` may read.
pub fn root_of(node: &mut Node, board: &str, entries: &[Entry], viewer: &str, reference: &str) -> Result<String> {
    let own = node.host(board)?.board.origin().to_string();
    let id = resolve(board, &own, reference);
    entries.iter().find(|e| e.id == id && e.kind == Kind::Message && e.readable_by(viewer)).map(thread_root)
        .ok_or_else(|| Error::Invalid(format!("no thread {reference}")))
}

/// Whether ``e`` is the root of thread ``root`` or a reply in it.
pub fn in_thread(e: &Entry, root: &str) -> bool {
    e.kind == Kind::Message && (e.id == root || e.fields.get("thread_id").and_then(Value::as_str) == Some(root))
}

/// The messages the caller sent, newest last, each `synced` once every device that syncs this
/// board holds it, else `provisional`.
pub fn call(node: &mut Node, _method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let limit = p.get("limit").and_then(Value::as_u64).map_or(OUTBOX_DEFAULT, |n| (n as usize).clamp(1, OUTBOX_MAX));
    let sent: Vec<Entry> = node.view(board)?.into_iter().filter(|e| e.kind == Kind::Message && !e.foreign && e.sender == access.actor).collect();
    let hosted = node.host(board)?;
    let roster = hosted.board.peer_fingerprints();
    let from = sent.len().saturating_sub(limit);
    let out: Vec<Value> = sent[from..].iter().map(|e| {
        let mut v = entry_json(e);
        v["status"] = json!(hosted.board.status(e.seq, &roster));
        v
    }).collect();
    Ok(json!({"entries": out}))
}
