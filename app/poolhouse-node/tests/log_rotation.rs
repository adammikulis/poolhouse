//! A log that starts a new segment: a base row at three quarters of the cap, the durable rows restated, the rows before it archived, and
//! what a member that holds the log, one that joins later and one that is behind the base each end with.

mod kit;

use kit::rotation::*;
use kit::*;
use poolhouse_node::board::Board;
use poolhouse_node::row::{Kind, Row, GENESIS, MAX_SEQ};
use poolhouse_node::sync::exchange;
use serde_json::json;
use tempfile::{tempdir, TempDir};

#[test]
fn a_log_starts_a_new_segment_at_three_quarters_of_its_rows_and_the_chain_goes_on() {
    let dir = tempdir().unwrap();
    let mut a = rotating(&dir, 1);
    let origin = a.origin().to_string();
    for i in 0..29 {
        say(&mut a, "writer", &format!("message {i}"));
    }
    assert_eq!((a.first_seq(&origin), a.rows(&origin).len()), (1, 29), "29 of 40 rows is under three quarters");
    let last = say(&mut a, "writer", "the thirtieth");
    let rows = view(&a, &origin);
    assert_eq!(kinds(&rows), vec![Kind::Base, Kind::Head], "no durable row, so the segment is a base and a head");
    assert_eq!((rows[0].seq, rows[0].prev.as_str()), (31, last.hash.as_str()), "the base is the next row of the chain");
    assert_eq!(rows[0].body, json!({"carried": 0, "left": 0}));
    let archived = std::fs::read_to_string(archive(&dir).join(format!("{origin}.1-30.jsonl"))).unwrap();
    assert_eq!(archived.lines().count(), 30, "the thirty rows before the base are kept");
    let next = say(&mut a, "writer", "after");
    assert_eq!((next.seq, next.prev.as_str()), (33, rows[1].hash.as_str()));
    let before = view(&a, &origin);
    drop(a);
    assert_eq!(view(&rotating(&dir, 1), &origin), before, "a restart reads the segment back and its chain verifies from the base");
    assert_eq!(view(&plain(&dir, 1), &origin), before, "and a board that starts no segment reads it too");
}

#[test]
fn a_restart_with_a_log_past_three_quarters_starts_the_segment_once() {
    let dir = tempdir().unwrap();
    let mut a = plain(&dir, 1);
    let origin = a.origin().to_string();
    for i in 0..32 {
        say(&mut a, "writer", &format!("message {i}"));
    }
    assert_eq!(a.first_seq(&origin), 1, "a board that starts none holds the rows");
    drop(a);
    let a = rotating(&dir, 1);
    assert_eq!(kinds(a.rows(&origin)), vec![Kind::Base, Kind::Head], "opening it starts the segment");
    assert_eq!(a.first_seq(&origin), 33);
    let held = view(&a, &origin);
    drop(a);
    assert_eq!(view(&rotating(&dir, 1), &origin), held, "the next restart finds a segment and starts no other");
}

#[test]
fn the_durable_rows_are_restated_and_the_history_is_left_in_the_archive() {
    let dir = tempdir().unwrap();
    let mut a = rotating(&dir, 1);
    let origin = a.origin().to_string();
    let old = note(&mut a, "old", "superseded below", &[]);
    let keep = note(&mut a, "new", "keeps", &[&old.id()]);
    let g = grant(&mut a, "g1");
    a.append(Kind::Room, "writer", json!({"op": "create", "room": "#ops", "title": "ops"}), "").unwrap();
    say_until_new_segment(&mut a);
    let rows = view(&a, &origin);
    assert_eq!(kinds(&rows), vec![Kind::Base, Kind::Note, Kind::Grant, Kind::Room, Kind::Head], "messages and the superseded note stay in the archive");
    assert_eq!(rows[0].body, json!({"carried": 3, "left": 0}));
    assert_eq!((&rows[1].body, &rows[2].body), (&keep.body, &g.body), "a restated row has the body it had");
    assert_eq!((rows[2].actor.as_str(), rows[1].actor.as_str()), ("node", "writer"));
    assert_ne!(rows[1].id(), keep.id(), "and a new id");
}

#[test]
fn durable_rows_past_the_part_of_a_segment_they_may_use_are_counted_in_the_base() {
    let dir = tempdir().unwrap();
    let mut a = rotating(&dir, 1);
    let origin = a.origin().to_string();
    for i in 0..15 {
        note(&mut a, &format!("note {i}"), "x", &[]);
    }
    grant(&mut a, "g1");
    say_until_new_segment(&mut a);
    let rows = view(&a, &origin);
    let notes: Vec<&str> = rows.iter().filter(|r| r.kind == Kind::Note).map(|r| r.body["title"].as_str().unwrap()).collect();
    assert_eq!(notes.len(), 9, "a quarter of 40 rows is 10, the grant is one of them");
    assert_eq!(notes.last(), Some(&"note 14"), "the newest are kept");
    assert_eq!(rows[0].body, json!({"carried": 10, "left": 6}), "and the base says how many were not");
    assert_eq!(rows.iter().filter(|r| r.kind == Kind::Grant).count(), 1, "a grant is always restated");
}

struct Members {
    dirs: Vec<TempDir>,
}

impl Members {
    fn new(n: usize) -> Members {
        Members { dirs: (0..n).map(|_| tempdir().unwrap()).collect() }
    }

    fn open(&self, i: usize) -> Board {
        rotating(&self.dirs[i], i as u8 + 1)
    }
}

#[test]
fn members_across_a_segment_start_end_with_the_writers_view_and_a_chain_that_verifies() {
    let m = Members::new(7);
    let (mut a, mut b, mut c, mut d, mut e) = (m.open(0), m.open(1), m.open(2), m.open(3), m.open(4));
    let origin = a.origin().to_string();
    for i in 0..8 {
        say(&mut a, "writer", &format!("early {i}"));
    }
    grant(&mut a, "g1");
    for other in [&mut b, &mut d, &mut e] {
        exchange(&mut a, other).unwrap();
        assert_eq!(other.rows(&origin).len(), a.rows(&origin).len(), "each of them holds the first rows");
    }
    say_until_new_segment(&mut a);
    let writers = view(&a, &origin);
    assert_eq!(kinds(&writers)[..2], [Kind::Base, Kind::Grant], "the grant is restated");

    exchange(&mut a, &mut b).unwrap();
    assert_eq!(view(&b, &origin), writers, "a member that held the old rows ends with the new segment");
    assert!(std::fs::read_dir(archive(&m.dirs[1])).unwrap().count() == 1, "and keeps the old rows in its archive");

    exchange(&mut a, &mut c).unwrap();
    assert_eq!(view(&c, &origin), writers, "a member that joins later starts at the base");
    assert_eq!(c.first_seq(&origin), writers[0].seq);
    assert!(!archive(&m.dirs[2]).exists(), "it never held the old rows");

    exchange(&mut a, &mut d).unwrap();
    assert_eq!(view(&d, &origin), writers, "a member behind the base is served the old rows from the archive and goes on to the base");
    assert!(archive(&m.dirs[3]).join(format!("{origin}.1-30.jsonl")).exists(), "it holds every old row, not only the ones it had");

    std::fs::remove_dir_all(archive(&m.dirs[0])).unwrap();
    exchange(&mut a, &mut e).unwrap();
    assert_eq!(view(&e, &origin), writers, "with the archive gone a member behind starts at the base");
    assert_eq!(e.first_seq(&origin), writers[0].seq);

    let mut f = m.open(5);
    exchange(&mut b, &mut f).unwrap();
    assert_eq!(view(&f, &origin), writers, "a relay hands a new member the segment");

    a.append(Kind::Note, "writer", json!({"nkind": "fact", "title": "after", "body": "x", "author": "writer", "supersedes": []}), "").unwrap();
    for other in [&mut b, &mut c, &mut d, &mut e, &mut f] {
        exchange(&mut a, other).unwrap();
        assert_eq!(view(other, &origin), view(&a, &origin), "rows after the segment start reach every member");
    }
    drop((a, b, c, d, e, f));
    let tip = |i: usize| view(&m.open(i), &origin);
    assert!((1..6).all(|i| tip(i) == tip(0)), "and every one of them reads its log back after a restart");
}

#[test]
fn a_member_applies_a_segment_only_when_every_restated_row_has_arrived() {
    let m = Members::new(2);
    let (mut a, mut b) = (m.open(0), m.open(1));
    let origin = a.origin().to_string();
    for i in 0..12 {
        say(&mut a, "writer", &format!("early {i}"));
    }
    for i in 0..18 {
        note(&mut a, &format!("big {i}"), &"x".repeat(60 * 1024), &[]);
    }
    assert!(a.first_seq(&origin) > 1, "the thirtieth row started the segment");
    let tail = a.trusted(&origin).to_vec();
    let first_head = tail.iter().position(|r| r.kind == Kind::Head).unwrap();
    assert!(first_head + 1 < tail.len(), "the restated rows are signed in more than one part");
    let old = a.archived_from(&origin, 1, 1000);
    assert_eq!(old.len(), 30, "the archive holds the rows before the base");
    let part: Vec<Row> = old.iter().chain(&tail[..=first_head]).cloned().collect();
    b.ingest(&origin, &part, true).unwrap();
    assert_eq!(b.rows(&origin).len(), old.len(), "the restated rows are not all here: the view is still the old rows");
    assert_eq!(b.chain(&origin).len(), old.len() + first_head + 1, "though the chain holds what arrived");
    b.ingest(&origin, &tail[first_head + 1..], true).unwrap();
    assert!(view(&b, &origin) == tail, "once the last one is here the segment is the view");
    assert_eq!(b.first_seq(&origin), a.first_seq(&origin));
}

#[test]
fn a_replay_of_the_old_segment_changes_nothing_and_a_head_of_another_key_is_refused() {
    let m = Members::new(2);
    let (mut a, mut b) = (m.open(0), m.open(1));
    let origin = a.origin().to_string();
    say_until_new_segment(&mut a);
    exchange(&mut a, &mut b).unwrap();
    let held = view(&b, &origin);
    let old: Vec<Row> = a.archived_from(&origin, 1, 100);
    assert!(old.len() >= 30);
    assert_eq!(b.ingest(&origin, &old, false).unwrap(), 0, "rows before the segment are not taken again");
    assert_eq!(view(&b, &origin), held);
    let tip = held.last().unwrap();
    let attacker = key(99);
    let tail = base_after(&attacker, tip, json!({"carried": 0, "left": 0}), vec![]);
    is_damaged(b.ingest(&origin, &tail, false), "different key");
    assert_eq!(view(&b, &origin), held, "a base under another key is not stored");
}

#[test]
fn a_base_is_refused_when_it_cannot_be_what_a_device_writes() {
    let m = Members::new(3);
    let (mut a, mut b, mut joiner) = (m.open(0), m.open(1), m.open(2));
    let origin = a.origin().to_string();
    let writer = key(1);
    let zero = GENESIS;
    let far = base_after(&writer, &forged(&origin, MAX_SEQ, zero, (Kind::Message, ""), json!({})), json!({"carried": 0, "left": 0}), vec![]);
    is_damaged(joiner.ingest(&origin, &far, false), "highest sequence");
    let heavy = base_after(&writer, &forged(&origin, 5, zero, (Kind::Message, ""), json!({})), json!({"carried": 21, "left": 0}), vec![]);
    is_damaged(joiner.ingest(&origin, &heavy, false), "more rows than a segment may");
    let mut bare = base_after(&writer, &forged(&origin, 5, zero, (Kind::Message, ""), json!({})), json!({"carried": 0, "left": 0}), vec![]);
    bare[0].body = json!({"carried": 0});
    let bare = vec![rehash(bare[0].clone())];
    is_damaged(joiner.ingest(&origin, &bare, false), "malformed");
    assert!(joiner.rows(&origin).is_empty(), "nothing of those was stored");

    for i in 0..12 {
        say(&mut a, "writer", &format!("early {i}"));
    }
    exchange(&mut a, &mut b).unwrap();
    let tip = b.chain(&origin).last().unwrap().clone();
    let short = base_after(&writer, &tip, json!({"carried": 0, "left": 0}), vec![]);
    is_damaged(b.ingest(&origin, &short, false), "under 50 percent");
    assert_eq!(b.first_seq(&origin), 1, "a segment that ends this early does not replace the log");

    for i in 0..12 {
        say(&mut a, "writer", &format!("more {i}"));
    }
    let kept = a.append(Kind::Grant, "node", json!({"op": "revoke", "gid": "g1", "reason": "done"}), "").unwrap();
    exchange(&mut a, &mut b).unwrap();
    let tip = b.chain(&origin).last().unwrap().clone();
    let leaves_out = base_after(&writer, &tip, json!({"carried": 0, "left": 0}), vec![]);
    is_damaged(b.ingest(&origin, &leaves_out, false), "grant revocation");
    let restates = base_after(&writer, &tip, json!({"carried": 1, "left": 0}), vec![(Kind::Grant, "node", kept.body.clone())]);
    assert_eq!(b.ingest(&origin, &restates, false).unwrap(), 3, "a base that restates the revocation is taken");
    assert_eq!(kinds(b.rows(&origin)), vec![Kind::Base, Kind::Grant, Kind::Head]);
}

#[test]
fn a_cut_names_a_seq_and_a_copy_is_cut_by_it_across_a_segment_start() {
    let m = Members::new(3);
    let (mut a, mut b, mut c) = (m.open(0), m.open(1), m.open(2));
    let origin = a.origin().to_string();
    say_until_new_segment(&mut a);
    for i in 0..6 {
        say(&mut a, "writer", &format!("after {i}"));
    }
    a.seal().unwrap();
    exchange(&mut a, &mut b).unwrap();
    exchange(&mut a, &mut c).unwrap();
    let first = b.first_seq(&origin);
    let at = |board: &Board, seq: u64| board.chain(&origin).iter().find(|r| r.seq == seq).unwrap().hash.clone();
    b.cut(&origin, first + 3, &at(&b, first + 3)).unwrap();
    assert_eq!(b.chain(&origin).last().unwrap().seq, first + 3, "the copy keeps the rows through the seq named");
    c.cut(&origin, first - 5, &"0".repeat(64)).unwrap();
    assert!(c.chain(&origin).is_empty(), "a cut before the segment start leaves no copy: those rows are in the archive");
}

#[test]
fn archives_are_removed_by_age_and_by_acknowledgement() {
    let m = Members::new(2);
    let (mut a, mut b) = (m.open(0), m.open(1));
    let count = |m: &Members| std::fs::read_dir(archive(&m.dirs[0])).unwrap().count();
    let age = |days: u64| {
        for file in std::fs::read_dir(archive(&m.dirs[0])).unwrap().flatten() {
            let when = std::time::SystemTime::now() - std::time::Duration::from_secs(days * 24 * 3600);
            std::fs::File::options().write(true).open(file.path()).unwrap().set_modified(when).unwrap();
        }
    };
    say_until_new_segment(&mut a);
    a.prune_archives().unwrap();
    assert_eq!(count(&m), 1, "a new archive stays");
    exchange(&mut a, &mut b).unwrap();
    age(10);
    a.prune_archives().unwrap();
    assert_eq!(count(&m), 0, "after a week, once every device that synced the board is past it");
    say_until_new_segment_again(&mut a);
    assert_eq!(count(&m), 1);
    a.bind_peer(&"f".repeat(64), "0123456789abcdef0123456789abcdef").unwrap();
    age(10);
    a.prune_archives().unwrap();
    assert_eq!(count(&m), 1, "a device that synced and has not acknowledged keeps it");
    age(40);
    a.prune_archives().unwrap();
    assert_eq!(count(&m), 0, "after a month whoever has not acknowledged");
}

fn say_until_new_segment_again(board: &mut Board) {
    let origin = board.origin().to_string();
    let first = board.first_seq(&origin);
    for i in 0..100 {
        if board.first_seq(&origin) > first {
            return;
        }
        say(board, "writer", &format!("again {i}"));
    }
    panic!("no second segment started");
}

#[test]
fn a_crash_between_the_archive_and_the_rename_leaves_the_old_log_and_the_segment_is_started_again() {
    let dir = tempdir().unwrap();
    let mut a = rotating(&dir, 1);
    let origin = a.origin().to_string();
    say_until_new_segment(&mut a);
    let started = view(&a, &origin);
    drop(a);
    let old = archive(&dir).join(format!("{origin}.1-30.jsonl"));
    std::fs::copy(&old, dir.path().join("log").join(format!("{origin}.jsonl"))).unwrap();
    let again = rotating(&dir, 1);
    let rows = view(&again, &origin);
    assert_eq!(kinds(&rows), kinds(&started), "the old log is a log past three quarters and starts its segment on opening");
    assert_eq!(rows[0].seq, started[0].seq);
}
