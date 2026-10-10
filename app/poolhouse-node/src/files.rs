//! Files on the board: the message carries a handle and a description, the bytes stay on the device.
//!
//! A client seals a file (the node never sees its plain bytes), sends it in chunks with `file_put`,
//! which stores it once under the SHA-256 of what was stored, and posts a message of type `file`
//! whose `file` field describes it. `file_get` gives the stored bytes back only to a session that may
//! read a message carrying the blob. `file_delete` shreds the bytes of every blob a handle names and
//! remembers the handle, so the messages stay on the board and the file is gone.

use std::collections::BTreeSet;
use std::path::PathBuf;

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fold::Entry;
use crate::fsutil::{hex, private_dir, sha256_hex, unhex, write_atomic};
use crate::node::Node;
use crate::row::{is_line, Kind};

pub const METHODS: [&str; 3] = ["file_put", "file_get", "file_delete"];
/// The grant a session needs to delete a file.
pub const DELETE: &str = "file_delete";
/// The most bytes one stored blob may take.
pub const MAX_BLOB: usize = 9 * 1024 * 1024;
/// The most bytes one `file_put` or `file_get` moves.
pub const CHUNK: usize = 256 * 1024;
const MAX_STAGED: usize = 8;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "file_put" => &["upload", "offset", "total", "data"],
        "file_get" => &["blob", "offset", "limit"],
        "file_delete" => &["handle"],
        _ => return None,
    })
}

fn is_hex(text: &str, len: usize) -> bool {
    text.len() == len && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Check the `file` field of a message with the rules a peer applies to a row it receives.
pub fn check_meta(meta: &Value) -> Result<()> {
    let map = meta.as_object().ok_or_else(|| Error::Invalid("file is an object".into()))?;
    const KEYS: [&str; 8] = ["id", "blob", "sha", "name", "size", "type", "text", "derived_from"];
    if let Some(k) = map.keys().find(|k| !KEYS.contains(&k.as_str())) {
        return Err(Error::Invalid(format!("unknown file field {}", k.chars().take(40).collect::<String>())));
    }
    let text = |k: &str, most: usize| -> Result<&str> {
        match map.get(k) {
            None => Ok(""),
            Some(Value::String(s)) if s.len() <= most && is_line(s) => Ok(s),
            _ => Err(Error::Invalid(format!("file {k} is one short line"))),
        }
    };
    if !is_hex(text("id", 12)?, 12) || !is_hex(text("blob", 64)?, 64) || !is_hex(text("sha", 64)?, 64) {
        return Err(Error::Invalid("a file names its handle, blob and hash in hex".into()));
    }
    if text("name", 240)?.is_empty() || text("type", 16)?.is_empty() || !map.get("size").is_some_and(|s| s.as_u64().is_some_and(|n| n <= MAX_BLOB as u64)) {
        return Err(Error::Invalid("a file has a name, a type and a size".into()));
    }
    text("derived_from", 128)?;
    if !map.get("text").is_some_and(Value::is_boolean) {
        return Err(Error::Invalid("file text is true or false".into()));
    }
    Ok(())
}

fn dir(node: &Node, board: &str) -> PathBuf {
    node.dir.join("boards").join(board).join("files")
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

fn number(p: &Map<String, Value>, key: &str) -> Result<usize> {
    match p.get(key) {
        None => Ok(0),
        Some(v) => v.as_u64().map(|n| n as usize).ok_or_else(|| Error::Invalid(format!("{key} is a whole number"))),
    }
}

fn deleted(node: &Node, board: &str) -> BTreeSet<String> {
    std::fs::read(dir(node, board).join("deleted.json")).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default()
}

/// The handles of files that were deleted on this device.
pub fn gone(node: &Node, board: &str) -> BTreeSet<String> {
    deleted(node, board)
}

fn put(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    node.access(token, board, true)?;
    let (upload, offset, total) = (text(p, "upload")?, number(p, "offset")?, number(p, "total")?);
    let data = unhex(text(p, "data")?).ok_or_else(|| Error::Invalid("data is hex".into()))?;
    if upload.len() < 8 || upload.len() > 64 || !upload.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit()) {
        return Err(Error::Invalid("an upload is named by 8 to 64 lower-case letters and digits".into()));
    }
    if total == 0 || total > MAX_BLOB || data.is_empty() || data.len() > CHUNK || offset + data.len() > total {
        return Err(Error::Quota(format!("a file is up to {MAX_BLOB} bytes, sent in chunks of up to {CHUNK}")));
    }
    let base = dir(node, board);
    let staging = base.join("staging");
    private_dir(&base)?;
    private_dir(&staging)?;
    private_dir(&base.join("blobs"))?;
    let part = staging.join(format!("{upload}.part"));
    let have = std::fs::metadata(&part).map_or(0, |m| m.len() as usize);
    if have != offset {
        return Err(Error::Invalid(format!("the upload holds {have} bytes; the next chunk starts there")));
    }
    if offset == 0 && std::fs::read_dir(&staging)?.count() >= MAX_STAGED {
        return Err(Error::Quota("too many uploads are waiting; finish or wait for them".into()));
    }
    let mut bytes = std::fs::read(&part).unwrap_or_default();
    bytes.extend_from_slice(&data);
    if bytes.len() < total {
        write_atomic(&part, &bytes)?;
        return Ok(json!({"staged": bytes.len()}));
    }
    let blob = sha256_hex(&bytes);
    let target = base.join("blobs").join(format!("{blob}.bin"));
    if !target.exists() {
        write_atomic(&target, &bytes)?;
    }
    let _ = std::fs::remove_file(&part);
    Ok(json!({"blob": blob, "size": total}))
}

/// The entry that lets ``actor`` read the blob, if there is one and its file was not deleted.
fn reaching(node: &mut Node, board: &str, actor: &str, channels: Option<&Vec<String>>, blob: &str) -> Result<Option<Entry>> {
    let entries = node.view(board)?;
    let rooms = crate::rooms::fold(&entries);
    let gone = deleted(node, board);
    Ok(entries.into_iter().find(|e| {
        e.kind == Kind::Message && e.readable_by(actor) && crate::rooms::may_read(&rooms, e, actor) && channels.is_none_or(|c| c.contains(&e.channel))
            && e.fields.get("file").is_some_and(|f| f["blob"] == blob && f["id"].as_str().is_some_and(|h| !gone.contains(h)))
    }))
}

fn get(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, false)?;
    let (blob, offset) = (text(p, "blob")?, number(p, "offset")?);
    let limit = p.get("limit").and_then(Value::as_u64).map_or(CHUNK, |n| (n as usize).clamp(1, CHUNK));
    if !is_hex(blob, 64) || reaching(node, board, &access.actor, access.channels.as_ref(), blob)?.is_none() {
        return Err(Error::Denied("that file is not available to you".into()));
    }
    let bytes = std::fs::read(dir(node, board).join("blobs").join(format!("{blob}.bin"))).map_err(|_| Error::Denied("that file is not stored here".into()))?;
    let end = bytes.len().min(offset.saturating_add(limit));
    Ok(json!({"data": hex(&bytes[offset.min(end)..end]), "size": bytes.len(), "offset": offset.min(end)}))
}

fn delete(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    if !node.grants.allows(&access.holder, DELETE) {
        return Err(Error::Denied("this session may not delete a file".into()));
    }
    let handle = text(p, "handle")?;
    if !is_hex(handle, 12) {
        return Err(Error::Invalid("a file handle is 12 hex characters".into()));
    }
    let blobs: BTreeSet<String> = node.view(board)?.iter().filter_map(|e| e.fields.get("file")).filter(|f| f["id"] == handle).filter_map(|f| f["blob"].as_str().map(String::from)).collect();
    if blobs.is_empty() {
        return Err(Error::Invalid("no such file".into()));
    }
    let base = dir(node, board);
    let mut shredded = 0;
    for blob in &blobs {
        let path = base.join("blobs").join(format!("{blob}.bin"));
        if let Ok(meta) = std::fs::metadata(&path) {
            std::fs::write(&path, vec![0u8; meta.len() as usize])?;
            std::fs::remove_file(&path)?;
            shredded += 1;
        }
    }
    let mut all = deleted(node, board);
    all.insert(handle.to_string());
    private_dir(&base)?;
    write_atomic(&base.join("deleted.json"), &serde_json::to_vec(&all)?)?;
    node.host(board)?.board.append(Kind::Audit, &access.actor, json!({"event": "file.delete", "subject": handle, "detail": format!("{shredded} blobs shredded")}), "")?;
    Ok(json!({"deleted": handle, "shredded": shredded}))
}

/// Refuse a file message whose blob is not stored on this device.
pub fn check_post(node: &Node, board: &str, fields: &Map<String, Value>) -> Result<()> {
    let Some(meta) = fields.get("file") else { return Ok(()) };
    check_meta(meta)?;
    let blob = meta["blob"].as_str().unwrap_or("");
    if !dir(node, board).join("blobs").join(format!("{blob}.bin")).exists() {
        return Err(Error::Invalid("the file was not sent to the node first".into()));
    }
    Ok(())
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "file_put" => put(node, board, token, p),
        "file_get" => get(node, board, token, p),
        _ => delete(node, board, token, p),
    }
}
