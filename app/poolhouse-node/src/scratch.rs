//! Scratch folders: private working space a session makes for itself, kept by the node.
//!
//! A folder lives at `<state>/scratch/<board>/<session>/<name>`, owner-only, with a size limit
//! and an expiry. Only the session that made it, or a session above it in the spawn tree, may
//! list, resolve or remove it, and a path inside never leaves the folder. A session that is a
//! spawned subagent has no scratch space of its own. The node removes a session's expired
//! folders whenever it lists or makes one.

use std::path::{Component, Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fsutil::{private_dir, write_atomic};
use crate::node::Node;

pub const METHODS: [&str; 4] = ["scratch_new", "scratch_ls", "scratch_path", "scratch_rm"];
const MAX_BYTES: u64 = 256 * 1024 * 1024;
const TTL_S: u64 = 3 * 86_400;
const MOST_FOLDERS: usize = 16;
const META: &str = ".scratch.json";

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "scratch_new" => &["folder", "ttl_s"],
        "scratch_ls" => &["owner"],
        "scratch_path" => &["folder", "relative", "owner"],
        "scratch_rm" => &["folder", "owner"],
        _ => return None,
    })
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

fn usable(name: &str) -> bool {
    let bytes = name.as_bytes();
    !bytes.is_empty() && bytes.len() <= 64 && bytes[0].is_ascii_alphanumeric() && !name.contains("..")
        && bytes.iter().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b, b'.' | b'_' | b'-'))
}

fn linked(path: &Path) -> bool {
    std::fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink())
}

fn size_of(folder: &Path) -> u64 {
    let Ok(read) = std::fs::read_dir(folder) else { return 0 };
    read.flatten().map(|e| match e.file_type() {
        Ok(t) if t.is_dir() => size_of(&e.path()),
        Ok(t) if t.is_file() => e.metadata().map_or(0, |m| m.len()),
        _ => 0,
    }).sum()
}

/// The folder of ``owner`` on ``board`` when ``caller`` may reach it: the caller itself or a session above it.
fn owner_dir(node: &mut Node, board: &str, caller: &str, asked: &str) -> Result<(String, PathBuf)> {
    let owner = if asked.is_empty() { caller.to_string() } else { asked.to_string() };
    if owner != caller && !node.host(board)?.names.descendants(caller).contains(&owner) {
        return Err(Error::Denied(format!("{caller} cannot reach {owner}'s scratch folders")));
    }
    let root = node.dir.join("scratch");
    let dir = root.join(board).join(&owner);
    if linked(&root) || linked(&root.join(board)) || linked(&dir) {
        return Err(Error::Denied(format!("{owner}'s scratch directory is not a plain directory")));
    }
    Ok((owner, dir))
}

fn main_session(node: &mut Node, board: &str, token: &str, write: bool) -> Result<String> {
    let access = node.access(token, board, write)?;
    if access.holder.board != board || node.host(board)?.names.get(&access.actor).is_none_or(|i| !i.parent.is_empty()) {
        return Err(Error::Denied("only a main session of the board uses scratch folders".into()));
    }
    Ok(access.actor)
}

fn meta_of(folder: &Path) -> (u64, u64) {
    let doc: Value = std::fs::read(folder.join(META)).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
    (doc["expires_ms"].as_u64().unwrap_or(0), doc["max_bytes"].as_u64().unwrap_or(MAX_BYTES))
}

fn rows(node: &mut Node, board: &str, owner: &str, dir: &Path) -> Vec<Value> {
    let now = node.host(board).map_or(0, |h| h.board.now_ms());
    let Ok(read) = std::fs::read_dir(dir) else { return Vec::new() };
    let mut folders: Vec<PathBuf> = read.flatten().map(|e| e.path()).filter(|p| p.is_dir() && !linked(p)).collect();
    folders.sort();
    folders.iter().map(|f| {
        let (expires, limit) = meta_of(f);
        let used = size_of(f);
        json!({"owner": owner, "name": f.file_name().and_then(|n| n.to_str()).unwrap_or(""), "path": f.to_string_lossy(), "bytes": used, "limit": limit,
               "over_limit": used > limit, "expires_ms": expires, "expired": expires > 0 && now > expires})
    }).collect()
}

/// Remove the folders of ``owner`` whose time ran out.
fn sweep(node: &mut Node, board: &str, owner: &str, dir: &Path) -> Result<()> {
    for row in rows(node, board, owner, dir) {
        if row["expired"] == true {
            std::fs::remove_dir_all(row["path"].as_str().unwrap_or(""))?;
        }
    }
    Ok(())
}

fn make(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = main_session(node, board, token, true)?;
    let name = text(p, "folder")?;
    if !usable(name) {
        return Err(Error::Invalid(format!("{name:?} is not a usable folder name")));
    }
    let (_, dir) = owner_dir(node, board, &actor, "")?;
    sweep(node, board, &actor, &dir)?;
    let mine = rows(node, board, &actor, &dir);
    let folder = dir.join(name);
    if mine.len() >= MOST_FOLDERS && !folder.is_dir() {
        return Err(Error::Quota(format!("{actor} already has {MOST_FOLDERS} scratch folders")));
    }
    let used: u64 = mine.iter().map(|r| r["bytes"].as_u64().unwrap_or(0)).sum();
    if used > MAX_BYTES {
        return Err(Error::Quota(format!("{actor}'s scratch folders hold {used} bytes, over the limit")));
    }
    if linked(&folder) {
        return Err(Error::Denied(format!("{name} is a link, not a folder")));
    }
    private_dir(&node.dir.join("scratch"))?;
    private_dir(&node.dir.join("scratch").join(board))?;
    private_dir(&dir)?;
    private_dir(&folder)?;
    let now = node.host(board)?.board.now_ms();
    let ttl = p.get("ttl_s").and_then(Value::as_u64).filter(|t| *t > 0).unwrap_or(TTL_S).min(30 * 86_400);
    write_atomic(&folder.join(META), &serde_json::to_vec(&json!({"version": 1, "created_ms": now, "expires_ms": now + ttl * 1000, "max_bytes": MAX_BYTES}))?)?;
    Ok(json!({"path": folder.to_string_lossy()}))
}

fn resolve(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = main_session(node, board, token, false)?;
    let (name, relative) = (text(p, "folder")?, text(p, "relative")?);
    let (_, dir) = owner_dir(node, board, &actor, text(p, "owner")?)?;
    let out = || Error::Denied("the path is outside the scratch folder".into());
    if !usable(name) || Path::new(relative).is_absolute() {
        return Err(out());
    }
    let folder = dir.join(name);
    if linked(&folder) {
        return Err(out());
    }
    let mut target = folder.clone();
    for part in Path::new(relative).components() {
        match part {
            Component::Normal(n) => target.push(n),
            Component::CurDir => {}
            Component::ParentDir if target != folder => {
                target.pop();
            }
            _ => return Err(out()),
        }
    }
    let mut anchor = target.as_path();
    while !anchor.exists() {
        anchor = anchor.parent().ok_or_else(out)?;
    }
    let (real, top) = (std::fs::canonicalize(anchor)?, std::fs::canonicalize(&folder).map_err(|_| out())?);
    if !real.starts_with(&top) {
        return Err(out());
    }
    Ok(json!({"path": target.to_string_lossy()}))
}

fn remove(node: &mut Node, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let actor = main_session(node, board, token, true)?;
    let name = text(p, "folder")?;
    let (_, dir) = owner_dir(node, board, &actor, text(p, "owner")?)?;
    if !usable(name) {
        return Err(Error::Denied("the path is outside the scratch folder".into()));
    }
    let folder = dir.join(name);
    if !folder.is_dir() || linked(&folder) {
        return Ok(json!({"removed": false}));
    }
    std::fs::remove_dir_all(&folder)?;
    Ok(json!({"removed": true}))
}

pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "scratch_new" => make(node, board, token, p),
        "scratch_path" => resolve(node, board, token, p),
        "scratch_rm" => remove(node, board, token, p),
        _ => {
            let actor = main_session(node, board, token, false)?;
            let (owner, dir) = owner_dir(node, board, &actor, text(p, "owner")?)?;
            if owner == actor {
                sweep(node, board, &actor, &dir)?;
            }
            Ok(json!({"folders": rows(node, board, &owner, &dir)}))
        }
    }
}
