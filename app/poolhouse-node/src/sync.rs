//! Exchange between two nodes, in process: per board, per origin, with every check of `ingest`.
//!
//! This is the exchange logic a transport will carry; here the two sides are called directly.
//! A device only syncs boards both hold.

use std::collections::BTreeMap;

use crate::board::{Board, Tip};
use crate::error::{Error, Result};
use crate::node::Node;
use crate::row::{valid_origin, Row, MAX_ROW_BYTES};

pub const MAX_ROWS: usize = 400;
pub const MAX_ROUNDS: usize = 20;
pub const MAX_BATCH_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_REQUEST_ORIGINS: usize = 16;
pub const DIFFERENT_LOG: &str = "this device writes a different log than the one it presented before";

/// What one exchange moved, and what was refused (origin to error code).
#[derive(Debug, Default, PartialEq)]
pub struct Report {
    pub pulled: usize,
    pub pushed: usize,
    pub refused: BTreeMap<String, String>,
}

pub fn seqs(vector: &BTreeMap<String, Tip>) -> BTreeMap<String, u64> {
    vector.iter().map(|(o, t)| (o.clone(), t.seq)).collect()
}

/// For each origin the trusted rows past ``since``, starting one row early so the receiver
/// can compare it with its copy, within the row and byte limits of one request.
pub fn rows_since(board: &Board, since: &BTreeMap<String, u64>) -> BTreeMap<String, Vec<Row>> {
    let (mut out, mut count, mut bytes) = (BTreeMap::new(), 0, 0);
    for origin in board.origins() {
        let trusted = board.trusted(&origin);
        let (first, last) = (trusted.first().map_or(1, |r| r.seq), trusted.last().map_or(0, |r| r.seq));
        let have = since.get(&origin).copied().unwrap_or(0);
        if board.damaged().contains_key(&origin) || have >= last {
            continue;
        }
        let early = if have > 0 && have + 1 < first { board.archived_from(&origin, have, MAX_ROWS) } else { Vec::new() };
        let live = &trusted[usize::try_from(have.saturating_sub(first)).unwrap_or(usize::MAX).min(trusted.len())..];
        let reaches = early.last().is_some_and(|r| r.seq + 1 == first);
        let served: Vec<&Row> = match (early.is_empty(), reaches) {
            (true, _) => live.iter().collect(),
            (false, true) => early.iter().chain(trusted).collect(),
            (false, false) => early.iter().collect(),
        };
        for row in served {
            bytes += row.size();
            if count >= MAX_ROWS || (bytes > MAX_BATCH_BYTES && count > 0) {
                return out;
            }
            out.entry(origin.clone()).or_insert_with(Vec::new).push(row.clone());
            count += 1;
        }
    }
    out
}

/// Store what ``logs`` carries from the device ``from``; returns the row count.
pub fn take(board: &mut Board, logs: &BTreeMap<String, Vec<Row>>, from: &str, report: &mut Report) -> Result<usize> {
    let total: usize = logs.values().map(Vec::len).sum();
    if logs.len() > MAX_REQUEST_ORIGINS || total > 2 * MAX_ROWS {
        return Err(Error::Invalid("the request exceeds its bound".into()));
    }
    let mut stored = 0;
    for (origin, rows) in logs {
        if !valid_origin(origin) || rows.iter().any(|r| r.size() > MAX_ROW_BYTES) {
            return Err(Error::Invalid("a log is a list of small rows under an origin id".into()));
        }
        let owned = board.owns(from, origin);
        match board.ingest(origin, rows, owned) {
            Ok(n) => stored += n,
            Err(e @ (Error::Damaged(_) | Error::Gap(_) | Error::Quota(_) | Error::Invalid(_))) => {
                report.refused.insert(origin.clone(), e.code().into());
            }
            Err(e) => return Err(e),
        }
    }
    Ok(stored)
}

/// Store what ``logs`` carries from the device ``from`` that writes ``origin``. The device is tied to the origin only when a row
/// was stored, so a device that asked and moved nothing is not a device that holds a copy.
pub fn take_from(board: &mut Board, logs: &BTreeMap<String, Vec<Row>>, from: &str, origin: &str, report: &mut Report) -> Result<usize> {
    if !board.peer_agrees(from, origin) {
        return Err(Error::Denied(DIFFERENT_LOG.into()));
    }
    let fresh = !board.has_peer(from);
    if fresh {
        board.bind_peer(from, origin)?;
    }
    let stored = take(board, logs, from, report);
    if fresh && !matches!(stored, Ok(n) if n > 0) {
        board.release_peer(from)?;
    }
    stored
}

fn pull(local: &mut Board, remote: &mut Board, report: &mut Report) -> Result<()> {
    let (lf, rf) = (local.fingerprint(), remote.fingerprint());
    for _ in 0..MAX_ROUNDS {
        remote.acknowledge(&lf, &local.vector())?;
        remote.seal()?;
        let rows = rows_since(remote, &seqs(&local.vector()));
        local.acknowledge(&rf, &remote.trusted_vector())?;
        let stored = take(local, &rows, &rf, report)?;
        report.pulled += stored;
        if stored == 0 {
            break;
        }
    }
    Ok(())
}

/// Make ``local`` and ``remote`` (two copies of one board on two devices) hold the same rows.
pub fn exchange(local: &mut Board, remote: &mut Board) -> Result<Report> {
    if local.id != remote.id {
        return Err(Error::Denied("only copies of one board exchange".into()));
    }
    let (lf, rf) = (local.fingerprint(), remote.fingerprint());
    local.seal()?;
    remote.seal()?;
    if !local.bind_peer(&rf, remote.origin())? || !remote.bind_peer(&lf, local.origin())? {
        return Err(Error::Denied("this device writes a different log than the one it presented before".into()));
    }
    let mut report = Report::default();
    pull(local, remote, &mut report)?;
    let pulled = report.pulled;
    pull(remote, local, &mut report)?;
    (report.pulled, report.pushed) = (pulled, report.pulled - pulled);
    // One more look so each side records what the other now holds of its log.
    pull(local, remote, &mut report)?;
    pull(remote, local, &mut report)?;
    (report.pulled, report.pushed) = (pulled, report.pushed);
    Ok(report)
}

/// Exchange every board both nodes host; a board only one holds is left alone.
pub fn exchange_nodes(a: &mut Node, b: &mut Node) -> Result<BTreeMap<String, Report>> {
    let shared: Vec<String> = a.boards.keys().filter(|id| b.boards.contains_key(*id)).cloned().collect();
    let mut out = BTreeMap::new();
    for id in shared {
        let (x, y) = (a.boards.get_mut(&id), b.boards.get_mut(&id));
        if let (Some(x), Some(y)) = (x, y) {
            out.insert(id, exchange(&mut x.board, &mut y.board)?);
        }
    }
    Ok(out)
}
