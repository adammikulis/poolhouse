//! A node that a phone's key founded, for the tests of who in a pool may grant, decide, read and be added: the phone is listed as the strong
//! admin key, the test person's typed-yes key as a weak one, and a session `lead` of board `demo` asks.

use std::sync::Mutex;

use ed25519_dalek::Signer;
use poolhouse_node::fsutil::{hex, unhex};
use serde_json::{json, Value};

use super::grants::{person, Person, Spec};
use super::phone::{ask, FakePhone, Rig};
use super::{code, ok};

/// The grant id of the grant that lets the session found the pool.
pub const FOUNDING: &str = "g-00000000000000a0";

pub struct Founded {
    pub rig: Rig,
    pub phone: FakePhone,
    pub typed: Person,
    pub name: String,
    pub token: String,
}

/// A node founded by the strong key of a new phone, which lists the typed-yes key beside it as a weak admin key of the same person.
pub fn found() -> Founded {
    let rig = Rig::new();
    let mut phone = FakePhone::new("admin phone");
    rig.add(&mut phone, None);
    let typed = person();
    rig.node.lock().unwrap().persons.add(&typed.public(), "typed yes", super::grants::now_ms()).unwrap();
    let (name, token) = rig.session("demo", "lead");
    let keys = json!([{"key": phone.public, "person": "ann", "label": "phone"}, {"key": typed.public(), "person": "ann", "label": "typed"}]);
    let draft = ok(rig.call("pool_draft_found", "", &token, json!({"title": "home", "policy": "secure", "project": "", "keys": keys})));
    let mut charter = draft["charter"].clone();
    for (i, entry) in draft["accept"].as_array().unwrap().iter().enumerate() {
        let bytes = unhex(entry["sign_bytes"].as_str().unwrap()).unwrap();
        charter["admins"][i]["acceptance"] = json!(if entry["key"] == json!(phone.public) { phone.sign_low(&bytes) } else { hex(&typed.key.sign(&bytes).to_bytes()) });
    }
    ok(rig.call("grant_issue", "demo", &token, typed.issue_params("demo", FOUNDING, &Spec::session(&name, "pool_create").on("home"))));
    ok(rig.call("pool_found", "", &token, json!({"charter": charter})));
    rig.node.lock().unwrap().fatigue.rules.backoff_ms = 0;
    Founded { rig, phone, typed, name, token }
}

impl Founded {
    /// This node as a device that joined the pool would hold it: the same pool, the same certificate, the typed-yes key as the only key and
    /// no phone, so the admin's phone is one this device has not been shown yet.
    pub fn joined(self) -> Founded {
        let typed = self.typed.public();
        let fingerprint = self.typed.fingerprint();
        self.restarted(|root| {
            for file in ["persons.json", "persons_removed.json", "persons_nonces.json", "phones.json"] {
                let _ = std::fs::remove_file(root.join(file));
            }
            let entry = json!({"public": typed, "label": "typed yes", "at_ms": 1, "alg": "ed25519"});
            std::fs::write(root.join("persons.json"), json!({ fingerprint: entry }).to_string()).unwrap();
        })
    }

    /// This node stopped, its state directory changed by ``edit``, and started again with a new session.
    pub fn restarted(self, edit: impl FnOnce(&std::path::Path)) -> Founded {
        let Founded { rig: Rig { node, dir, pushed }, phone, typed, .. } = self;
        drop(node);
        edit(dir.path());
        let mut reopened = super::node(dir.path());
        reopened.pusher = Box::new(pushed.clone());
        reopened.fatigue.rules.backoff_ms = 0;
        let rig = Rig { node: Mutex::new(reopened), dir, pushed };
        let (name, token) = rig.session("demo", "lead");
        Founded { rig, phone, typed, name, token }
    }

    /// The directory of the pool this node founded.
    pub fn pool_dir(&self) -> std::path::PathBuf {
        let id = self.rig.node.lock().unwrap().members.gov().unwrap().id.clone();
        self.rig.dir.path().join("pools").join(id)
    }

    /// The reply of the phone endpoint to ``phone`` answering the request ``id`` with ``decision``.
    pub fn answer(&self, phone: &FakePhone, id: &str, decision: &str) -> Value {
        self.rig.phone("POST", "decide", "", phone.decision(&self.rig.get(phone, id), decision)).1
    }

    /// The id of the request the lead makes for ``action`` on ``resource``.
    pub fn request(&self, action: &str, resource: &str) -> String {
        ok(self.rig.ask("demo", &self.token, json!([ask(action, resource)]), json!({})))["id"].as_str().unwrap().to_string()
    }

    /// Ask for ``action`` on ``resource`` and have ``phone`` approve it.
    pub fn approve_with(&self, phone: &FakePhone, action: &str, resource: &str) -> Value {
        ok(self.answer(phone, &self.request(action, resource), "approve"))
    }

    pub fn grants(&self) -> Value {
        ok(self.rig.call("grants", "demo", &self.token, json!({})))
    }

    /// The status and the reason of the grant ``gid``, or `absent`.
    pub fn state(&self, gid: &str) -> (String, String) {
        let found = self.grants()["grants"].as_array().unwrap().iter().find(|g| g["gid"] == gid).cloned().unwrap_or(json!({"status": "absent", "why": ""}));
        (found["status"].as_str().unwrap().into(), found["why"].as_str().unwrap().into())
    }

    /// The grant ids on the board besides the founding one.
    pub fn gids(&self) -> Vec<String> {
        self.grants()["grants"].as_array().unwrap().iter().filter(|g| g["gid"] != FOUNDING).map(|g| g["gid"].as_str().unwrap().to_string()).collect()
    }

    /// The refusal ``reply`` as its plain sentence, after checking it is a denial.
    pub fn denial(reply: &Value) -> String {
        assert_eq!(code(reply), "denied", "{reply}");
        reply["error"]["message"].as_str().unwrap().to_string()
    }

    /// What the phone endpoint answers to ``op`` for ``phone`` (with its token).
    pub fn endpoint(&self, phone: &FakePhone, op: &str, body: Value) -> Value {
        self.rig.phone("POST", op, &phone.token, body).1
    }

    /// The addresses the node has pushed to so far.
    pub fn pushed_to(&self) -> Vec<String> {
        self.rig.pushed.sent().into_iter().map(|(endpoint, _)| endpoint).collect()
    }
}
