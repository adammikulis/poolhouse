//! The grant entries of one board, kept as they arrive, and what they add up to now.
//!
//! An entry counts only when it was written by this device or by a trusted one (the landing trust list), the key
//! that signed it is enrolled on this node, and, from another device, the action may travel. A revocation beats
//! the grant it names whatever the order; a use counts toward the grant's limit.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Map, Value};

use crate::board::Board;
use crate::error::Result;
use crate::node::Node;
use crate::row::{Kind, Row};
use crate::rules::held_back;

use super::person::Persons;
use super::spec::{parse_issue, parse_revoke, signing_bytes, Issue, PORTABLE};

/// How many entries of devices this one does not trust are read for the listing; the rest are not looked at.
const MOST_UNTRUSTED: usize = 200;

/// Where the issue and revoke rows of every origin of a board sit in its log, and how often each origin wrote a use of
/// each grant, picked out of the logs once and extended as they grow.
#[derive(Default)]
pub struct Book {
    rows: BTreeMap<String, Vec<usize>>,
    used: BTreeMap<String, BTreeMap<String, u64>>,
    seen: BTreeMap<String, (usize, String)>,
}

impl Book {
    /// Read the rows of ``board`` written since the last call; an origin whose log was replaced is read again.
    pub fn refresh(&mut self, board: &Board) {
        let origins = board.origins();
        self.rows.retain(|o, _| origins.contains(o));
        self.used.retain(|o, _| origins.contains(o));
        self.seen.retain(|o, _| origins.contains(o));
        for origin in origins {
            let log = board.rows(&origin);
            let changed = self.seen.get(&origin).is_some_and(|(n, h)| *n > log.len() || (*n > 0 && log[*n - 1].hash != *h));
            if changed {
                self.rows.remove(&origin);
                self.used.remove(&origin);
                self.seen.remove(&origin);
            }
            let from = self.seen.get(&origin).map_or(0, |s| s.0);
            for (at, row) in log.iter().enumerate().skip(from).filter(|(_, r)| r.kind == Kind::Grant) {
                let body = row.body.as_object();
                if body.and_then(|b| b.get("op")).and_then(Value::as_str) == Some("use") {
                    let gid = body.and_then(|b| b.get("gid")).and_then(Value::as_str).unwrap_or("").to_string();
                    *self.used.entry(origin.clone()).or_default().entry(gid).or_default() += 1;
                } else {
                    self.rows.entry(origin.clone()).or_default().push(at);
                }
            }
            if let Some(last) = log.last() {
                self.seen.insert(origin, (log.len(), last.hash.clone()));
            }
        }
    }

    /// Every issue and revoke row in the board's total order, leaving out a foreign row dated too far ahead and every
    /// entry of a device ``counted`` rejects past the first few.
    pub fn ordered(&self, board: &Board, now_ms: u64, counted: &dyn Fn(&str) -> bool) -> Vec<Row> {
        let mut all: Vec<&Row> = self.rows.iter().flat_map(|(origin, at)| {
            let log = board.rows(origin);
            at.iter().filter_map(move |i| log.get(*i))
        }).filter(|r| r.origin == board.origin() || !held_back(r, now_ms)).collect();
        all.sort_by(|a, b| a.order().cmp(&b.order()));
        let mut unlisted = 0;
        all.into_iter().filter(|r| counted(&r.origin) || {
            unlisted += 1;
            unlisted <= MOST_UNTRUSTED
        }).cloned().collect()
    }

    /// Whether another device wrote any entry of this board's grants.
    pub fn foreign(&self, own: &str) -> bool {
        self.rows.keys().chain(self.used.keys()).any(|o| o != own)
    }

    /// How often ``gid`` was used, counting only the origins ``counted`` accepts.
    pub fn uses(&self, gid: &str, counted: &dyn Fn(&str) -> bool) -> u64 {
        self.used.iter().filter(|(origin, _)| counted(origin)).filter_map(|(_, by)| by.get(gid)).sum()
    }
}

/// One grant as the board stands: what was signed, how much of it was used and whether it holds.
pub struct Grant {
    pub issue: Issue,
    /// The entry that carries it.
    pub row: Row,
    pub used: u64,
    /// `live`, `not-yet`, `expired`, `exhausted`, `revoked` or `ignored`.
    pub status: &'static str,
    pub why: String,
}

/// Every grant of a board, and the entries that name none that count.
pub struct Standing {
    pub grants: Vec<Grant>,
    pub ignored: Vec<(String, String)>,
}

fn refusal(persons: &Persons, own: &str, row: &Row, issue: &Issue, map: &Map<String, Value>, counted: bool) -> Option<&'static str> {
    if !counted {
        return Some("written by a device this one does not trust");
    }
    if row.origin != own && !PORTABLE.contains(&issue.action.as_str()) {
        return Some("this action is granted on the device it applies to");
    }
    let sig = map.get("sig").and_then(Value::as_str).unwrap_or("");
    if !persons.signed(&issue.person, &signing_bytes(map), sig) {
        return Some("not signed by a person key enrolled on this device");
    }
    None
}

fn status(issue: &Issue, used: u64, now_ms: u64) -> &'static str {
    if issue.uses.is_some_and(|n| used >= n) {
        "exhausted"
    } else if now_ms >= issue.until_ms {
        "expired"
    } else if now_ms < issue.at_ms {
        "not-yet"
    } else {
        "live"
    }
}

/// What the grant entries of ``board`` come to at this moment.
pub fn standing(node: &mut Node, board: &str) -> Result<Standing> {
    let now = node.now_ms();
    let (own, foreign) = {
        let hosted = node.host(board)?;
        hosted.book.refresh(&hosted.board);
        let own = hosted.board.origin().to_string();
        let foreign = hosted.book.foreign(&own);
        (own, foreign)
    };
    let trusted: BTreeSet<String> = if foreign { crate::landtrust::writers(node, board)?.trusted } else { BTreeSet::new() };
    let counted = |origin: &str| origin == own || trusted.contains(origin);
    let rows = {
        let hosted = node.host(board)?;
        hosted.book.ordered(&hosted.board, now, &counted)
    };
    let mut revoked: BTreeMap<String, String> = BTreeMap::new();
    let mut found: Vec<(Row, Issue, Option<&'static str>)> = Vec::new();
    let mut ignored = Vec::new();
    for row in &rows {
        let Some(map) = row.body.as_object() else { continue };
        match map.get("op").and_then(Value::as_str) {
            Some("issue") => match parse_issue(map) {
                Ok(issue) if found.iter().any(|(_, i, why)| why.is_none() && i.gid == issue.gid) => ignored.push((row.id(), "that grant id was used before".to_string())),
                Ok(issue) => {
                    let why = refusal(&node.persons, &own, row, &issue, map, counted(&row.origin));
                    found.push((row.clone(), issue, why));
                }
                Err(e) => ignored.push((row.id(), e.to_string())),
            },
            Some("revoke") if counted(&row.origin) => {
                if let Ok(r) = parse_revoke(map) {
                    if node.persons.signed(&r.person, &signing_bytes(map), map.get("sig").and_then(Value::as_str).unwrap_or("")) {
                        revoked.entry(r.gid).or_insert(r.reason);
                    }
                }
            }
            _ => {}
        }
    }
    let book = &node.host(board)?.book;
    let spent: Vec<u64> = found.iter().map(|(_, issue, _)| book.uses(&issue.gid, &counted)).collect();
    let grants = found.into_iter().zip(spent).map(|((row, issue, why), n)| {
        let gone = revoked.get(&issue.gid);
        let (state, why) = match (why, gone) {
            (_, Some(reason)) => ("revoked", reason.clone()),
            (Some(why), None) => ("ignored", why.to_string()),
            (None, None) => (status(&issue, n, now), String::new()),
        };
        Grant { row, used: n, status: state, why, issue }
    }).collect();
    Ok(Standing { grants, ignored })
}
