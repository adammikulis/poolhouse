//! A pool directory of another charter version stops the node with one plain sentence and is never read, moved or converted: the node
//! does not start beside it as an unfounded device, and the files of the old pool are the bytes they were.
//!
//! The pool is made by this build (a real founded pool of the test kit) and then made over into a pool of charter version 3 in two ways:
//! only its version number changed, and a whole version 3 charter (`strength` for `claim`, no `floors`, no `first.delegate`) whose pool
//! id, directory and founding act agree with each other, so nothing but the version tells it from a pool of its own version.

mod kit;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use kit::gov::*;
use poolhouse_node::error::Error;
use poolhouse_node::fsutil::sha256_hex;
use poolhouse_node::gov::charter::{Unit, OTHER_VERSION};
use poolhouse_node::gov::pool::GovPool;
use poolhouse_node::membership::{Policy, Pool};
use poolhouse_node::node::Node;
use poolhouse_node::row::canonical;
use serde_json::{json, Value};
use tempfile::TempDir;

const A: u8 = 1;

/// A state directory holding the pool a real node of this build founds, and the directory of that pool.
fn founded() -> (TempDir, PathBuf) {
    let state = kit::short_dir();
    let charter = charter(&[phone_admin(A, "ann"), weak(2, "ann")], Policy::Secure, Unit::Person);
    let pool = GovPool::create(state.path(), found_act(&charter)).unwrap();
    let dir = pool.dir().to_path_buf();
    drop(pool);
    (state, dir)
}

fn read(file: &Path) -> Value {
    serde_json::from_slice(&std::fs::read(file).unwrap()).unwrap()
}

/// The pool made over into one whose founding charter is version 3 and whose only change is that number.
fn only_the_version(dir: &Path) {
    let mut found = read(&dir.join("charter.json"));
    found["body"]["v"] = json!(3);
    std::fs::write(dir.join("charter.json"), found.to_string()).unwrap();
}

/// The pool made over into a pool of charter version 3 as that version wrote one, with the id of its own anchor: the directory is renamed
/// to it and the founding act names it. The keys' acceptances stay what they were, for no member of this build could check them.
fn whole_version_three(dir: &Path) -> PathBuf {
    let mut found = read(&dir.join("charter.json"));
    let body = found["body"].as_object_mut().unwrap();
    body.insert("v".into(), json!(3));
    body.remove("floors");
    body["first"].as_object_mut().unwrap().remove("delegate");
    for key in body["admins"].as_array_mut().unwrap() {
        let key = key.as_object_mut().unwrap();
        let claim = key.remove("claim").unwrap();
        key.insert("strength".into(), json!(if claim["method"] == "phone-keystore" { "strong" } else { "weak" }));
    }
    let mut bare = found["body"].clone();
    bare["admins"].as_array_mut().unwrap().iter_mut().for_each(|k| k["acceptance"] = json!(""));
    let id = sha256_hex(canonical(&bare).as_bytes())[..32].to_string();
    found["pool"] = json!(id);
    found["nonce"] = json!(id);
    std::fs::write(dir.join("charter.json"), found.to_string()).unwrap();
    let renamed = dir.parent().unwrap().join(&id);
    std::fs::rename(dir, &renamed).unwrap();
    renamed
}

/// Every file under ``root`` with its bytes, by path relative to it, and every directory.
fn tree(root: &Path) -> BTreeMap<PathBuf, Option<Vec<u8>>> {
    fn walk(root: &Path, at: &Path, out: &mut BTreeMap<PathBuf, Option<Vec<u8>>>) {
        for entry in std::fs::read_dir(at).unwrap() {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(root).unwrap().to_path_buf();
            if path.is_dir() {
                out.insert(relative, None);
                walk(root, &path, out);
            } else {
                out.insert(relative, Some(std::fs::read(&path).unwrap()));
            }
        }
    }
    let mut out = BTreeMap::new();
    walk(root, root, &mut out);
    out
}

fn refusal_of(state: &Path) -> Error {
    match Node::open(state) {
        Err(error) => error,
        Ok(_) => panic!("a node opened beside a pool of another charter version"),
    }
}

/// The real `poolhouse-node run` on ``state``: how it ended, and what it said.
fn run_node(state: &Path) -> (std::process::ExitStatus, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_poolhouse-node"))
        .args(["run", "--state"]).arg(state).args(["--presence", "terminal"])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    let deadline = Instant::now() + Duration::from_secs(60);
    let status = loop {
        if let Some(status) = child.try_wait().unwrap() {
            break status;
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("a node beside a pool of another charter version is still running: it started");
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    let mut said = String::new();
    std::io::Read::read_to_string(&mut child.stderr.take().unwrap(), &mut said).unwrap();
    (status, said)
}

/// The sentence a person is told, whatever way the node meets the pool.
fn assert_plain(said: &str) {
    assert!(said.contains(OTHER_VERSION), "{said}");
    assert!(said.contains("another version of Poolhouse") && said.contains("cannot be used") && said.contains("make a new pool"), "{said}");
    assert!(!said.contains("damaged") && !said.contains("charter.json") && !said.contains("not a charter"), "a pool of another version is not said to be damaged: {said}");
}

#[test]
fn a_pool_of_charter_version_three_stops_the_node_with_one_plain_sentence_and_is_left_as_it_was() {
    for (what, make) in [("only its version number changed", only_the_version as fn(&Path)), ("a whole version 3 charter", |dir: &Path| drop(whole_version_three(dir)))] {
        let (state, dir) = founded();
        make(&dir);
        let before = tree(&state.path().join("pools"));
        assert_eq!(before.keys().filter(|p| p.components().count() == 1).count(), 1, "{what}: one pool directory is planted");

        let error = refusal_of(state.path());
        assert!(matches!(error, Error::Invalid(_)), "{what}: a pool of another version is refused, not damaged: {error}");
        assert_eq!(error.to_string(), format!("invalid: {OTHER_VERSION}"), "{what}");
        assert!(matches!(Pool::load(state.path()), Err(Error::Invalid(ref m)) if m.as_str() == OTHER_VERSION), "{what}: the pool is not read as no pool");

        let (status, said) = run_node(state.path());
        assert!(!status.success(), "{what}: the node ran on and exited well: {said}");
        assert_plain(&said);
        assert_eq!(said.trim().lines().count(), 1, "{what}: one sentence, said once: {said}");

        assert_eq!(tree(&state.path().join("pools")), before, "{what}: the old pool is byte for byte what it was");
        assert!(std::fs::read_dir(state.path().join("pool-scratch")).unwrap().next().is_none(), "{what}: nothing of the pool was rebuilt in the scratch directory");
        for aside in ["pools-old-format", "pool.json", "pool.json.old-format", "grants_floor.json"] {
            assert!(!state.path().join(aside).exists(), "{what}: {aside} was made: an old pool is neither moved nor converted");
        }
        assert_eq!(std::fs::read_dir(state.path().join("pools")).unwrap().count(), 1, "{what}: and no pool was made beside it");
    }
}

#[test]
fn a_pool_of_its_own_version_beside_the_same_state_still_opens() {
    let (state, dir) = founded();
    let before = tree(&dir);
    let node = Node::open(state.path()).unwrap_or_else(|e| panic!("a pool of this version does not open: {e}"));
    assert!(node.members.gov().is_some(), "the pool is read");
    drop(node);
    assert_eq!(read(&dir.join("charter.json"))["body"]["v"], json!(4));
    assert_eq!(tree(&dir).keys().collect::<Vec<_>>(), before.keys().collect::<Vec<_>>());
}
