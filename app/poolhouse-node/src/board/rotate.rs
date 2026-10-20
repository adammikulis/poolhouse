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
use crate::row::{Kind, Row, MAX_ROW_BYTES};
use crate::rules::Accepted;

const DAY_MS: u64 = 24 * 3600 * 1000;

/// The `op` of a grant row.
fn op(row: &Row) -> &str {
    row.body.get("op").and_then(|v| v.as_str()).unwrap_or("")
}

fn size_of(rows: &[&Row]) -> Held {
    Held { rows: rows.len(), bytes: rows.iter().map(|r| r.size() as u64 + 1).sum() }
}

/// Count revocations by exact original logical operation ID and hash.
fn revokes(rows: &[&Row]) -> Result<BTreeMap<String, usize>> {
    let mut found = BTreeMap::new();
    let operations = rows.iter().map(|r| r.operation()).collect::<Result<Vec<_>>>()?;
    for r in operations.iter().filter(|r| r.kind == Kind::Grant && op(r) == "revoke") {
        *found.entry(format!("{}|{}", r.id(), r.hash)).or_default() += 1;
    }
    Ok(found)
}

/// Whether every revocation of ``old`` is among ``new`` as often as it was.
fn revokes_kept(old: &[&Row], new: &[&Row]) -> Result<()> {
    let now = revokes(new)?;
    match revokes(old)?.into_iter().find(|(key, n)| now.get(key).copied().unwrap_or(0) < *n) {
        Some(_) => Err(Error::Damaged("a new segment leaves out a grant revocation that was held".into())),
        None => Ok(()),
    }
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
        let tail = self.tail(&last, &kept, left)?;
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
        let grant_rows = self.logs.values().flat_map(|l| l.view()).filter(|r| r.kind == Kind::Grant);
        let gid = |r: &Row| r.body.get("gid").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let limited: BTreeSet<String> = grant_rows.clone().filter(|r| op(r) == "issue" && r.body.get("uses").is_some()).map(gid).collect();
        let revoked: BTreeSet<String> = grant_rows.filter(|r| op(r) == "revoke").map(gid).collect();
        let live = |r: &Row| match r.kind {
            Kind::Grant if op(r) == "use" => limited.contains(&gid(r)) && !revoked.contains(&gid(r)),
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
    fn tail(&self, last: &Row, kept: &[Row], left: u64) -> Result<Vec<Row>> {
        let mut out = vec![self.next_row(Some(last), (Kind::Base, "", ""), json!({"carried": kept.len(), "left": left}))];
        let (mut rows, mut bytes) = (1, out[0].size());
        for r in kept {
            let row = self.next_row(out.last(), (Kind::Carry, &r.actor, &r.idem), json!({"original": r}));
            if row.size() > MAX_ROW_BYTES {
                return Err(Error::Invalid("an original operation cannot fit its carry envelope".into()));
            }
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
        let actual = size_of(&out.iter().collect::<Vec<_>>());
        if actual.rows > self.bounds.rows / 2 || actual.bytes > self.bounds.bytes / 2 {
            return Err(Error::Invalid("the serialized carry segment exceeds its transport bound".into()));
        }
        Ok(out)
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
        let rows: Vec<(String, u64)> = self.chain(&self.origin).iter().filter(|r| !matches!(r.kind, Kind::Head | Kind::Base)).map(|r| (r.actor.clone(), r.size() as u64 + 1)).collect();
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
            None => (self.staged.get(origin).map_or(held, |s| size_of(&s.iter().collect::<Vec<_>>())), size(&took.rows)),
        }
    }

    /// Refuse the bases among ``took``: one that restates more than a segment may, one that ends a segment holding under
    /// `MIN_SEGMENT_PERCENT` of what a log keeps, and one whose restated rows leave out a grant revocation of the rows ``held``.
    pub(super) fn check_bases(&self, held: &[Row], took: &Accepted) -> Result<()> {
        let combined: Vec<Row> = held.iter().chain(&took.rows).cloned().collect();
        crate::rules::check_carries(&combined, false)?;
        let all: Vec<&Row> = combined.iter().collect();
        let damaged = |why: String| Err(Error::Damaged(why));
        let (mut start, newest) = (0, all.iter().rposition(|r| r.kind == Kind::Base));
        for (at, row) in all.iter().enumerate().filter(|(_, r)| r.kind == Kind::Base) {
            if at >= held.len() || (newest == Some(at) && at > 0) {
                let restated = all[at + 1..].iter().filter(|r| r.kind != Kind::Head).take(carried(row) as usize);
                if restated.into_iter().any(|r| r.kind != Kind::Carry) {
                    return damaged(format!("base at row {} is followed by rows that are not the durable rows it says it restates", row.seq));
                }
            }
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
        if seen >= carried(all[at]) {
            revokes_kept(&all[..at], &all[at + 1..])?;
        }
        Ok(())
    }

    /// Hold ``rows`` of a segment of ``origin`` that restarts the copy held (``held`` if this device holds rows of it) until the base, every
    /// restated row and a head are here, then check that no revocation of the rows held is left out and replace the log.
    pub(super) fn hold_segment(&mut self, origin: &str, (restart, held): (bool, &[Row]), rows: Vec<Row>) -> Result<()> {
        let mut next = if restart { Vec::new() } else { self.staged.remove(origin).unwrap_or_default() };
        next.extend(rows);
        let seen = next.iter().skip(1).filter(|r| !matches!(r.kind, Kind::Head | Kind::Base)).count() as u64;
        if !next.first().is_some_and(|b| b.kind == Kind::Base && seen >= carried(b))
            || !next.last().is_some_and(|row| row.kind == Kind::Head) {
            self.staged.insert(origin.into(), next);
            return Ok(());
        }
        let complete: Vec<Row> = held.iter().chain(&next).cloned().collect();
        crate::rules::check_carries(&complete, true)?;
        self.staged.remove(origin);
        revokes_kept(&held.iter().collect::<Vec<_>>(), &next.iter().collect::<Vec<_>>())?;
        let archive = self.archive_dir();
        self.log_mut(origin)?.replace(usize::MAX, next, &archive)?;
        self.prune_archives()
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
    pub fn prune_archives(&mut self) -> Result<()> {
        let now = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_millis() as u64);
        let mut by_origin: BTreeMap<String, Vec<(u64, u64, PathBuf)>> = BTreeMap::new();
        for origin in self.archive_origins() {
            by_origin.insert(origin.clone(), self.archives(&origin));
        }
        for (origin, files) in by_origin {
            let excess = files.len().saturating_sub(MOST_ARCHIVES);
            let mut room = self.bounds.bytes;
            let mut too_big = BTreeSet::new();
            for (_, _, path) in files.iter().rev() {
                let size = std::fs::metadata(path).map_or(0, |m| m.len());
                if size > room {
                    too_big.insert(path.clone());
                }
                room = room.saturating_sub(size);
            }
            for (n, (_, last, path)) in files.into_iter().enumerate() {
                let modified = std::fs::metadata(&path).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map_or(now, |d| d.as_millis() as u64);
                let age = now.saturating_sub(modified);
                let acked = origin != self.origin || self.state.t.peers.keys().all(|f| self.state.t.acks.get(f).copied().unwrap_or(0) > last);
                if n < excess || too_big.contains(&path) || age >= ARCHIVE_MAX_MS || (age >= ARCHIVE_MIN_MS && acked) {
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
