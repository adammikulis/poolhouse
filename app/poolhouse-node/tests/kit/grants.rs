//! A person's key for tests: it enrols on a node and signs the grants a test needs, through the node's own API.

use ed25519_dalek::{Signer, SigningKey};
use poolhouse_node::fsutil::{hex, sha256_hex};
use poolhouse_node::grants::spec::{signing_bytes, ACTIONS};
use poolhouse_node::row::canonical;
use serde_json::{json, Map, Value};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

pub const HOUR: u64 = 3_600_000;

/// How a test reaches a node: a method, a board, a token and params in; the reply out.
pub type Call<'a> = &'a mut dyn FnMut(&str, &str, &str, Value) -> Value;

/// A nonce no other call of this test process made.
pub fn fresh_nonce() -> String {
    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    format!("{:016x}{:016x}", now_ms(), COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst))
}

pub fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64
}

/// The top of the lineage of the process ``pid`` as the node reads it: the terminal shell (or the app) a person signs for.
pub fn anchor_of(pid: u32) -> Value {
    let lineage = poolhouse_node::lineage::of(pid);
    serde_json::to_value(lineage.last().expect("a running process has a lineage")).unwrap()
}

/// The anchor of this test process, which is the peer of every request `req` makes.
pub fn my_anchor() -> Value {
    anchor_of(std::process::id())
}

/// One grant a test asks the person to sign.
#[derive(Clone)]
pub struct Spec {
    pub to: Value,
    pub action: String,
    pub resource: String,
    pub device: String,
    pub from_ms: i64,
    pub until_ms: i64,
    pub uses: Option<u64>,
    pub review: bool,
}

impl Spec {
    /// ``action`` for the session ``name``, anywhere on this board and device, for an hour.
    pub fn session(name: &str, action: &str) -> Spec {
        Spec::to(json!({"kind": "session", "name": name}), action)
    }

    pub fn to(to: Value, action: &str) -> Spec {
        Spec { to, action: action.into(), resource: "*".into(), device: "*".into(), from_ms: 0, until_ms: HOUR as i64, uses: None, review: false }
    }

    pub fn on(mut self, resource: &str) -> Spec {
        self.resource = resource.into();
        self
    }

    pub fn uses(mut self, n: u64) -> Spec {
        self.uses = Some(n);
        self
    }

    /// Only on the device with this certificate fingerprint.
    pub fn device(mut self, fingerprint: &str) -> Spec {
        self.device = fingerprint.into();
        self
    }

    /// Starts ``from`` and ends ``until`` milliseconds from now (either may be negative).
    pub fn window(mut self, from: i64, until: i64) -> Spec {
        (self.from_ms, self.until_ms) = (from, until);
        self
    }
}

pub struct Person {
    pub key: SigningKey,
}

/// The same person key in every test that asks for it.
pub fn person() -> Person {
    Person { key: SigningKey::from_bytes(&[0x70; 32]) }
}

/// A second person key, such as a laptop's.
pub fn agent() -> Person {
    Person { key: SigningKey::from_bytes(&[0x21; 32]) }
}

/// A third person key.
pub fn third() -> Person {
    Person { key: SigningKey::from_bytes(&[0x22; 32]) }
}

fn plus(base: u64, delta: i64) -> u64 {
    base.checked_add_signed(delta).unwrap()
}

impl Person {
    pub fn public(&self) -> String {
        hex(self.key.verifying_key().as_bytes())
    }

    pub fn fingerprint(&self) -> String {
        sha256_hex(self.key.verifying_key().as_bytes())
    }

    fn sign(&self, body: &Map<String, Value>) -> String {
        hex(&self.key.sign(&signing_bytes(body)).to_bytes())
    }

    /// The enrolment parameters of this key, signed by itself and, when another key is already enrolled, approved by it for the
    /// node whose certificate fingerprint is the second of ``approver``.
    pub fn enrolment(&self, about: &str, approver: Option<(&Person, &str)>) -> Value {
        self.enrolment_for(about, approver, &my_anchor())
    }

    /// `enrolment`, with the approval signed for the terminal named by ``anchor``.
    pub fn enrolment_for(&self, about: &str, approver: Option<(&Person, &str)>, anchor: &Value) -> Value {
        let at = now_ms();
        let message = format!("poolhouse/person-enroll/v1\n{}", canonical(&json!({"op": "enroll", "public": self.public(), "about": about, "at_ms": at})));
        let mut params = json!({"public": self.public(), "about": about, "at_ms": at, "proof": hex(&self.key.sign(message.as_bytes()).to_bytes())});
        if let Some((a, node)) = approver {
            let (approved, nonce) = (now_ms(), fresh_nonce());
            let body = json!({"op": "approve", "public": self.public(), "about": about, "at_ms": at, "approved_ms": approved, "by": a.fingerprint(), "node": node, "nonce": nonce, "anchor": anchor});
            let bytes = format!("poolhouse/person-approve/v1\n{}", canonical(&body));
            params["by"] = json!(a.fingerprint());
            params["approved_ms"] = json!(approved);
            params["nonce"] = json!(nonce);
            params["anchor"] = anchor.clone();
            params["approval"] = json!(hex(&a.key.sign(bytes.as_bytes()).to_bytes()));
        }
        params
    }

    /// The `rebind` params moving the session ``name`` of ``board`` (named by ``harness`` and ``session``), signed by this key for
    /// the node whose certificate fingerprint is ``node`` and for the terminal named by ``anchor``.
    pub fn rebind_params(&self, harness: &str, session: &str, board: &str, name: &str, node: &str, anchor: &Value) -> Value {
        let (at, nonce) = (now_ms(), fresh_nonce());
        let body = json!({"op": "rebind", "board": board, "name": name, "node": node, "nonce": nonce, "at_ms": at, "anchor": anchor});
        let bytes = format!("poolhouse/session-rebind/v1\n{}", canonical(&body));
        json!({"harness": harness, "session": session, "at_ms": at, "nonce": nonce, "anchor": anchor, "by": self.fingerprint(), "sig": hex(&self.key.sign(bytes.as_bytes()).to_bytes())})
    }

    /// Enrol this key on the node ``call`` reaches, carried by the session ``token``, with the one-time secret the person's yes makes.
    pub fn enroll(&self, _dir: &Path, call: Call, token: &str) -> Value {
        let mut params = self.enrolment("a test person", None);
        params["secret"] = json!(first_secret(call, token, "key", &self.public()));
        call("person_enroll", "", token, params)
    }

    /// The `person_unenroll` params removing the key ``fingerprint`` from the node whose certificate fingerprint is ``node``,
    /// signed by this key at ``at_ms`` with ``nonce``, for the terminal named by ``anchor``.
    pub fn unenroll_params_with(&self, fingerprint: &str, node: &str, at_ms: u64, nonce: &str, anchor: &Value) -> Value {
        let body = json!({"op": "unenroll", "fingerprint": fingerprint, "by": self.fingerprint(), "at_ms": at_ms, "node": node, "nonce": nonce, "anchor": anchor});
        let message = format!("poolhouse/person-unenroll/v1\n{}", canonical(&body));
        json!({"fingerprint": fingerprint, "at_ms": at_ms, "by": self.fingerprint(), "nonce": nonce, "anchor": anchor, "sig": hex(&self.key.sign(message.as_bytes()).to_bytes())})
    }

    pub fn unenroll_params_at(&self, fingerprint: &str, node: &str, at_ms: u64) -> Value {
        self.unenroll_params_with(fingerprint, node, at_ms, &fresh_nonce(), &my_anchor())
    }

    pub fn unenroll_params(&self, fingerprint: &str, node: &str) -> Value {
        self.unenroll_params_at(fingerprint, node, now_ms())
    }

    /// The params of a request whose body (with its `op` and `board`) a test built by hand, signed by this key.
    pub fn sign_params(&self, mut body: Map<String, Value>) -> Value {
        body.remove("sig");
        let sig = self.sign(&body);
        body.remove("op");
        body.remove("board");
        body.insert("sig".into(), json!(sig));
        Value::Object(body)
    }

    /// The `grant_issue` params for ``spec`` with grant id ``gid``, signed by this key.
    pub fn issue_params(&self, board: &str, gid: &str, spec: &Spec) -> Value {
        let now = now_ms();
        let mut body = Map::new();
        for (k, v) in [("op", json!("issue")), ("board", json!(board)), ("gid", json!(gid)), ("to", spec.to.clone()), ("action", json!(spec.action)),
                       ("scope", json!({"board": board, "device": spec.device, "resource": spec.resource})), ("at_ms", json!(plus(now, spec.from_ms))),
                       ("until_ms", json!(plus(now, spec.until_ms))), ("person", json!(self.fingerprint()))] {
            body.insert(k.into(), v);
        }
        if let Some(n) = spec.uses {
            body.insert("uses".into(), json!(n));
        }
        if spec.review {
            body.insert("conditions".into(), json!({"review": true}));
        }
        let sig = self.sign(&body);
        body.remove("op");
        body.remove("board");
        body.insert("sig".into(), json!(sig));
        Value::Object(body)
    }

    /// Issue ``spec`` as grant ``gid`` on ``board``, carried by the session ``token``; the node's reply.
    pub fn issue(&self, call: Call, board: &str, token: &str, gid: &str, spec: &Spec) -> Value {
        call("grant_issue", board, token, self.issue_params(board, gid, spec))
    }

    /// The `grant_revoke` params for ``gid``, signed by this key.
    pub fn revoke_params(&self, board: &str, gid: &str, reason: &str) -> Value {
        let mut body = Map::new();
        for (k, v) in [("op", json!("revoke")), ("board", json!(board)), ("gid", json!(gid)), ("at_ms", json!(now_ms())), ("reason", json!(reason)), ("person", json!(self.fingerprint()))] {
            body.insert(k.into(), v);
        }
        let sig = self.sign(&body);
        body.remove("op");
        body.remove("board");
        body.insert("sig".into(), json!(sig));
        Value::Object(body)
    }

    pub fn revoke(&self, call: Call, board: &str, token: &str, gid: &str) -> Value {
        call("grant_revoke", board, token, self.revoke_params(board, gid, "test"))
    }

    /// The `approval_decide` params approving the request ``made`` (as `approval_request` answered it), signed by this key.
    pub fn decision(&self, made: &Value) -> Value {
        let fingerprint = self.fingerprint();
        let sig = hex(&self.key.sign(&poolhouse_node::approvals::challenge::bytes(&made["challenge"], "approve", &fingerprint)).to_bytes());
        json!({"id": made["id"], "decision": "approve", "key": fingerprint, "sig": sig, "text_sha256": sha256_hex(made["text"].as_str().unwrap().as_bytes()),
               "pick": made["code"].as_str().unwrap_or("")})
    }

    /// Grant every action of the registry (`only` limits it) to the session ``name`` for an hour.
    pub fn allow(&self, call: Call, board: &str, token: &str, name: &str, only: &[&str]) {
        for action in ACTIONS.iter().filter(|a| **a != "service_bind" && (only.is_empty() || only.contains(a))) {
            let reply = self.issue(call, board, token, &gid_of(name, action), &Spec::session(name, action));
            assert_eq!(reply["ok"], true, "grant {action} to {name}: {reply}");
        }
    }
}

/// The grant id `allow` gives ``action`` for ``name``.
pub fn gid_of(name: &str, action: &str) -> String {
    format!("g-{}", &sha256_hex(format!("{name} {action}").as_bytes())[..20])
}

/// Ask the person for the one-time secret the first key (``purpose`` `key`) or the first phone (`phone`) is added with, as the session
/// ``token``. A node of a test says yes at once (`kit::node`); a node process asks on the terminal named here, where this test types `yes`
/// as a person would, so the same binary a person installs is the one that is run.
pub fn first_secret(call: Call, token: &str, purpose: &str, public: &str) -> String {
    #[cfg(unix)]
    let mut screen = super::screen::screen();
    #[cfg(unix)]
    let named = json!({"purpose": purpose, "public": public, "terminal": screen.name});
    #[cfg(not(unix))]
    let named = json!({"purpose": purpose, "public": public});
    let asked = call("presence_ask", "", token, named);
    assert_eq!(asked["ok"], true, "ask the prompt: {asked}");
    let mut said = asked;
    #[cfg(unix)]
    if said["result"]["state"] == "asking" {
        screen.sees("Type yes");
        screen.types("yes\n");
    }
    let end = std::time::Instant::now() + std::time::Duration::from_secs(20);
    while said["result"]["state"] == "asking" && std::time::Instant::now() < end {
        std::thread::sleep(std::time::Duration::from_millis(50));
        said = call("presence_status", "", token, json!({}));
    }
    assert_eq!(said["result"]["state"], "confirmed", "the person said yes: {said}");
    said["result"]["secret"].as_str().expect("the secret comes once").to_string()
}

/// Enrol the test person on the node ``call`` reaches (its state directory is ``dir``), unless it already is.
pub fn enrolled(dir: &Path, call: Call, token: &str) {
    let held = call("person_keys", "", "", json!({}));
    if held["result"]["keys"].as_array().is_some_and(|k| !k.is_empty()) {
        return;
    }
    let reply = person().enroll(dir, call, token);
    assert_eq!(reply["ok"], true, "enrol the test person: {reply}");
}

/// Enrol the test person on the node process whose state is ``state`` and grant the session ``name`` of ``board`` the actions ``only`` names, or all.
pub fn allow_process(state: &std::path::Path, board: &str, token: &str, name: &str, only: &[&str]) {
    let mut call = |m: &str, b: &str, t: &str, p: Value| match poolhouse_node::client::Client::connect(state).and_then(|mut c| c.call(m, b, t, p)) {
        Ok(v) => json!({"ok": true, "result": v}),
        Err(e) => json!({"ok": false, "error": {"code": e.code(), "message": e.to_string()}}),
    };
    enrolled(state, &mut call, token);
    person().allow(&mut call, board, token, name, only);
}
