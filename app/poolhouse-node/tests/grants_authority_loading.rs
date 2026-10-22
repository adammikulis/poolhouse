//! A pool that cannot be read stops the node instead of reading as a pool with no acts, a pool is written whole, and a pool set aside for a
//! version change leaves a floor under every grant entry written before.
mod kit;

use std::path::Path;

use kit::grants::Spec;
use kit::pool::found;
use kit::*;
use poolhouse_node::error::Error;
use poolhouse_node::membership::Pool;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};

fn damaged(root: &Path) -> bool {
    matches!(Pool::load(root), Err(Error::Damaged(_)))
}

fn rewrite(file: &Path, edit: impl FnOnce(Value) -> Value) {
    let before: Value = serde_json::from_slice(&std::fs::read(file).unwrap()).unwrap();
    std::fs::write(file, edit(before).to_string()).unwrap();
}

#[test]
fn a_pool_whose_acts_or_seen_file_is_damaged_is_never_read_as_a_pool_with_no_acts() {
    let pool = found();
    let dir = pool.pool_dir();
    let kit::phone::Rig { node, dir: state, .. } = pool.rig;
    drop(node);
    let root = state.path();
    assert!(!damaged(root), "the pool as written loads");
    let foreign = {
        let mut act: Value = serde_json::from_slice(&std::fs::read(dir.join("charter.json")).unwrap()).unwrap();
        act["pool"] = json!("another-pool");
        act
    };
    let cases: Vec<(&str, &str, Box<dyn Fn(Value) -> Value>)> = vec![
        ("acts.json", "valid JSON of the wrong shape", Box::new(|_| json!([]))),
        ("acts.json", "acts that are not a list", Box::new(|_| json!({"acts": {"a": 1}}))),
        ("acts.json", "an act that is no act", Box::new(|_| json!({"acts": [{"nonsense": true}]}))),
        ("acts.json", "an act of another pool", Box::new(move |_| json!({"acts": [foreign.clone()]}))),
        ("seen.json", "no awake time", Box::new(|v| json!({"seen": v["seen"], "ready": v["ready"]}))),
        ("seen.json", "a seen record of the wrong shape", Box::new(|v| json!({"awake_ms": 5, "seen": [], "ready": v["ready"]}))),
        ("seen.json", "a ready record of the wrong shape", Box::new(|v| json!({"awake_ms": 5, "seen": v["seen"], "ready": "now"}))),
    ];
    for (file, what, damage) in cases {
        let original = std::fs::read(dir.join(file)).unwrap();
        rewrite(&dir.join(file), damage);
        assert!(damaged(root), "{file} with {what}");
        std::fs::write(dir.join(file), original).unwrap();
        assert!(!damaged(root), "{file} restored");
    }
    for file in ["acts.json", "seen.json"] {
        let original = std::fs::read(dir.join(file)).unwrap();
        std::fs::remove_file(dir.join(file)).unwrap();
        assert!(damaged(root), "{file} missing");
        std::fs::write(dir.join(file), original).unwrap();
    }
    let charter = std::fs::read(dir.join("charter.json")).unwrap();
    std::fs::remove_file(dir.join("charter.json")).unwrap();
    assert!(damaged(root), "a pool directory that lost its charter is damage, not no pool");
    std::fs::write(dir.join("charter.json"), charter).unwrap();
    assert!(!damaged(root));
}

#[test]
fn a_pool_is_written_whole_into_its_directory_and_no_scratch_is_left() {
    let pool = found();
    let dir = pool.pool_dir();
    for file in ["charter.json", "acts.json", "seen.json", "refused.json"] {
        assert!(dir.join(file).is_file(), "{file}");
    }
    assert!(!pool.rig.dir.path().join("pool-scratch").join(dir.file_name().unwrap()).exists());
}

#[test]
fn a_pool_that_cannot_be_written_whole_leaves_no_directory_under_pools() {
    let pool = found();
    let act: poolhouse_node::gov::act::Act = serde_json::from_slice(&std::fs::read(pool.pool_dir().join("charter.json")).unwrap()).unwrap();
    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join("pool-scratch"), b"a file where the scratch directory goes").unwrap();
    assert!(poolhouse_node::gov::pool::GovPool::create(root.path(), act).is_err());
    let left = std::fs::read_dir(root.path().join("pools")).map_or(0, |entries| entries.count());
    assert_eq!(left, 0, "nothing a load could take for a pool, whole or not");
}

fn raw_issue(pool: &kit::pool::Founded, gid: &str) {
    let mut row = pool.typed.issue_params("demo", gid, &Spec::session(&pool.name, "sync_now")).as_object().unwrap().clone();
    row.insert("op".into(), json!("issue"));
    row.insert("board".into(), json!("demo"));
    pool.rig.node.lock().unwrap().host("demo").unwrap().board.append_person(Kind::Grant, &pool.name, Value::Object(row)).unwrap();
}

#[test]
fn a_pool_set_aside_for_a_version_change_leaves_a_floor_under_every_older_grant_row() {
    let pool = found();
    raw_issue(&pool, "g-00000000000000e1");
    assert!(pool.state("g-00000000000000e1").1.contains("come from an approval on a phone"), "while the pool stood the row waited as ignored");
    let dir = pool.pool_dir();
    let reopened = pool.restarted(|_| rewrite(&dir.join("charter.json"), |mut act| {
        act["body"]["v"] = json!(act["body"]["v"].as_u64().unwrap() + 1);
        act
    }));
    assert_eq!(reopened.grants()["governance"], "unfounded", "the build no longer reads that pool");
    assert!(reopened.rig.dir.path().join("grants_floor.json").is_file());
    let listing = reopened.grants();
    assert_eq!(reopened.state("g-00000000000000e1").0, "absent", "the older row grants nothing: {listing}");
    assert!(listing["ignored"].as_array().unwrap().iter().any(|e| e["why"].as_str().unwrap().contains("before this device set an older pool aside")), "{listing}");
    std::thread::sleep(std::time::Duration::from_millis(30));
    raw_issue(&reopened, "g-00000000000000e2");
    assert_eq!(reopened.state("g-00000000000000e2").0, "live", "a row written after the floor counts under the rule of a device in no pool");
    let issued = reopened.rig.call("grant_issue", "demo", &reopened.token, reopened.typed.issue_params("demo", "g-00000000000000e3", &Spec::session(&reopened.name, "sync_now")));
    assert_eq!(issued["ok"], true, "{issued}");
    assert_eq!(reopened.state("g-00000000000000e3").0, "live");
}

#[test]
fn a_pool_is_never_written_into_a_directory_that_already_holds_something() {
    let pool = found();
    let act: poolhouse_node::gov::act::Act = serde_json::from_slice(&std::fs::read(pool.pool_dir().join("charter.json")).unwrap()).unwrap();
    let root = tempfile::tempdir().unwrap();
    let held = root.path().join("pools").join(pool.pool_dir().file_name().unwrap());
    std::fs::create_dir_all(&held).unwrap();
    std::fs::write(held.join("occupant"), b"another file").unwrap();
    assert!(poolhouse_node::gov::pool::GovPool::create(root.path(), act).is_err(), "a directory that holds a file cannot be taken by a pool");
    assert_eq!(std::fs::read_dir(&held).unwrap().count(), 1, "and nothing of the pool was written into it");
}

#[test]
fn a_use_row_at_or_before_the_floor_counts_toward_no_grant() {
    let dir = tempfile::tempdir().unwrap();
    let mut board = kit::board(&dir, 1);
    let used = json!({"op": "use", "gid": "g-00000000000000f1", "action": "sync_now", "target": "*", "user": "lead"});
    let early = board.append(Kind::Grant, "node", used.clone(), "").unwrap();
    std::thread::sleep(std::time::Duration::from_millis(20));
    let late = board.append(Kind::Grant, "node", used, "").unwrap();
    let counted = |floor: u64| {
        let mut book = poolhouse_node::grants::book::Book::default();
        book.refresh(&board, floor);
        book.uses("g-00000000000000f1", &|_| true)
    };
    assert_eq!((counted(0), counted(early.hlc.0), counted(late.hlc.0)), (2, 1, 0), "a use counts when it is after the floor and not before");
}
