//! Where a device's key is kept: in a file only when nothing better is there, and never beside a node that was given it.

mod kit;

use std::io::Write;
use std::process::{Command, Stdio};

use kit::{ok, req, short_dir};
use poolhouse_node::client::Client;
use poolhouse_node::device::{self, Custody, Supplied};
use poolhouse_node::error::Error;
use poolhouse_node::node::Node;
use serde_json::{json, Value};

const BIN: &str = env!("CARGO_BIN_EXE_poolhouse-node");

fn supplied(seed: u8) -> Supplied {
    Supplied { seed: [seed; 32], custody: Custody::Keystore }
}

fn fingerprint_of(seed: u8) -> String {
    device::fingerprint(&kit::key(seed))
}

/// ``path`` is not there, or holds a note a node that reads it as a key stops on.
fn assert_not_a_key(path: &std::path::Path) {
    if let Ok(text) = std::fs::read_to_string(path) {
        assert!(text.starts_with("kept "), "{path:?} holds {text:?}");
        assert!(poolhouse_node::fsutil::unhex(text.trim()).is_none(), "a note that is not hex");
    }
}

fn status(n: &mut Node) -> Value {
    ok(req(n, "pool_status", "", "", json!({})))
}

#[test]
fn a_node_given_no_key_keeps_one_in_a_file_and_says_it_is_weak() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    assert!(dir.path().join("device.key").is_file());
    assert_eq!(status(&mut n)["device_key"], json!({"custody": "file", "weak": true}));
}

#[test]
fn a_key_the_node_is_given_is_its_identity_and_is_never_written_to_a_file() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open_with(dir.path(), Some(&supplied(7))).unwrap();
    assert_eq!(n.fingerprint(), fingerprint_of(7));
    assert_eq!(poolhouse_node::cert::board_fingerprint(&n.cert.der).unwrap(), fingerprint_of(7), "the certificate is made from the key it was given");
    assert_eq!(status(&mut n)["device_key"], json!({"custody": "keystore", "weak": false}));
    let names: Vec<String> = std::fs::read_dir(dir.path()).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into()).collect();
    assert_not_a_key(&dir.path().join("device.key"));
    for name in &names {
        let path = dir.path().join(name);
        if path.is_file() {
            let bytes = std::fs::read(&path).unwrap();
            assert!(!bytes.windows(32).any(|w| w == [7u8; 32]), "{name} holds the key");
            assert!(!String::from_utf8_lossy(&bytes).contains(&"07".repeat(32)), "{name} holds the key as hex");
        }
    }
}

#[test]
fn a_node_whose_key_is_kept_elsewhere_will_not_make_itself_another_one() {
    let dir = tempfile::tempdir().unwrap();
    drop(Node::open_with(dir.path(), Some(&supplied(7))).unwrap());
    let refused = Node::open(dir.path()).err().expect("a node with no key starts nothing");
    assert!(matches!(refused, Error::Denied(_)), "{refused}");
    assert!(refused.to_string().contains("keystore"), "{refused}");
    assert_not_a_key(&dir.path().join("device.key"));
}

#[test]
fn a_copy_of_the_state_directory_carries_no_identity() {
    let (from, to) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
    drop(Node::open_with(from.path(), Some(&supplied(7))).unwrap());
    for entry in std::fs::read_dir(from.path()).unwrap() {
        let path = entry.unwrap().path();
        if path.is_file() {
            std::fs::copy(&path, to.path().join(path.file_name().unwrap())).unwrap();
        }
    }
    assert!(Node::open(to.path()).is_err(), "the copy has no key");
    let other = Node::open_with(to.path(), Some(&supplied(8))).err().expect("another key is not the key of this device");
    assert!(matches!(other, Error::Denied(_)), "{other}");
    assert!(Node::open_with(to.path(), Some(&supplied(7))).is_ok(), "the key itself opens it");
}

#[test]
fn a_key_file_for_the_same_key_is_removed_once_the_node_has_the_key_and_another_is_never_overwritten() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("device.key");
    std::fs::write(&file, "07".repeat(32)).unwrap();
    drop(Node::open_with(dir.path(), Some(&supplied(7))).unwrap());
    assert_not_a_key(&file);

    let other = tempfile::tempdir().unwrap();
    std::fs::write(other.path().join("device.key"), "09".repeat(32)).unwrap();
    let refused = Node::open_with(other.path(), Some(&supplied(7))).err().expect("two keys for one device");
    assert!(matches!(refused, Error::Denied(_)), "{refused}");
    assert_eq!(std::fs::read_to_string(other.path().join("device.key")).unwrap(), "09".repeat(32));
}

#[test]
fn a_node_started_with_its_key_on_standard_input_runs_as_that_key_and_leaves_no_file() {
    let dir = short_dir();
    let state = dir.path().join("s");
    let mut child = Command::new(BIN).args(["run", "--state"]).arg(&state).args(["--key-stdin", "--key-custody", "keystore"])
        .stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
    child.stdin.take().unwrap().write_all(format!("{}\n", "0b".repeat(32)).as_bytes()).unwrap();
    kit::netkit::eventually("the node to answer", 15, || Client::connect(&state).is_ok());
    let status = Client::connect(&state).unwrap().call("pool_status", "", "", json!({})).unwrap();
    assert_eq!(Client::connect(&state).unwrap().call("hello", "", "", json!({})).unwrap()["fingerprint"], fingerprint_of(11));
    assert_eq!(status["device_key"], json!({"custody": "keystore", "weak": false}));
    assert_not_a_key(&state.join("device.key"));
    let _ = Client::connect(&state).and_then(|mut c| c.call("shutdown", "", "", json!({})));
    let _ = child.kill();
    let _ = child.wait();
}

#[test]
fn a_node_told_to_read_its_key_that_gets_nothing_valid_does_not_start() {
    let dir = short_dir();
    for input in ["", "not a key\n", &format!("{}\n", "ab".repeat(31))] {
        let mut child = Command::new(BIN).args(["run", "--state"]).arg(dir.path().join("s")).arg("--key-stdin")
            .stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
        child.stdin.take().unwrap().write_all(input.as_bytes()).unwrap();
        assert!(!child.wait().unwrap().success(), "input {input:?}");
        assert!(!dir.path().join("s").join("device.key").exists());
    }
}

#[test]
fn a_state_directory_whose_record_is_gone_is_never_given_another_identity() {
    let dir = tempfile::tempdir().unwrap();
    drop(Node::open_with(dir.path(), Some(&supplied(7))).unwrap());
    std::fs::remove_file(dir.path().join("device.custody.json")).unwrap();
    let alone = Node::open(dir.path()).err().expect("a node with no record and no key makes none");
    assert!(matches!(alone, Error::Denied(_)), "{alone}");
    assert_not_a_key(&dir.path().join("device.key"));
    let other = Node::open_with(dir.path(), Some(&supplied(8))).err().expect("a key that is not the certificate's");
    assert!(matches!(other, Error::Denied(_)), "{other}");
    assert!(Node::open_with(dir.path(), Some(&supplied(7))).is_ok(), "the key of the device opens it and the record is written again");
    assert!(dir.path().join("device.custody.json").is_file());
    std::fs::write(dir.path().join("device.custody.json"), "{").unwrap();
    assert!(matches!(Node::open(dir.path()).err(), Some(Error::Damaged(_))), "a record that is damaged is not read as none");
    let weak = tempfile::tempdir().unwrap();
    drop(Node::open(weak.path()).unwrap());
    std::fs::create_dir(weak.path().join("device.custody.json")).unwrap();
    assert!(Node::open(weak.path()).is_err(), "a record that cannot be read at all is not read as none");
}

#[test]
fn a_state_directory_that_holds_a_wrapped_or_sealed_key_and_nothing_else_is_never_given_a_new_one() {
    for kept in ["device.key.wrapped", "device.key.enc"] {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join(kept), b"x").unwrap();
        let alone = Node::open(dir.path()).err().expect("a key kept elsewhere is not replaced by a new one");
        assert!(matches!(alone, Error::Denied(_)), "{kept}: {alone}");
        assert!(!dir.path().join("device.key").exists(), "{kept}: no key was made");
    }
}

#[test]
fn a_record_that_says_file_or_names_no_fingerprint_is_damaged() {
    for record in [json!({"custody": "file", "fingerprint": "ab".repeat(32)}), json!({"custody": "keystore"}), json!({"custody": "elsewhere", "fingerprint": "ab".repeat(32)})] {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("device.custody.json"), record.to_string()).unwrap();
        assert!(matches!(Node::open(dir.path()).err(), Some(Error::Damaged(_))), "{record}");
    }
}

#[test]
fn a_node_told_the_key_is_in_a_file_or_given_too_long_a_line_does_not_start() {
    let dir = short_dir();
    let mut child = Command::new(BIN).args(["run", "--state"]).arg(dir.path().join("s")).args(["--key-stdin", "--key-custody", "file"])
        .stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
    child.stdin.take().unwrap().write_all(format!("{}\n", "0b".repeat(32)).as_bytes()).unwrap();
    assert!(!child.wait().unwrap().success(), "a key is never given as one that is in a file");
    let mut child = Command::new(BIN).args(["run", "--state"]).arg(dir.path().join("s")).arg("--key-stdin")
        .stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
    child.stdin.take().unwrap().write_all("0b".repeat(500).as_bytes()).unwrap();
    assert!(!child.wait().unwrap().success());
}

#[test]
fn what_a_node_is_told_about_its_key_is_never_printed() {
    let shown = format!("{:?}", supplied(7));
    assert!(!shown.contains('7') && !shown.contains("07"), "{shown}");
}
