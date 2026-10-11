//! The `acts` member op: how members swap the acts of their pool.
//!
//! A request names the pool, up to 4,096 ids of the acts the asker holds (`have`), the page it wants and up to 256 acts it
//! pushes. The reply is the next page of the acts the asker lacks (at most 256 acts and 2 MiB, so a page fits the frame), the
//! ids the answerer holds, and what became of what was pushed. Every act on either side is checked by the receiver from the keys
//! it already holds; the member that carried it proves nothing about it.

use std::collections::BTreeSet;
use std::sync::{Mutex, MutexGuard};

use serde_json::{json, Value};

use crate::error::{Error, Result};
use crate::node::Node;
use crate::peer::PeerClient;

use super::act::Act;
use super::pool::Ingest;
use super::{HAVE_ACTS, MAX_PAGES, PAGE_ACTS};

const MOST_ANSWERS: usize = 16;

fn locked(node: &Mutex<Node>) -> Result<MutexGuard<'_, Node>> {
    node.lock().map_err(|_| Error::Damaged("node poisoned".into()))
}

/// Whether ``text`` is a token of an act held: its id and the tag of its signers, 80 hex digits.
fn valid_token(text: &str) -> bool {
    text.len() == 80 && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn have_of(req: &Value) -> Result<BTreeSet<String>> {
    let list = match req.get("have") {
        None => return Ok(BTreeSet::new()),
        Some(Value::Array(list)) => list,
        Some(_) => return Err(Error::Invalid("have is a list of act ids".into())),
    };
    if list.len() > HAVE_ACTS || !list.iter().all(|v| v.as_str().is_some_and(valid_token)) {
        return Err(Error::Invalid("have names at most 4096 acts by their tokens".into()));
    }
    Ok(list.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
}

/// What a request pushed, read: at most 256 acts; one that is not an act is counted as refused.
fn pushed(req: &Value) -> Result<(Vec<Act>, usize)> {
    let list = match req.get("push") {
        None => return Ok((Vec::new(), 0)),
        Some(Value::Array(list)) if list.len() <= PAGE_ACTS => list,
        Some(_) => return Err(Error::Invalid("push is a list of at most 256 acts".into())),
    };
    let acts: Vec<Act> = list.iter().filter_map(|v| serde_json::from_value(v.clone()).ok()).collect();
    let unread = list.len() - acts.len();
    Ok((acts, unread))
}

/// Answer an `acts` request from a member (the caller has checked that it is one).
pub fn serve(node: &mut Node, req: &Value) -> Result<Value> {
    let Some(gov) = node.members.gov() else { return Err(Error::Denied("this pool is not governed by acts".into())) };
    if req.get("pool").and_then(Value::as_str) != Some(gov.id.as_str()) {
        return Err(Error::Denied("this is not the same pool".into()));
    }
    let have = have_of(req)?;
    let page = usize::try_from(req.get("page").and_then(Value::as_u64).unwrap_or(0)).unwrap_or(usize::MAX);
    if page >= MAX_PAGES {
        return Err(Error::Invalid("there are not that many pages".into()));
    }
    let (acts, unread) = pushed(req)?;
    let taken = node.gov_ingest_many(acts, false)?;
    let refused: Vec<Value> = taken.iter().filter_map(|(id, i)| if let Ingest::Refused(why) = i { Some(json!({"id": id, "why": why})) } else { None }).take(MOST_ANSWERS).collect();
    let accepted = taken.iter().filter(|(_, i)| matches!(i, Ingest::Accepted | Ingest::Merged)).count();
    let gov = node.members.gov().ok_or_else(|| Error::Denied("this pool is not governed by acts".into()))?;
    let (out, more) = if req.get("have").is_none() && req.get("page").is_none() { (Vec::new(), false) } else { gov.page(&have, page) };
    Ok(json!({"pool": gov.id, "acts": out, "more": more, "have": gov.have(), "accepted": accepted, "refused": refused, "unread": unread}))
}

/// Swap acts with the member ``c``: read every page of what it holds that this device lacks, then push what it lacks. How many acts
/// this device took.
pub fn sync_acts(node: &Mutex<Node>, c: &mut PeerClient) -> Result<usize> {
    let (pool, have) = {
        let n = locked(node)?;
        match n.members.gov() {
            Some(g) => (g.id.clone(), g.have()),
            None => return Ok(0),
        }
    };
    let (mut took, mut theirs) = (0, BTreeSet::new());
    for page in 0..MAX_PAGES {
        let reply = c.call(&json!({"op": "acts", "pool": pool, "have": have, "page": page}))?;
        let acts: Vec<Act> = serde_json::from_value(reply["acts"].clone()).map_err(|_| Error::Invalid("acts is a list of acts".into()))?;
        took += locked(node)?.gov_ingest_many(acts, false)?.iter().filter(|(_, i)| matches!(i, Ingest::Accepted | Ingest::Merged)).count();
        theirs = have_of(&reply)?;
        if reply["more"] != true {
            break;
        }
    }
    for page in 0..MAX_PAGES {
        let (out, more) = {
            let n = locked(node)?;
            n.members.gov().map(|g| g.page(&theirs, page)).unwrap_or_default()
        };
        if !out.is_empty() {
            c.call(&json!({"op": "acts", "pool": pool, "push": out}))?;
        }
        if !more {
            break;
        }
    }
    Ok(took)
}
