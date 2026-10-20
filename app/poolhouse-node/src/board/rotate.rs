//! New segments of a log: a device starts one on its own log at a share of the cap, a copy of another device's log applies one once it
//! holds the base and every restated row, and the rows before a base are kept in an archive for the members that have not yet reached it.

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::json;

use super::{Board, HEAD_EVERY_BYTES, HEAD_EVERY_ROWS};
use crate::error::{Error, Result};
use crate::log::carried;
use crate::logbounds::{Held, ARCHIVE_MAX_MS, ARCHIVE_MIN_MS, MIN_SEGMENT_PERCENT, MOST_ARCHIVES};
use crate::row::{canonical, Kind, Row};
use crate::rules::Accepted;

const DAY_MS: u64 = 24 * 3600 * 1000;

fn size_of(rows: &[&Row]) -> Held {
    Held { rows: rows.len(), bytes: rows.iter().map(|r| r.size() as u64 + 1).sum() }
}

/// The rows of ``rows`` of kind grant, each as the text that identifies it: its actor, its key and its body.
fn grants(rows: &[&Row]) -> BTreeSet<String> {
    rows.iter().filter(|r| r.kind == Kind::Grant).map(|r| format!("{}|{}|{}", r.actor, r.idem, canonical(&r.body))).collect()
}

impl Board {
    /// The folder the rows before a base are kept in.
    pub(super) fn archive_dir(&self) -> PathBuf {
        self.dir.join("archive")
    }

    /// The archived segments of ``origin``: first seq, last seq and file, oldest first.
    fn archives(&self, origin: &str) -> Vec<(u64, u64, PathBuf)> {
        let Ok(entries) = std::fs::read_dir(self.archive_dir()) else { return Vec::new() };
        let mut found: Vec<(u64, u64, PathBuf)> = entries.flatten().filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            let range = name.strip_prefix(origin)?.strip_prefix('.')?.strip_suffix(".jsonl")?;
            let (first, last) = range.split_once('-')?;
            Some((first.parse().ok()?, last.parse().ok()?, e.path()))
        }).collect();
        found.sort();
        found
    }

    /// Up to ``limit`` rows of ``origin`` from seq ``seq`` on, from the archived segments that hold them one after another; none when no
    /// archive holds ``seq``.
    pub fn archived_from(&self, origin: &str, seq: u64, limit: usize) -> Vec<Row> {
        let mut out: Vec<Row> = Vec::new();
        for (first, last, path) in self.archives(origin).into_iter().filter(|(_, last, _)| *last >= seq) {
            let next = out.last().map_or(seq, |r| r.seq + 1);
            if first > next || out.len() >= limit {
                break;
            }
            let Ok(bytes) = std::fs::read(path) else { break };
            let rows = bytes.split(|b| *b == b'\n').filter(|l| !l.is_empty()).filter_map(|l| serde_json::from_slice::<Row>(l).ok());
            out.extend(rows.filter(|r| r.seq >= next && r.seq <= last).take(limit - out.len()));
        }
        out
    }

    /// The hash of row ``seq`` of ``origin``, from the live segment, the base that follows it or an archive.
    pub(super) fn hash_at(&self, origin: &str, seq: u64) -> Option<String> {
        let log = self.logs.get(origin)?;
        if let Some(row) = log.at(seq) {
            return Some(row.hash.clone());
        }
        match log.rows().first() {
            Some(base) if base.kind == Kind::Base && base.seq == seq + 1 => Some(base.prev.clone()),
            _ => self.archived_from(origin, seq, 1).first().filter(|r| r.seq == seq).map(|r| r.hash.clone()),
        }
    }

    /// Whether this device's log is due a new segment.
    pub fn rotate_due(&self) -> bool {
        self.rotate_percent > 0 && self.logs.get(&self.origin).is_some_and(|l| self.bounds.segment_at(l.segment_held(), self.rotate_percent))
    }

    /// Start a new segment of this device's log if it is due. True when one was started.
    pub fn rotate_if_due(&mut self) -> Result<bool> {
        if !self.rotate_due() {
            return Ok(false);
        }
        self.rotate()?;
        Ok(true)
    }

    /// Start a new segment of this device's log: a base row, this device's durable rows restated, and a head, in place of the rows held,
    /// which go to the archive. The log is replaced in one rename, so a crash leaves the old log or the new one.
    pub fn rotate(&mut self) -> Result<()> {
        let origin = self.origin.clone();
        let old = self.rows(&origin).to_vec();
        let Some(last) = old.last().cloned() else { return Ok(()) };
        let (kept, left) = self.restated(&old)?;
        let tail = self.tail(&last, &kept, left);
        let archive = self.archive_dir();
        self.log_mut(&origin)?.replace(usize::MAX, tail, &archive)?;
        self.recount();
        self.prune_archives()
    }

    /// The rows of ``old`` the new segment restates and how many durable rows did not fit.
    fn restated(&self, old: &[Row]) -> Result<(Vec<Row>, u64)> {
        let now = self.now_ms();
        let superseded: BTreeSet<&str> = self.logs.values().flat_map(|l| l.view()).filter(|r| r.kind == Kind::Note)
            .flat_map(|r| r.body.get("supersedes").and_then(|v| v.as_array()).into_iter().flatten().filter_map(|v| v.as_str())).collect();
        let live = |r: &Row| match r.kind {
            Kind::Grant | Kind::Room | Kind::Task => true,
            Kind::Note => {
                let ttl = r.body.get("ttl_days").and_then(|v| v.as_u64()).unwrap_or(0);
                !superseded.contains(r.id().as_str()) && (ttl == 0 || now.saturating_sub(r.hlc.0) <= ttl * DAY_MS)
            }
            _ => false,
        };
        let durable: Vec<usize> = (0..old.len()).filter(|i| live(&old[*i])).collect();
        let grants = durable.iter().filter(|i| old[**i].kind == Kind::Grant).count();
        let used: Held = durable.iter().filter(|i| old[**i].kind == Kind::Grant).fold(Held::default(), |mut h, i| { h.add(old[*i].size() as u64 + 1); h });
        let carry = self.bounds.carry();
        let (mut rows, mut bytes, mut chosen) = (used.rows, used.bytes, BTreeSet::new());
        for i in durable.iter().rev().filter(|i| old[**i].kind != Kind::Grant) {
            let size = old[*i].size() as u64 + 1;
            if rows + 1 > carry.rows || bytes + size > carry.bytes {
                break;
            }
            (rows, bytes) = (rows + 1, bytes + size);
            chosen.insert(*i);
        }
        let kept: Vec<Row> = durable.iter().filter(|i| old[**i].kind == Kind::Grant || chosen.contains(*i)).map(|i| old[*i].clone()).collect();
        if kept.len() > self.bounds.rows / 2 || bytes > self.bounds.bytes / 2 {
            return Err(crate::limits::refuse("the grants of this log are more than a new segment may restate", self.bounds.rows / 2, "CARRY_PERCENT", "logbounds.rs"));
        }
        let left = (durable.len() - grants - chosen.len()) as u64;
        Ok((kept, left))
    }

    /// The rows of a new segment after ``last``: the base, ``kept`` restated with a head as often as the log signs, and a last head.
    fn tail(&self, last: &Row, kept: &[Row], left: u64) -> Vec<Row> {
        let mut out = vec![self.next_row(Some(last), (Kind::Base, "", ""), json!({"carried": kept.len(), "left": left}))];
        let (mut rows, mut bytes) = (1, out[0].size());
        for r in kept {
            let row = self.next_row(out.last(), (r.kind, &r.actor, &r.idem), r.body.clone());
            (rows, bytes) = (rows + 1, bytes + row.size());
            out.push(row);
            if rows >= HEAD_EVERY_ROWS || bytes >= HEAD_EVERY_BYTES {
                out.push(self.head_after(&out));
                (rows, bytes) = (0, 0);
            }
        }
        if out.last().is_some_and(|r| r.kind != Kind::Head) {
            out.push(self.head_after(&out));
        }
        out
    }

    /// A head that signs the last of ``rows``.
    fn head_after(&self, rows: &[Row]) -> Row {
        let tip = rows.last().expect("a head follows a row");
        self.next_row(Some(tip), (Kind::Head, "", ""), self.head_body(tip))
    }

    /// Count the rows of this device's live segment toward their authors and groups again.
    fn recount(&mut self) {
        self.authors.clear();
        self.lineages.clear();
        let rows: Vec<(String, u64)> = self.rows(&self.origin).iter().filter(|r| !matches!(r.kind, Kind::Head | Kind::Base)).map(|r| (r.actor.clone(), r.size() as u64 + 1)).collect();
        for (actor, bytes) in rows {
            self.authors.entry(actor.clone()).or_default().add(bytes);
            self.lineages.entry(self.group_of(&actor)).or_default().add(bytes);
        }
    }

    /// The rows and bytes of ``origin``'s live segment and of ``took`` that a copy of it would hold once the rows before a base are archived.
    pub(super) fn segment_sizes(&self, origin: &str, took: &Accepted) -> (Held, Held) {
        let held = self.logs.get(origin).map_or_else(Held::default, |l| l.segment_held());
        let size = |rows: &[Row]| size_of(&rows.iter().collect::<Vec<_>>());
        match took.rows.iter().rposition(|r| r.kind == Kind::Base) {
            Some(at) => (Held::default(), size(&took.rows[at..])),
            None if took.restart => (Held::default(), size(&took.rows)),
            None => (held, size(&took.rows)),
        }
    }

    /// Refuse the bases among ``took``: one that restates more than a segment may, one that ends a segment holding under
    /// `MIN_SEGMENT_PERCENT` of what a log keeps, and one whose restated rows leave out a grant row of the rows ``held``.
    pub(super) fn check_bases(&self, held: &[Row], took: &Accepted) -> Result<()> {
        let all: Vec<&Row> = held.iter().chain(&took.rows).collect();
        let damaged = |why: String| Err(Error::Damaged(why));
        let mut start = 0;
        for (at, row) in all.iter().enumerate().filter(|(_, r)| r.kind == Kind::Base) {
            if at >= held.len() {
                if carried(row) > self.bounds.rows as u64 / 2 {
                    return damaged(format!("base at row {} restates more rows than a segment may", row.seq));
                }
                if !took.restart && at > 0 && !self.bounds.segment_at(size_of(&all[start..at]), MIN_SEGMENT_PERCENT) {
                    return damaged(format!("base at row {} ends a segment that holds under {MIN_SEGMENT_PERCENT} percent of what a log keeps", row.seq));
                }
            }
            start = at;
        }
        let Some(at) = all.iter().rposition(|r| r.kind == Kind::Base).filter(|at| *at > 0) else { return Ok(()) };
        let seen = all[at + 1..].iter().filter(|r| !matches!(r.kind, Kind::Head | Kind::Base)).count() as u64;
        if seen >= carried(all[at]) && !grants(&all[..at]).is_subset(&grants(&all[at + 1..])) {
            return damaged(format!("base at row {} leaves out a grant row that was held", all[at].seq));
        }
        Ok(())
    }

    /// Replace the log of ``origin`` by its newest segment once the base, its restated rows and a head are held.
    pub(super) fn apply_segment(&mut self, origin: &str) -> Result<()> {
        let archive = self.archive_dir();
        let Some(log) = self.logs.get_mut(origin) else { return Ok(()) };
        let Some(at) = log.complete() else { return Ok(()) };
        log.replace(at, Vec::new(), &archive)?;
        self.prune_archives()
    }

    /// Remove the archives that are no longer needed: older than `ARCHIVE_MAX_MS`; older than `ARCHIVE_MIN_MS` with every device that
    /// synced this board past them (own log) or whatever the age (a copy); and all but the newest `MOST_ARCHIVES` of an origin.
    pub(super) fn prune_archives(&mut self) -> Result<()> {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_millis() as u64);
        let mut by_origin: BTreeMap<String, Vec<(u64, u64, PathBuf)>> = BTreeMap::new();
        for origin in self.archive_origins() {
            by_origin.insert(origin.clone(), self.archives(&origin));
        }
        for (origin, files) in by_origin {
            let excess = files.len().saturating_sub(MOST_ARCHIVES);
            for (n, (_, last, path)) in files.into_iter().enumerate() {
                let modified = std::fs::metadata(&path).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map_or(now, |d| d.as_millis() as u64);
                let age = now.saturating_sub(modified);
                let acked = origin != self.origin || self.state.t.peers.keys().all(|f| self.state.t.acks.get(f).copied().unwrap_or(0) > last);
                if n < excess || age >= ARCHIVE_MAX_MS || (age >= ARCHIVE_MIN_MS && acked) {
                    std::fs::remove_file(&path)?;
                }
            }
        }
        Ok(())
    }

    /// The origins that have a file in the archive folder.
    fn archive_origins(&self) -> BTreeSet<String> {
        let Ok(entries) = std::fs::read_dir(self.archive_dir()) else { return BTreeSet::new() };
        entries.flatten().filter_map(|e| e.file_name().to_string_lossy().split('.').next().map(str::to_string)).filter(|o| crate::row::valid_origin(o)).collect()
    }
}
