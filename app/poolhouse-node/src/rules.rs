//! Pure rules for per-origin logs: the clock, chain and head checks, and the merge.

use std::collections::BTreeSet;

use ed25519_dalek::{Signature, VerifyingKey};

use crate::error::{Error, Result};
use crate::fsutil::unhex;
use crate::row::{check_body, valid_name, valid_origin, Kind, Row, GENESIS, MAX_ROW_BYTES, MAX_SEQ, VERSION};

/// How far ahead of the local clock a foreign row may be before it is held back.
pub const SKEW_MAX_MS: u64 = 300_000;

/// The rows to store for an origin and the key its heads verified under.
pub struct Accepted {
    pub rows: Vec<Row>,
    pub public: Option<[u8; 32]>,
    /// The rows start a new segment at a base: the copy held is set aside and the rows are stored in its place.
    pub restart: bool,
}

/// The next clock value after ``previous``, the wall clock and ``seen``.
pub fn tick(previous: (u64, u64), now_ms: u64, seen: (u64, u64)) -> (u64, u64) {
    let wall = now_ms.max(previous.0).max(seen.0);
    let from = |pair: (u64, u64)| if pair.0 == wall { Some(pair.1) } else { None };
    let counter = from(previous).into_iter().chain(from(seen)).max().map_or(0, |c| c + 1);
    (wall, counter)
}

/// Whether the row's wall clock is further ahead of ``now_ms`` than the allowed skew.
pub fn held_back(row: &Row, now_ms: u64) -> bool {
    row.hlc.0 > now_ms.saturating_add(SKEW_MAX_MS)
}

/// The bytes a head signs.
pub fn head_message(pool: &str, board: &str, origin: &str, seq: u64, digest: &str) -> Vec<u8> {
    format!("poolhouse-board-head-v1|{pool}|{board}|{origin}|{seq}|{digest}").into_bytes()
}

/// Every log's rows in one total order: heads and bases dropped, a row delivered twice kept once, and a
/// repeated idempotency key of one actor on one origin kept only at its first row.
pub fn merge(logs: &[&[Row]]) -> Vec<Row> {
    let mut rows: Vec<&Row> = logs.iter().flat_map(|l| l.iter()).filter(|r| !matches!(r.kind, Kind::Head | Kind::Base)).collect();
    rows.sort_by(|a, b| a.order().cmp(&b.order()));
    let mut ids = BTreeSet::new();
    let mut keys = BTreeSet::new();
    let mut kept = Vec::new();
    for row in rows {
        if !ids.insert((row.origin.clone(), row.seq)) {
            continue;
        }
        if !row.idem.is_empty() && !keys.insert((row.origin.clone(), row.actor.clone(), row.idem.clone())) {
            continue;
        }
        kept.push(row.clone());
    }
    kept
}

fn shape(row: &Row, board: &str, origin: &str) -> Result<()> {
    if row.v != VERSION || row.board != board || row.origin != origin {
        return Err(Error::Damaged("row does not belong to this log".into()));
    }
    if row.hlc.2 != origin {
        return Err(Error::Damaged("row carries no clock value".into()));
    }
    if check_body(&row.body).is_err() {
        return Err(Error::Damaged("row body is not allowed".into()));
    }
    if row.seq > MAX_SEQ {
        return Err(Error::Damaged(format!("row {} is past the highest sequence number", row.seq)));
    }
    if row.kind == Kind::Base {
        let counts = ["carried", "left"].iter().all(|k| row.body.get(*k).is_some_and(|v| v.is_u64()));
        let hash = row.prev.len() == 64 && row.prev.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
        if !counts || !hash || row.seq < 2 {
            return Err(Error::Damaged(format!("base at row {} is malformed", row.seq)));
        }
    }
    Ok(())
}

/// Check ``rows`` follow one another (and ``tip``) by sequence, hash and clock. With no ``tip`` the first row is seq 1 or a base.
pub fn check_chain(rows: &[Row], board: &str, origin: &str, tip: Option<&Row>) -> Result<()> {
    let from_base = rows.first().filter(|r| tip.is_none() && r.kind == Kind::Base && r.seq >= 2);
    let start = match (tip, from_base) {
        (Some(t), _) => (t.hash.clone(), t.seq, t.hlc.0),
        (None, Some(b)) => (b.prev.clone(), b.seq - 1, 0),
        (None, None) => (GENESIS.to_string(), 0, 0),
    };
    let (mut prev, mut seq, mut wall) = start;
    for row in rows {
        shape(row, board, origin)?;
        if row.seq != seq + 1 || row.prev != prev {
            return Err(Error::Damaged(format!("row {} does not follow row {seq}", row.seq)));
        }
        if row.hash != row.digest() {
            return Err(Error::Damaged(format!("row {} hash does not match its contents", seq + 1)));
        }
        if row.hlc.0 < wall {
            return Err(Error::Damaged(format!("row {} moves the wall clock backwards", seq + 1)));
        }
        (prev, seq, wall) = (row.hash.clone(), row.seq, row.hlc.0);
    }
    Ok(())
}

fn head_fields(row: &Row) -> Result<([u8; 32], Vec<u8>, u64, String)> {
    let bad = || Error::Damaged(format!("head at row {} is malformed", row.seq));
    let text = |k: &str| row.body.get(k).and_then(|v| v.as_str()).ok_or_else(bad);
    let public: [u8; 32] = unhex(text("public")?).and_then(|b| b.try_into().ok()).ok_or_else(bad)?;
    let sig = unhex(text("sig")?).ok_or_else(bad)?;
    let target = row.body.get("head").and_then(|v| v.as_u64()).ok_or_else(bad)?;
    Ok((public, sig, target, text("hash")?.to_string()))
}

fn check_heads(new: &[Row], tops: &std::collections::BTreeMap<u64, String>, pinned: Option<[u8; 32]>, pool: &str)
    -> Result<(Option<[u8; 32]>, u64)> {
    let (mut public, mut last) = (pinned, 0);
    for row in new.iter().filter(|r| r.kind == Kind::Head) {
        let (key, sig, target, digest) = head_fields(row)?;
        if public.is_some_and(|p| p != key) {
            return Err(Error::Damaged(format!("head at row {} is signed by a different key", row.seq)));
        }
        if target + 1 != row.seq || tops.get(&target) != Some(&digest) {
            return Err(Error::Damaged(format!("head at row {} does not sign the row before it", row.seq)));
        }
        let message = head_message(pool, &row.board, &row.origin, target, &digest);
        let verified = VerifyingKey::from_bytes(&key).ok().zip(Signature::from_slice(&sig).ok())
            .is_some_and(|(k, s)| k.verify_strict(&message, &s).is_ok());
        if !verified {
            return Err(Error::Damaged(format!("head at row {} has a bad signature", row.seq)));
        }
        (public, last) = (Some(key), row.seq);
    }
    Ok((public, last))
}

/// The rows of ``incoming`` to append to the ``held`` copy of ``origin``'s log: those past
/// what is held, through the last row a verified head covers. `Damaged` for a fork, a broken
/// chain or a forged head; `Gap` when the rows start after the held end and do not start at a base;
/// `Invalid` for a row that is oversized or of a bad origin (never evidence against the origin). Rows
/// that start at a base past the held end restart the copy; rows before the first row held are skipped.
pub fn accept(board: &str, origin: &str, held: &[Row], incoming: &[Row], pinned: Option<[u8; 32]>, pool: &str)
    -> Result<Accepted> {
    if !valid_origin(origin) || !valid_name(board) {
        return Err(Error::Invalid("origin or board is not an id".into()));
    }
    if incoming.iter().any(|r| r.size() > MAX_ROW_BYTES) {
        return Err(Error::Invalid("a row exceeds its size bound".into()));
    }
    let held_first = held.first().map_or(1, |r| r.seq);
    let skipped = incoming.iter().position(|r| r.seq >= held_first).unwrap_or(incoming.len());
    let incoming = &incoming[skipped..];
    let none = Accepted { rows: vec![], public: pinned, restart: false };
    let Some(first) = incoming.first().map(|r| r.seq) else { return Ok(none) };
    if first < 1 {
        return Err(Error::Damaged("rows carry no sequence number".into()));
    }
    let tip = held.last().map_or(0, |r| r.seq);
    let restart = first > tip + 1;
    if restart && incoming[0].kind != Kind::Base {
        return Err(Error::Gap(format!("rows start at {first} but {tip} is the last held")));
    }
    let (kept, new): (&[Row], &[Row]) = if restart {
        (&[], incoming)
    } else {
        let start = (first - held_first) as usize;
        let overlap = &held[start..held.len().min(start + incoming.len())];
        if let Some((mine, _)) = overlap.iter().zip(incoming).find(|(m, t)| m.hash != t.hash) {
            return Err(Error::Damaged(format!("row {} differs from the held copy", mine.seq)));
        }
        (held, &incoming[overlap.len()..])
    };
    if new.is_empty() {
        return Ok(none);
    }
    check_chain(new, board, origin, kept.last())?;
    let tops = kept.iter().chain(new).map(|r| (r.seq, r.hash.clone())).collect();
    let (public, last) = check_heads(new, &tops, pinned, pool)?;
    Ok(Accepted { rows: new.iter().filter(|r| r.seq <= last).cloned().collect(), public, restart })
}
