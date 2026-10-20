//! Helpers for the log rotation tests: small bounds, boards that start segments, and rows a hostile writer could build.

use ed25519_dalek::{Signer, SigningKey};
use poolhouse_node::board::Board;
use poolhouse_node::error::Error;
use poolhouse_node::fsutil::hex;
use poolhouse_node::logbounds::Bounds;
use poolhouse_node::row::{Kind, Row};
use poolhouse_node::rules::head_message;
use serde_json::{json, Value};
use std::path::PathBuf;
use tempfile::TempDir;

use super::*;

pub fn small() -> Bounds {
    Bounds { rows: 40, bytes: 1 << 24, reserved_rows: 4, reserved_bytes: 1 << 10, last_rows: 1, last_bytes: 1 << 8, author_percent: 100, lineage_percent: 100 }
}

pub fn rotating(dir: &TempDir, seed: u8) -> Board {
    Board::open_rotating(&dir.path().join("log"), "demo", "", key(seed), small(), 75).unwrap()
}

pub fn plain(dir: &TempDir, seed: u8) -> Board {
    Board::open_bounded(&dir.path().join("log"), "demo", "", key(seed), small()).unwrap()
}

pub fn archive(dir: &TempDir) -> PathBuf {
    dir.path().join("log").join("archive")
}

pub fn note(board: &mut Board, title: &str, body: &str, supersedes: &[&str]) -> Row {
    let fields = json!({"nkind": "fact", "title": title, "body": body, "author": "writer", "supersedes": supersedes});
    board.append(Kind::Note, "writer", fields, "").unwrap()
}

pub fn grant(board: &mut Board, gid: &str) -> Row {
    board.append(Kind::Grant, "node", json!({"op": "issue", "gid": gid, "to": "writer", "action": "source_add"}), "").unwrap()
}

pub fn kinds(rows: &[Row]) -> Vec<Kind> {
    rows.iter().map(|r| r.kind).collect()
}

pub fn say_until_new_segment(board: &mut Board) {
    let origin = board.origin().to_string();
    for i in 0..100 {
        if board.first_seq(&origin) > 1 {
            return;
        }
        say(board, "writer", &format!("message {i}"));
    }
    panic!("no segment started");
}

pub fn view(board: &Board, origin: &str) -> Vec<Row> {
    board.rows(origin).to_vec()
}

/// A row of the log of ``origin``, as a hostile writer could build it.
pub fn forged(origin: &str, seq: u64, prev: &str, (kind, actor): (Kind, &str), body: Value) -> Row {
    let row = Row { v: 1, board: "demo".into(), origin: origin.into(), seq, prev: prev.into(), hlc: (4_000_000_000_000 + seq, 0, origin.into()), kind, actor: actor.into(), idem: String::new(), body, hash: String::new() };
    rehash(row)
}

pub fn signed_head(signer: &SigningKey, last: &Row) -> Row {
    let signature = signer.sign(&head_message("", "demo", &last.origin, last.seq, &last.hash));
    let body = json!({"head": last.seq, "hash": last.hash, "public": hex(&signer.verifying_key().to_bytes()), "sig": hex(&signature.to_bytes())});
    forged(&last.origin, last.seq + 1, &last.hash, (Kind::Head, ""), body)
}

/// A base after ``tip``, then ``more`` rows, then a head signed by ``signer``.
pub fn base_after(signer: &SigningKey, tip: &Row, counts: Value, more: Vec<(Kind, &str, Value)>) -> Vec<Row> {
    let mut rows = vec![forged(&tip.origin, tip.seq + 1, &tip.hash, (Kind::Base, ""), counts)];
    for (kind, actor, body) in more {
        let last = rows.last().unwrap().clone();
        rows.push(forged(&tip.origin, last.seq + 1, &last.hash, (kind, actor), body));
    }
    let head = signed_head(signer, rows.last().unwrap());
    rows.push(head);
    rows
}

pub fn is_damaged<T: std::fmt::Debug>(result: Result<T, Error>, saying: &str) {
    match result {
        Err(Error::Damaged(why)) => assert!(why.contains(saying), "{why}"),
        other => panic!("expected a damaged log saying {saying:?}, got {other:?}"),
    }
}

