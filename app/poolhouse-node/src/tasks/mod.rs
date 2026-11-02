//! The task board as board entries: a task is made, assigned a model lease, claimed, checkpointed, submitted,
//! reviewed by someone independent and landed, and every step is a `task` entry stamped with the caller's name.
//! The state of a task is a fold of its entries (`fold`), so a second device computes the same board from the
//! same log; the calls (`calls`) check who may take each step before it is written, with the rules the fold
//! applies to what it reads.
//!
//! A claimed task holds a `task` claim lease of this device's table for two minutes at a time; a heartbeat renews
//! it and one that is not renewed ends like any lease, which is how a lost worker is noticed.

pub mod calls;
pub mod fold;
pub mod show;

use serde_json::{Map, Value};

use crate::error::{Error, Result};
use crate::row::{is_line, valid_actor, valid_name};

pub const METHODS: [&str; 18] = [
    "task_create", "task_list", "task_get", "task_watch", "task_assign", "task_claim", "task_beat", "task_checkpoint", "task_submit",
    "task_review", "task_block", "task_ready", "task_mode", "task_scope", "task_record", "task_complete", "task_watchers", "task_admit",
];
/// How long one heartbeat extends a task's lease.
pub const HEARTBEAT_S: u64 = 120;
pub const MAX_TASKS: usize = 5000;
/// The grant that lets a session with no parent act as the person for task authority.
pub const AUTHORITY: &str = "task_authority";
const OPS: [&str; 13] = ["create", "watch", "assign", "claim", "checkpoint", "submit", "review", "block", "ready", "enforce", "scope", "record", "complete"];
pub const KEYS: [&str; 43] = [
    "op", "task", "title", "description", "acceptance", "deps", "capabilities", "limits", "source_key", "assignees", "reviewers", "who", "on",
    "worker", "lease", "assign", "model", "at", "summary", "progress", "commit", "environment", "artifacts", "checks", "usage", "provenance",
    "observed", "proposal", "proposal_hash", "accepted", "outcome", "reason", "verified_usage", "quality", "review", "kind", "expired",
    "transition", "mode", "scope", "rid", "record", "landed",
];

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "task_create" => &["title", "description", "acceptance", "deps", "capabilities", "limits", "source_key", "assignees", "reviewers"],
        "task_list" => &[],
        "task_get" | "task_beat" | "task_watchers" | "task_admit" => &["task"],
        "task_watch" => &["task", "on", "who"],
        "task_assign" => &["task", "worker", "lease", "capabilities"],
        "task_claim" => &["task", "assign"],
        "task_checkpoint" => &["task", "summary", "progress", "commit", "environment"],
        "task_submit" => &["task", "artifacts", "checks", "usage", "summary", "provenance"],
        "task_review" => &["task", "accepted", "outcome", "reason", "checks", "verified_usage", "quality", "review"],
        "task_block" => &["task", "reason", "kind"],
        "task_ready" => &["task", "reason", "expired"],
        "task_mode" => &["mode"],
        "task_scope" => &["task", "scope"],
        "task_record" => &["task", "kind", "rid", "record"],
        "task_complete" => &["task", "landed"],
        _ => return None,
    })
}

fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

pub fn text<'a>(map: &'a Map<String, Value>, key: &str, most: usize) -> Result<&'a str> {
    match map.get(key) {
        None => Ok(""),
        Some(Value::String(s)) if s.len() <= most => Ok(s),
        _ => bad(&format!("{key} is not text within {most} bytes")),
    }
}

pub fn line<'a>(map: &'a Map<String, Value>, key: &str, most: usize) -> Result<&'a str> {
    let value = text(map, key, most)?;
    if is_line(value) { Ok(value) } else { bad(&format!("{key} is not one clean line")) }
}

/// The list at ``key`` as clean lines: at most ``most`` of them, each of at most ``each`` bytes.
pub fn lines(map: &Map<String, Value>, key: &str, most: usize, each: usize, unique: bool) -> Result<Vec<String>> {
    let out: Vec<String> = match map.get(key) {
        None => return Ok(Vec::new()),
        Some(Value::Array(a)) if a.len() <= most => a
            .iter()
            .map(|v| v.as_str().filter(|s| s.len() <= each && !s.trim().is_empty() && is_line(s)).map(String::from))
            .collect::<Option<_>>()
            .ok_or_else(|| Error::Invalid(format!("{key} holds clean lines of at most {each} bytes")))?,
        _ => return bad(&format!("{key} is a list of at most {most}")),
    };
    if unique && out.iter().enumerate().any(|(i, s)| out[..i].contains(s)) {
        return bad(&format!("{key} cannot contain duplicates"));
    }
    Ok(out)
}

fn names(map: &Map<String, Value>, key: &str) -> Result<()> {
    if lines(map, key, 32, 96, true)?.iter().all(|n| valid_actor(n)) { Ok(()) } else { bad(&format!("{key} names sessions")) }
}

fn small(map: &Map<String, Value>, key: &str, most: usize) -> Result<()> {
    match map.get(key) {
        None => Ok(()),
        Some(Value::Object(o)) if o.len() <= most => Ok(()),
        _ => bad(&format!("{key} is an object of at most {most} fields")),
    }
}

fn flag(map: &Map<String, Value>, key: &str) -> Result<()> {
    if map.get(key).is_some_and(|v| !v.is_boolean()) { bad(&format!("{key} is true or false")) } else { Ok(()) }
}

fn digest(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The recorded checks of a submission or a review: one to 32 named results, each passed or not.
pub fn checks(map: &Map<String, Value>, required: bool) -> Result<()> {
    match map.get("checks") {
        None if !required => Ok(()),
        Some(Value::Array(c)) if (1..=32).contains(&c.len()) => {
            let mut seen: Vec<&str> = Vec::new();
            for item in c {
                let o = item.as_object().filter(|o| o.len() == 2).ok_or_else(|| Error::Invalid("a check is a name and a passed result".into()))?;
                let name = o.get("name").and_then(Value::as_str).filter(|n| !n.trim().is_empty() && n.len() <= 500 && is_line(n));
                match (name, o.get("passed").is_some_and(Value::is_boolean)) {
                    (Some(n), true) if !seen.contains(&n) => seen.push(n),
                    _ => return bad("a check has a unique name and a boolean passed result"),
                }
            }
            Ok(())
        }
        _ => bad("checks hold one to 32 recorded results"),
    }
}

fn limits(map: &Map<String, Value>) -> Result<()> {
    let Some(l) = map.get("limits") else { return Ok(()) };
    let o = l.as_object().ok_or_else(|| Error::Invalid("limits is an object".into()))?;
    for (k, v) in o {
        let fine = match k.as_str() {
            "model" => v.as_str().is_some_and(|m| !m.trim().is_empty() && m.len() <= 1024 && is_line(m)),
            "max_wall_s" => v.as_u64().is_some_and(|n| n > 0),
            "max_retries" => v.as_u64().is_some_and(|n| n <= 10),
            _ => false,
        };
        if !fine {
            return bad("limits are model, a positive max_wall_s and max_retries from 0 to 10");
        }
    }
    Ok(())
}

fn submission(map: &Map<String, Value>) -> Result<()> {
    match map.get("artifacts") {
        Some(Value::Object(a)) if (1..=32).contains(&a.len()) && a.iter().all(|(k, v)| !k.trim().is_empty() && k.len() <= 500 && is_line(k) && v.as_str().is_some_and(digest)) => {}
        _ => return bad("one to 32 artifact names with SHA-256 hashes are required"),
    }
    checks(map, true)?;
    line(map, "summary", 2000)?;
    for key in ["usage", "observed"] {
        small(map, key, 12)?;
    }
    match map.get("provenance") {
        None => Ok(()),
        Some(Value::Object(p)) if p.iter().all(|(k, v)| matches!(k.as_str(), "commit" | "environment" | "model" | "runtime") && v.as_str().is_some_and(|s| s.len() <= 1024 && is_line(s))) => Ok(()),
        _ => bad("provenance is commit, environment, model and runtime"),
    }
}

fn review(map: &Map<String, Value>) -> Result<()> {
    flag(map, "accepted")?;
    if !matches!(line(map, "outcome", 32)?, "accepted" | "rejected" | "blocked_infrastructure") || line(map, "reason", 2000)?.trim().is_empty() {
        return bad("a review gives its outcome and its reason");
    }
    checks(map, true)?;
    if !digest(text(map, "proposal_hash", 64)?) || line(map, "proposal", 128)?.is_empty() {
        return bad("a review names the exact proposal and its hash");
    }
    small(map, "verified_usage", 8)?;
    small(map, "review", 4)?;
    match map.get("quality") {
        None => Ok(()),
        Some(Value::Array(q)) if q.len() <= 3 && q.iter().all(Value::is_object) => Ok(()),
        _ => bad("quality is at most three reviewed tiers"),
    }
}

/// Check the fields of one task entry, with the rules a peer applies to a row it receives.
pub fn check(map: &Map<String, Value>) -> Result<()> {
    if let Some(k) = map.keys().find(|k| !KEYS.contains(&k.as_str())) {
        return bad(&format!("unknown field {}", k.chars().filter(|c| !c.is_control()).take(40).collect::<String>()));
    }
    let op = line(map, "op", 16)?;
    if !OPS.contains(&op) {
        return bad("a task entry is a create, watch, assign, claim, checkpoint, submit, review, block, ready, enforce, scope, record or complete");
    }
    if matches!(op, "create" | "enforce") != line(map, "task", 128)?.is_empty() {
        return bad("every step names its task except the one that makes it and the one that sets the mode");
    }
    if map.get("at").is_some_and(|a| a.as_u64().is_none()) {
        return bad("at is a time in milliseconds");
    }
    line(map, "lease", 64)?;
    match op {
        "create" => {
            if line(map, "title", 200)?.trim().is_empty() || text(map, "description", 20_000)?.len() > 20_000 {
                return bad("a task has a title");
            }
            if lines(map, "acceptance", 32, 500, true)?.is_empty() {
                return bad("at least one acceptance criterion is required");
            }
            lines(map, "deps", 32, 128, true)?;
            lines(map, "capabilities", 32, 500, true)?;
            line(map, "source_key", 500)?;
            names(map, "assignees")?;
            names(map, "reviewers")?;
            limits(map)?;
        }
        "watch" => {
            flag(map, "on")?;
            if !valid_actor(line(map, "who", 96)?) {
                return bad("who is a session name");
            }
        }
        "assign" => {
            if !valid_name(line(map, "worker", 48)?) || line(map, "model", 256)?.is_empty() || line(map, "lease", 64)?.is_empty() {
                return bad("an assignment names a worker and the model lease it runs on");
            }
            lines(map, "capabilities", 32, 500, true)?;
        }
        "claim" if line(map, "assign", 128)?.is_empty() || line(map, "lease", 64)?.is_empty() => return bad("a claim names its assignment and its lease"),
        "checkpoint" | "ready" => {
            if text(map, "summary", 2000)?.trim().is_empty() && op == "checkpoint" {
                return bad("a checkpoint has a summary");
            }
            for key in ["progress", "commit", "environment", "transition"] {
                line(map, key, 2000)?;
            }
            flag(map, "expired")?;
            if op == "ready" && (line(map, "reason", 2000)?.trim().is_empty() || !map.contains_key("expired")) {
                return bad("recovery and resumption give a reason");
            }
        }
        "submit" => submission(map)?,
        "review" => review(map)?,
        "block" if !matches!(line(map, "kind", 16)?, "infrastructure" | "failure") || line(map, "reason", 2000)?.trim().is_empty() => {
            return bad("a blockage is an infrastructure or failure kind with a reason");
        }
        "enforce" if !matches!(line(map, "mode", 8)?, "open" | "strict") => return bad("the enforcement mode is open or strict"),
        "scope" => small(map, "scope", 16)?,
        "record" if line(map, "kind", 32)?.is_empty() || line(map, "rid", 200)?.is_empty() || !map.get("record").is_some_and(Value::is_object) => {
            return bad("a record names its kind, its id and carries the record");
        }
        "complete" => {
            line(map, "landed", 64)?;
        }
        _ => {}
    }
    Ok(())
}
