//! Current charter authority on real isolated nodes; seeded keys are protocol fixtures, not hardware custody proof.
mod kit;

use ed25519_dalek::{Signer, SigningKey};
use kit::grants::{Person, Spec};
use kit::*;
use poolhouse_node::approvals::challenge;
use poolhouse_node::fsutil::{hex, sha256_hex};
use poolhouse_node::membership::Policy;
use poolhouse_node::node::Node;
use poolhouse_node::row::Kind;
use serde_json::{json, Value};
use tempfile::{tempdir, TempDir};

struct Governed {
    _dir: TempDir,
    node: Node,
    name: String,
    token: String,
    outsider: Person,
}

impl Governed {
    fn new() -> Self {
        let dir = tempdir().unwrap();
        let mut node = kit::node(dir.path());
        let admin = Person { key: SigningKey::from_bytes(&[1; 32]) };
        // Existing governance fixtures establish a strong charter key without claiming physical phone evidence.
        node.persons.add_phone_key("ed25519", &admin.public(), "pool-admin", kit::grants::now_ms()).unwrap();
        node.set_signer(kit::gov::person(1, "pool-admin"));
        node.pool_create("fixture-pool", Policy::Secure, "").unwrap();
        let (name, token) = session(&mut node, "demo", "carrier");
        let outsider = kit::grants::person();
        let params = outsider.enrolment("local-person", Some((&admin, &node.cert.fingerprint())));
        ok(req(&mut node, "person_enroll", "", &token, params));
        assert!(node.persons.get(&outsider.fingerprint()).is_some());
        assert!(!node.persons.is_phone_key(&outsider.public()));
        assert!(node.members.gov().unwrap().state().chain.latest().charter.admin(&outsider.public()).is_none());
        Self { _dir: dir, node, name, token, outsider }
    }

    fn file(&mut self) -> (String, std::path::PathBuf, Vec<u8>) {
        let bytes = b"isolated shared file contents".to_vec();
        let blob = ok(req(&mut self.node, "file_put", "demo", &self.token,
                         json!({"upload":"upload0001", "offset":0, "total":bytes.len(), "data":hex(&bytes)})))["blob"].as_str().unwrap().to_string();
        let handle = blob[..12].to_string();
        ok(req(&mut self.node, "post", "demo", &self.token, json!({"kind":"message", "fields":{
            "type":"file", "to":"#general", "body":"file: fixture.txt", "file":{
                "id":handle, "blob":blob, "sha":blob, "name":"fixture.txt", "size":bytes.len(), "type":"text", "text":true}}})));
        let path = self.node.dir.join("boards/demo/files/blobs").join(format!("{blob}.bin"));
        assert_eq!(std::fs::read(&path).unwrap(), bytes);
        (handle, path, bytes)
    }

    fn issue(&mut self, id: &str, action: &str, resource: &str, append: bool) -> Value {
        let spec = Spec::session(&self.name, action).on(resource);
        let mut params = self.outsider.issue_params("demo", id, &spec);
        if append {
            params["op"] = json!("issue");
            params["board"] = json!("demo");
            self.node.host("demo").unwrap().board.append_person(Kind::Grant, &self.name, params).unwrap();
            json!({"injected_signed_row":true})
        } else {
            req(&mut self.node, "grant_issue", "demo", &self.token, params)
        }
    }
}

#[test]
fn an_enrolled_non_admin_raw_grant_cannot_delete_the_shared_file() {
    let mut rig = Governed::new();
    let (handle, path, bytes) = rig.file();
    let issued = rig.issue("g-00000000000000a1", "file_delete", &handle, false);
    let deleted = req(&mut rig.node, "file_delete", "demo", &rig.token, json!({"handle":handle}));
    let intact = std::fs::read(&path).is_ok_and(|found| found == bytes);
    assert!(code(&deleted) == "denied" && intact, "issue={issued}; delete={deleted}; bytes_intact={intact}");
}

#[test]
fn a_valid_non_admin_signed_row_cannot_bypass_the_grant_fold() {
    let mut rig = Governed::new();
    let (handle, path, bytes) = rig.file();
    rig.issue("g-00000000000000a2", "file_delete", &handle, true);
    let deleted = req(&mut rig.node, "file_delete", "demo", &rig.token, json!({"handle":handle}));
    let intact = std::fs::read(&path).is_ok_and(|found| found == bytes);
    assert!(code(&deleted) == "denied" && intact, "delete={deleted}; bytes_intact={intact}");
}

#[test]
fn a_non_admin_approval_cannot_write_a_grant_that_deletes_a_file() {
    let mut rig = Governed::new();
    let (handle, path, bytes) = rig.file();
    let made = ok(req(&mut rig.node, "approval_request", "demo", &rig.token,
                      json!({"asks":[kit::phone::ask("file_delete", &handle)]})));
    let key = rig.outsider.fingerprint();
    let signature = hex(&rig.outsider.key.sign(&challenge::bytes(&made["challenge"], "approve", &key)).to_bytes());
    let (_, carrier) = session(&mut rig.node, "demo", "approver");
    let decided = req(&mut rig.node, "approval_decide", "", &carrier, json!({
        "id":made["id"], "decision":"approve", "key":key, "sig":signature,
        "text_sha256":sha256_hex(made["text"].as_str().unwrap().as_bytes()), "pick":made["code"]}));
    let deleted = req(&mut rig.node, "file_delete", "demo", &rig.token, json!({"handle":handle}));
    let intact = std::fs::read(&path).is_ok_and(|found| found == bytes);
    assert!(code(&deleted) == "denied" && intact, "decision={decided}; delete={deleted}; bytes_intact={intact}");
}

#[test]
fn an_enrolled_non_admin_cannot_persist_a_consent_change() {
    let mut rig = Governed::new();
    let path = rig.node.dir.join("shards.json");
    assert!(!path.exists());
    let issued = rig.issue("g-00000000000000a3", "shard_consent", "*", false);
    // Turning an already-off consent off still writes a durable actor/time record; no interpreter or compute is started.
    let changed = req(&mut rig.node, "shard_consent", "", &rig.token, json!({"enabled":false}));
    assert!(code(&changed) == "denied" && !path.exists(), "issue={issued}; consent={changed}; file_written={}", path.exists());
}
