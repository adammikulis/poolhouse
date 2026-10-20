//! Signed segment completeness, durable revocations and authoritative restart boundaries.

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
    let (mut a, mut b, c) = (rotating(&m.0, 1), rotating(&m.1, 2), rotating(&m.2, 3));
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
    assert!(b.chain(&origin) == tail, "whole, it replaces the rows");
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
    let once = base_after(&key(1), &tip, json!({"carried": 1, "left": 0}), vec![b.rows(&origin).iter().find(|row| row.kind == Kind::Grant).unwrap().clone()]);
    is_damaged(b.ingest(&origin, &once, false), "grant revocation");
}

#[test]
fn a_base_whose_following_rows_are_not_the_durable_rows_it_counts_is_refused() {
    let dir = tempdir().unwrap();
    let mut joiner = rotating(&dir, 2);
    let origin = "0123456789abcdef0123456789abcdef";
    let phantom = forged(origin, 9, &"a".repeat(64), (Kind::Message, ""), json!({}));
    let lies = base_after(&key(1), &phantom, json!({"carried": 3, "left": 0}), vec![forged(origin, 1, &"0".repeat(64), (Kind::Message, "writer"), json!({"body": "chatter"}))]);
    is_damaged(joiner.ingest(origin, &lies, false), "durable operation");
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
    let one = note(tip, "one");
    let first = forged(&origin, base.seq + 1, &base.hash, (Kind::Carry, "writer"), json!({"original": one}));
    let head1 = signed_head(&key(1), &first);
    let two = note(&one, "two");
    let second = forged(&origin, head1.seq + 1, &head1.hash, (Kind::Carry, "writer"), json!({"original": two}));
    let head2 = signed_head(&key(1), &second);
    assert!(b.ingest(&origin, &[base, first, head1], true).unwrap() > 0);
    assert_eq!(view(&b, &origin), held, "half a segment changes no row of the view");
    is_damaged(b.ingest(&origin, &[second, head2], true), "grant revocation");
    assert_eq!(view(&b, &origin), held, "and the whole one that leaves out the revocation does not replace it");
}

#[test]
fn an_empty_joiner_keeps_partial_initial_segment_invisible_even_across_restart() {
    let (writer, receiver) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (plain(&writer, 1), plain(&receiver, 2));
    let origin = a.origin().to_string();
    grant(&mut a, "g1");
    for i in 0..18 {
        note(&mut a, &format!("large {i}"), &"x".repeat(70 * 1024), &[]);
    }
    revoke(&mut a);
    a.rotate().unwrap();
    let tail = a.trusted(&origin).to_vec();
    let boundary = tail.iter().position(|row| row.kind == Kind::Head).unwrap() + 1;
    assert!(boundary < tail.len());
    b.ingest(&origin, &tail[..boundary], false).unwrap();
    assert!(b.rows(&origin).is_empty());
    assert!(b.chain(&origin).is_empty(), "an initial incomplete segment is not persisted as a visible log");
    assert_eq!(b.vector()[&origin].seq, tail[boundary - 1].seq);
    drop(b);
    let mut reopened = plain(&receiver, 2);
    assert!(reopened.rows(&origin).is_empty());
    reopened.ingest(&origin, &tail, false).unwrap();
    assert!(reopened.rows(&origin).iter().any(|row| row.kind == Kind::Grant && row.body["op"] == "revoke"));
}


#[test]
fn incomplete_origins_share_the_origin_limit_and_existing_origins_can_finish() {
    let dir = tempdir().unwrap();
    let mut receiver = plain(&dir, 2);
    let mut first = None;
    for number in 0..poolhouse_node::board::MAX_ORIGINS {
        let origin = format!("{number:032x}");
        let one = forged(&origin, 1, &"0".repeat(64), (Kind::Note, "writer"), json!({"nkind":"fact","title":"one","body":"x","author":"writer"}));
        let two = forged(&origin, 2, &one.hash, (Kind::Note, "writer"), json!({"nkind":"fact","title":"two","body":"x","author":"writer"}));
        let tip = forged(&origin, 10, &"a".repeat(64), (Kind::Message, "writer"), json!({"body":"old"}));
        let all = base_after(&key(1), &tip, json!({"carried":2,"left":0}), vec![one, two]);
        let head = signed_head(&key(1), &all[1]);
        let initial = vec![all[0].clone(), all[1].clone(), head.clone()];
        let last = forged(&origin, head.seq + 1, &head.hash, (Kind::Carry, "writer"), all[2].body.clone());
        let ending = vec![last.clone(), signed_head(&key(1), &last)];
        if number + 1 == poolhouse_node::board::MAX_ORIGINS {
            assert!(receiver.ingest(&origin, &initial, true).is_err());
        } else {
            receiver.ingest(&origin, &initial, true).unwrap();
            assert!(receiver.rows(&origin).is_empty());
            first.get_or_insert((origin, ending));
        }
    }
    let (origin, ending) = first.unwrap();
    assert!(receiver.ingest(&origin, &ending, true).unwrap() > 0);
    receiver.forgive(&origin).unwrap();
    assert!(receiver.rows(&origin).is_empty());
}

#[test]
fn a_later_restart_batch_cannot_change_a_known_original_operation() {
    let (writer, receiver) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (plain(&writer, 1), plain(&receiver, 2));
    let first = note(&mut a, "first", "stable", &[]);
    let original = note(&mut a, "original", "unchanged", &[]);
    exchange(&mut a, &mut b).unwrap();
    let origin = a.origin().to_string();
    let held = view(&b, &origin);
    let mut changed = original.clone();
    changed.body["body"] = json!("forged");
    changed = rehash(changed);
    let tip = forged(&origin, held.last().unwrap().seq + 10, &"a".repeat(64), (Kind::Message, "writer"), json!({"body":"old"}));
    let all = base_after(&key(1), &tip, json!({"carried":2,"left":0}), vec![first.clone(), changed]);
    let head = signed_head(&key(1), &all[1]);
    b.ingest(&origin, &[all[0].clone(), all[1].clone(), head.clone()], true).unwrap();
    let last = forged(&origin, head.seq + 1, &head.hash, (Kind::Carry, "writer"), all[2].body.clone());
    is_damaged(b.ingest(&origin, &[last.clone(), signed_head(&key(1), &last)], true), "known original");
    assert_eq!(view(&b, &origin), held);
    assert_ne!(first.id(), original.id());
}

#[test]
fn repeated_authoritative_gap_restarts_prune_a_passive_copys_archives() {
    let (writer, receiver) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (plain(&writer, 1), plain(&receiver, 2));
    let original = note(&mut a, "retained", &"x".repeat(60 * 1024), &[]);
    exchange(&mut a, &mut b).unwrap();
    let origin = a.origin().to_string();
    for _ in 0..12 {
        let tip = b.chain(&origin).last().unwrap();
        let gap = forged(&origin, tip.seq + 10, &"a".repeat(64), (Kind::Message, "writer"), json!({"body":"old"}));
        let segment = base_after(&key(1), &gap, json!({"carried":1,"left":0}), vec![original.clone()]);
        b.ingest(&origin, &segment, true).unwrap();
    }
    let entries: Vec<_> = std::fs::read_dir(archive(&receiver)).unwrap().map(|entry| entry.unwrap()).collect();
    assert!(entries.len() <= 8, "a passive copy must retain at most its archive count budget");
    let bytes: u64 = entries.iter().map(|entry| entry.metadata().unwrap().len()).sum();
    assert!(bytes <= small().bytes);
}
