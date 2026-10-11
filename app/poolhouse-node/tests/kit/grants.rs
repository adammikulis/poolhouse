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

    /// The enrolment parameters of this key, signed by itself and, when another key is already enrolled, approved by it.
    pub fn enrolment(&self, about: &str, approver: Option<&Person>) -> Value {
        let at = now_ms();
        let message = format!("poolhouse/person-enroll/v1\n{}", canonical(&json!({"op": "enroll", "public": self.public(), "about": about, "at_ms": at})));
        let sign = |p: &Person| hex(&p.key.sign(message.as_bytes()).to_bytes());
        let mut params = json!({"public": self.public(), "about": about, "at_ms": at, "proof": sign(self)});
        if let Some(a) = approver {
            params["by"] = json!(a.fingerprint());
            params["approval"] = json!(sign(a));
        }
        params
    }

    /// The `rebind` params moving the session ``name`` of ``board`` (named by ``harness`` and ``session``), signed by this key for
    /// the node whose certificate fingerprint is ``node``.
    pub fn rebind_params(&self, harness: &str, session: &str, board: &str, name: &str, node: &str) -> Value {
        let (at, nonce) = (now_ms(), fresh_nonce());
        let body = json!({"op": "rebind", "board": board, "name": name, "node": node, "nonce": nonce, "at_ms": at});
        let bytes = format!("poolhouse/session-rebind/v1\n{}", canonical(&body));
        json!({"harness": harness, "session": session, "at_ms": at, "nonce": nonce, "by": self.fingerprint(), "sig": hex(&self.key.sign(bytes.as_bytes()).to_bytes())})
    }

    /// Enrol this key on the node ``call`` reaches (its state directory is ``dir``), carried by the session ``token``, with
    /// the one-time secret a person's command at a terminal would have left there.
    pub fn enroll(&self, dir: &Path, call: Call, token: &str) -> Value {
        let mut params = self.enrolment("a test person", None);
        params["secret"] = json!(expect_enrolment(dir));
        call("person_enroll", "", token, params)
    }

    /// The `person_unenroll` params removing the key ``fingerprint``, signed by this key at ``at_ms``.
    pub fn unenroll_params_at(&self, fingerprint: &str, at_ms: u64) -> Value {
        let message = format!("poolhouse/person-unenroll/v1\n{}", canonical(&json!({"op": "unenroll", "fingerprint": fingerprint, "by": self.fingerprint(), "at_ms": at_ms})));
        json!({"fingerprint": fingerprint, "at_ms": at_ms, "by": self.fingerprint(), "sig": hex(&self.key.sign(message.as_bytes()).to_bytes())})
    }

    pub fn unenroll_params(&self, fingerprint: &str) -> Value {
        self.unenroll_params_at(fingerprint, now_ms())
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

    /// Grant every action of the registry (`only` limits it) to the session ``name`` for an hour.
    pub fn allow(&self, call: Call, board: &str, token: &str, name: &str, only: &[&str]) {
        for action in ACTIONS.iter().filter(|a| only.is_empty() || only.contains(a)) {
            let reply = self.issue(call, board, token, &gid_of(name, action), &Spec::session(name, action));
            assert_eq!(reply["ok"], true, "grant {action} to {name}: {reply}");
        }
    }
}

/// The grant id `allow` gives ``action`` for ``name``.
pub fn gid_of(name: &str, action: &str) -> String {
    format!("g-{}", &sha256_hex(format!("{name} {action}").as_bytes())[..20])
}

/// Leave in the state directory ``dir`` what a person's command at a terminal leaves for the first enrolment: the hash of a
/// fresh secret, which is returned.
pub fn expect_enrolment(dir: &Path) -> String {
    let secret = format!("secret-{}", sha256_hex(&now_ms().to_be_bytes()));
    let hash = json!({"sha256": sha256_hex(secret.as_bytes()), "at_ms": now_ms()});
    std::fs::write(dir.join("enroll_secret.json"), hash.to_string()).unwrap();
    secret
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
