//! What a non-author reviewer broke in the first version of log rotation: a restart that dropped a held revocation, a relay that moved a copy
//! past rows, a segment that stalled sync, grants that stopped a segment, duplicate revocations that collapsed, a base that lied about
//! its restated rows.

mod kit;

use kit::rotation::*;
use kit::*;
use poolhouse_node::row::{Kind, Row};
use poolhouse_node::sync::{exchange, rows_since};
use serde_json::json;
use std::collections::BTreeMap;
use tempfile::tempdir;

fn revoke(board: &mut poolhouse_node::board::Board) -> Row {
    board.append(Kind::Grant, "node", json!({"op": "revoke", "gid": "g1", "reason": "done"}), "").unwrap()
}

#[test]
fn a_restart_at_a_base_that_leaves_out_a_held_revocation_is_refused_and_the_rows_stay() {
    let m = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (rotating(&m.0, 1), rotating(&m.1, 2));
    let origin = a.origin().to_string();
    revoke(&mut a);
    for i in 0..10 {
        say(&mut a, "writer", &format!("early {i}"));
    }
    exchange(&mut a, &mut b).unwrap();
    let held = view(&b, &origin);
    assert!(held.iter().any(|r| r.kind == Kind::Grant));
    let tip = held.last().unwrap();
    let phantom = forged(&origin, tip.seq + 5, &"a".repeat(64), (Kind::Message, ""), json!({}));
    let tail = base_after(&key(1), &phantom, json!({"carried": 0, "left": 0}), vec![]);
    is_damaged(b.ingest(&origin, &tail, true), "grant revocation");
    assert_eq!(view(&b, &origin), held, "the copy keeps the rows it held");
    assert_eq!(b.first_seq(&origin), 1);
}

#[test]
fn a_member_behind_the_base_jumps_only_for_the_device_that_wrote_the_log_and_only_when_the_segment_is_whole() {
    let m = (tempdir().unwrap(), tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b, mut c) = (rotating(&m.0, 1), rotating(&m.1, 2), rotating(&m.2, 3));
    let origin = a.origin().to_string();
    revoke(&mut a);
    for i in 0..11 {
        say(&mut a, "writer", &format!("early {i}"));
    }
    exchange(&mut a, &mut b).unwrap();
    let held = view(&b, &origin);
    for i in 0..18 {
        note(&mut a, &format!("big {i}"), &"x".repeat(70 * 1024), &[]);
    }
    assert!(a.first_seq(&origin) > 1);
    let tail = a.trusted(&origin).to_vec();
    let first_head = tail.iter().position(|r| r.kind == Kind::Head).unwrap();
    assert!(first_head + 1 < tail.len());
    assert!(matches!(b.ingest(&origin, &tail[..=first_head], false), Err(poolhouse_node::error::Error::Gap(_))), "a relay cannot move a copy past rows");
    assert_eq!(view(&b, &origin), held);
    b.ingest(&origin, &tail[..=first_head], true).unwrap();
    assert_eq!(view(&b, &origin), held, "the segment is not whole: the view is still the rows held, revocation included");
    assert_eq!(b.vector()[&origin].seq, tail[first_head].seq, "and the copy says how far the segment has come");
    b.ingest(&origin, &tail[first_head + 1..], true).unwrap();
    assert!(view(&b, &origin) == tail, "whole, it replaces the rows");
    assert!(view(&b, &origin).iter().any(|r| r.kind == Kind::Grant), "with the revocation restated");
    let mut joiner = c;
    assert!(joiner.ingest(&origin, &tail, false).unwrap() > 0, "a device that holds nothing may start at a base from any relay");
}

#[test]
fn a_member_whose_peer_archive_ends_before_the_base_is_served_the_base() {
    let (dir, other) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, _b) = (rotating(&dir, 1), rotating(&other, 2));
    let origin = a.origin().to_string();
    say_until_new_segment(&mut a);
    let file = archive(&dir).join(format!("{origin}.1-30.jsonl"));
    let head: String = std::fs::read_to_string(&file).unwrap().lines().take(5).map(|l| format!("{l}\n")).collect();
    std::fs::remove_file(&file).unwrap();
    std::fs::write(archive(&dir).join(format!("{origin}.1-5.jsonl")), head).unwrap();
    let served = rows_since(&a, &BTreeMap::from([(origin.clone(), 5)]));
    assert_eq!(served[&origin][0].kind, Kind::Base, "the archive has nothing past row 5, so the member is served the base, not row 5 again");
}

#[test]
fn use_rows_of_a_grant_with_no_limit_are_not_restated_and_those_of_a_limited_grant_are() {
    let dir = tempdir().unwrap();
    let mut a = rotating(&dir, 1);
    let origin = a.origin().to_string();
    a.append(Kind::Grant, "node", json!({"op": "issue", "gid": "free", "to": "writer", "action": "source_add"}), "").unwrap();
    a.append(Kind::Grant, "node", json!({"op": "issue", "gid": "few", "to": "writer", "action": "source_add", "uses": 5}), "").unwrap();
    for _ in 0..3 {
        a.append(Kind::Grant, "node", json!({"op": "use", "gid": "few", "action": "source_add", "target": "x", "user": "writer"}), "").unwrap();
    }
    for _ in 0..25 {
        a.append(Kind::Grant, "node", json!({"op": "use", "gid": "free", "action": "source_add", "target": "x", "user": "writer"}), "").unwrap();
    }
    assert!(a.first_seq(&origin) > 1, "thirty grant rows were more than half of the cap and the log still started a segment");
    let rows = view(&a, &origin);
    let uses = |gid: &str| rows.iter().filter(|r| r.body["op"] == "use" && r.body["gid"] == gid).count();
    assert_eq!((uses("few"), uses("free"), rows.iter().filter(|r| r.body["op"] == "issue").count()), (3, 0, 2));
}

#[test]
fn a_revocation_written_three_times_must_be_restated_three_times() {
    let m = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (rotating(&m.0, 1), rotating(&m.1, 2));
    let origin = a.origin().to_string();
    for _ in 0..3 {
        revoke(&mut a);
    }
    for i in 0..18 {
        say(&mut a, "writer", &format!("early {i}"));
    }
    exchange(&mut a, &mut b).unwrap();
    let tip = b.chain(&origin).last().unwrap().clone();
    let once = base_after(&key(1), &tip, json!({"carried": 1, "left": 0}), vec![(Kind::Grant, "node", json!({"op": "revoke", "gid": "g1", "reason": "done"}))]);
    is_damaged(b.ingest(&origin, &once, false), "grant revocation");
}

#[test]
fn a_base_whose_following_rows_are_not_the_durable_rows_it_counts_is_refused() {
    let dir = tempdir().unwrap();
    let mut joiner = rotating(&dir, 2);
    let origin = "0123456789abcdef0123456789abcdef";
    let phantom = forged(origin, 9, &"a".repeat(64), (Kind::Message, ""), json!({}));
    let lies = base_after(&key(1), &phantom, json!({"carried": 3, "left": 0}), vec![(Kind::Message, "writer", json!({"body": "chatter"}))]);
    is_damaged(joiner.ingest(origin, &lies, false), "durable rows");
    assert!(joiner.rows(origin).is_empty());
}

#[test]
fn archives_beyond_the_bytes_a_log_keeps_are_removed_oldest_first() {
    let dir = tempdir().unwrap();
    let mut a = rotating(&dir, 1);
    let origin = a.origin().to_string();
    std::fs::create_dir_all(archive(&dir)).unwrap();
    for range in ["1-2", "3-4"] {
        std::fs::write(archive(&dir).join(format!("{origin}.{range}.jsonl")), vec![b'x'; 10 << 20]).unwrap();
    }
    a.prune_archives().unwrap();
    let left: Vec<String> = std::fs::read_dir(archive(&dir)).unwrap().flatten().map(|e| e.file_name().to_string_lossy().to_string()).collect();
    assert_eq!(left, vec![format!("{origin}.3-4.jsonl")], "the newest fits in the 16 MiB a log keeps and the older does not");
}

#[test]
fn a_restart_over_two_batches_that_leaves_out_a_held_revocation_is_refused_when_the_segment_is_whole() {
    let m = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (rotating(&m.0, 1), rotating(&m.1, 2));
    let origin = a.origin().to_string();
    revoke(&mut a);
    for i in 0..10 {
        say(&mut a, "writer", &format!("early {i}"));
    }
    exchange(&mut a, &mut b).unwrap();
    let held = view(&b, &origin);
    let tip = held.last().unwrap();
    let note = |after: &Row, title: &str| forged(&origin, after.seq + 1, &after.hash, (Kind::Note, "writer"), json!({"nkind": "fact", "title": title, "body": "x", "author": "writer"}));
    let phantom = forged(&origin, tip.seq + 5, &"a".repeat(64), (Kind::Message, ""), json!({}));
    let base = forged(&origin, phantom.seq + 1, &phantom.hash, (Kind::Base, ""), json!({"carried": 2, "left": 0}));
    let first = note(&base, "one");
    let head1 = signed_head(&key(1), &first);
    let second = note(&head1, "two");
    let head2 = signed_head(&key(1), &second);
    assert!(b.ingest(&origin, &[base, first, head1], true).unwrap() > 0);
    assert_eq!(view(&b, &origin), held, "half a segment changes no row of the view");
    is_damaged(b.ingest(&origin, &[second, head2], true), "grant revocation");
    assert_eq!(view(&b, &origin), held, "and the whole one that leaves out the revocation does not replace it");
}
