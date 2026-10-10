mod kit;

use kit::*;
use poolhouse_node::fsutil::{hex, sha256_hex};
use poolhouse_node::grants::Grants;
use poolhouse_node::identity::Holder;
use poolhouse_node::node::Node;
use poolhouse_node::sync::exchange_nodes;
use serde_json::{json, Value};
use tempfile::tempdir;

fn put(n: &mut Node, token: &str, upload: &str, offset: usize, total: usize, data: &[u8]) -> Value {
    req(n, "file_put", "demo", token, json!({"upload": upload, "offset": offset, "total": total, "data": hex(data)}))
}

/// Store ``bytes`` in two chunks and return the blob id.
fn store(n: &mut Node, token: &str, upload: &str, bytes: &[u8]) -> String {
    let cut = bytes.len() / 2;
    assert_eq!(ok(put(n, token, upload, 0, bytes.len(), &bytes[..cut]))["staged"], cut);
    ok(put(n, token, upload, cut, bytes.len(), &bytes[cut..]))["blob"].as_str().unwrap().to_string()
}

fn meta(blob: &str, handle: &str, text: bool) -> Value {
    json!({"id": handle, "blob": blob, "sha": blob, "name": "notes.txt", "size": 20, "type": "text", "text": text})
}

fn attach(n: &mut Node, token: &str, to: &str, file: Value) -> Value {
    req(n, "post", "demo", token, json!({"kind": "message", "fields": {"type": "file", "to": to, "body": "file: notes.txt", "file": file}}))
}

fn files(n: &mut Node, token: &str) -> Vec<Value> {
    ok(req(n, "read", "demo", token, json!({"files": true})))["entries"].as_array().unwrap().clone()
}

fn get(n: &mut Node, token: &str, blob: &str, offset: usize) -> Value {
    req(n, "file_get", "demo", token, json!({"blob": blob, "offset": offset}))
}

#[test]
fn a_file_is_stored_once_by_what_was_stored_and_read_back_by_whoever_may_read_its_message() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (bob, bt), (_, ct)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"), session(&mut n, "demo", "c"));
    let bytes: Vec<u8> = (0..1000u32).map(|i| (i % 251) as u8).collect();
    let blob = store(&mut n, &at, "upload0001", &bytes);
    assert_eq!(blob, sha256_hex(&bytes), "the id is the hash of the stored bytes");
    assert_eq!(store(&mut n, &at, "upload0002", &bytes), blob, "the same bytes are stored once");
    let handle = &blob[..12];
    ok(attach(&mut n, &at, &bob, meta(&blob, handle, false)));
    assert_eq!(code(&get(&mut n, &ct, &blob, 0)), "denied", "a third session cannot read a file sent to bob");
    assert_eq!(code(&get(&mut n, &ct, &sha256_hex(b"nothing"), 0)), "denied", "nor one that is not there");
    for token in [&at, &bt] {
        let got = ok(get(&mut n, token, &blob, 0));
        assert_eq!((got["size"].clone(), got["data"].as_str().unwrap().len()), (json!(1000), 2000));
    }
    assert_eq!(ok(get(&mut n, &bt, &blob, 990))["data"].as_str().unwrap().len(), 20, "a read from an offset");
    assert_eq!(ok(get(&mut n, &bt, &blob, 5000))["data"], "");
    assert_eq!(files(&mut n, &bt).len(), 1);
    assert!(files(&mut n, &ct).is_empty());
    assert_eq!(files(&mut n, &bt)[0]["gone"], false);
}

#[test]
fn an_upload_is_checked_chunk_by_chunk_and_a_file_message_must_name_a_stored_blob() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, at) = session(&mut n, "demo", "a");
    ok(put(&mut n, &at, "upload0001", 0, 10, b"12345"));
    assert_eq!(code(&put(&mut n, &at, "upload0001", 0, 10, b"12345")), "invalid", "a chunk starts where the last ended");
    assert_eq!(code(&put(&mut n, &at, "upload0001", 5, 6, b"12345")), "quota", "past the announced size");
    assert_eq!(code(&put(&mut n, &at, "bad", 0, 1, b"1")), "invalid");
    assert_eq!(code(&put(&mut n, &at, "upload0003", 0, 20 * 1024 * 1024, b"1")), "quota");
    assert_eq!(code(&req(&mut n, "file_put", "demo", &at, json!({"upload": "upload0004", "offset": 0, "total": 1, "data": "zz"}))), "invalid");
    assert_eq!(code(&req(&mut n, "file_put", "demo", "", json!({"upload": "upload0005", "offset": 0, "total": 1, "data": "00"}))), "denied");
    let blob = ok(put(&mut n, &at, "upload0001", 5, 10, b"67890"))["blob"].as_str().unwrap().to_string();
    assert_eq!(blob, sha256_hex(b"1234567890"));
    let missing = sha256_hex(b"never sent");
    assert_eq!(code(&attach(&mut n, &at, "#general", meta(&missing, &missing[..12], true))), "invalid", "the bytes go first");
    for bad in [json!({"id": "xyz"}), json!({"blob": "short"}), json!({"extra": 1}), json!({"name": "a\nb"}), json!({"size": -1}), json!({"text": "yes"})] {
        let mut m = meta(&blob, &blob[..12], true);
        for (k, v) in bad.as_object().unwrap() {
            m[k] = v.clone();
        }
        assert_eq!(code(&attach(&mut n, &at, "#general", m)), "invalid", "{bad}");
    }
    let plain = req(&mut n, "post", "demo", &at, json!({"kind": "message", "fields": {"type": "file", "to": "#general", "body": "no file"}}));
    assert_eq!(code(&plain), "invalid", "a file message carries its file");
    let sneaky = req(&mut n, "post", "demo", &at, json!({"kind": "message", "fields": {"type": "status", "to": "#general", "body": "x", "file": meta(&blob, &blob[..12], true)}}));
    assert_eq!(code(&sneaky), "invalid", "and only a file message does");
    ok(attach(&mut n, &at, "#general", meta(&blob, &blob[..12], true)));
}

struct Refusing;
impl Grants for Refusing {
    fn allows(&self, _: &Holder, action: &str) -> bool {
        action != "file_delete"
    }
}

#[test]
fn deleting_a_file_shreds_its_bytes_keeps_its_messages_and_needs_the_grant() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (bob, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    let blob = store(&mut n, &at, "upload0001", b"a secret worth shredding");
    let handle = blob[..12].to_string();
    ok(attach(&mut n, &at, &bob, meta(&blob, &handle, true)));
    let path = dir.path().join("boards/demo/files/blobs").join(format!("{blob}.bin"));
    assert!(path.exists());
    n.grants = Box::new(Refusing);
    assert_eq!(code(&req(&mut n, "file_delete", "demo", &bt, json!({"handle": handle}))), "denied");
    n.grants = Box::new(poolhouse_node::grants::StubGrants);
    assert_eq!(code(&req(&mut n, "file_delete", "demo", &bt, json!({"handle": "0123456789ab"}))), "invalid", "no such file");
    assert_eq!(code(&req(&mut n, "file_delete", "demo", &bt, json!({"handle": "short"}))), "invalid");
    assert_eq!(ok(req(&mut n, "file_delete", "demo", &bt, json!({"handle": handle})))["shredded"], 1);
    assert!(!path.exists());
    assert_eq!(code(&get(&mut n, &bt, &blob, 0)), "denied", "a deleted file is not available to anyone");
    assert_eq!(code(&get(&mut n, &at, &blob, 0)), "denied");
    assert_eq!(files(&mut n, &bt)[0]["gone"], true, "the message stays, marked gone");
    let audit = ok(req(&mut n, "read", "demo", &bt, json!({"kind": "audit"})));
    assert!(audit["entries"].as_array().unwrap().iter().any(|e| e["fields"]["event"] == "file.delete"));
    drop(n);
    let mut again = node(dir.path());
    assert_eq!(code(&get(&mut again, &bt, &blob, 0)), "denied", "still gone after a restart");
}

#[test]
fn a_file_message_crosses_devices_but_its_bytes_stay_where_they_were_stored() {
    let (da, db) = (tempdir().unwrap(), tempdir().unwrap());
    let (mut a, mut b) = (node(da.path()), node(db.path()));
    let ((_, at), (_, bt)) = (session(&mut a, "demo", "alice"), session(&mut b, "demo", "bob"));
    let blob = store(&mut a, &at, "upload0001", b"only on the first device");
    ok(attach(&mut a, &at, "#general", meta(&blob, &blob[..12], true)));
    exchange_nodes(&mut a, &mut b).unwrap();
    let seen = files(&mut b, &bt);
    assert_eq!((seen.len(), seen[0]["foreign"].clone()), (1, json!(true)));
    assert_eq!(code(&get(&mut b, &bt, &blob, 0)), "denied", "the other device does not hold the bytes");
    assert!(ok(get(&mut a, &at, &blob, 0))["size"] == 24);
}

#[test]
fn a_file_in_a_private_room_is_read_only_by_its_members() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(req(&mut n, "room", "demo", &at, json!({"op": "create", "room": "#vault", "private": true})));
    let blob = store(&mut n, &at, "upload0001", b"vault contents");
    ok(attach(&mut n, &at, "#vault", meta(&blob, &blob[..12], true)));
    assert_eq!(code(&get(&mut n, &bt, &blob, 0)), "denied");
    assert!(files(&mut n, &bt).is_empty());
    assert_eq!(files(&mut n, &at).len(), 1);
}
