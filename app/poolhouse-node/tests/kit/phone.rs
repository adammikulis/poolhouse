//! A phone for tests: a P-256 key that signs, a push key that opens what the node seals, and a node it talks to through the same
//! functions the HTTPS endpoint calls. Nothing here is a switch in the node: the phone is made of keys and requests.

use std::net::{IpAddr, Ipv4Addr};
use std::sync::Mutex;

use poolhouse_node::approvals::challenge;
use poolhouse_node::fsutil::{hex, sha256_hex};
use poolhouse_node::node::Node;
use poolhouse_node::phone::enrol::{add_bytes, make_offer};
use super::attest::{Claims, Root};
use super::grants::first_secret;
use poolhouse_node::phone::push::{decrypt_with, Recording};
use ring::agreement::{agree_ephemeral, EphemeralPrivateKey, UnparsedPublicKey, ECDH_P256};
use ring::rand::SystemRandom;
use ring::signature::{EcdsaKeyPair, KeyPair, ECDSA_P256_SHA256_ASN1_SIGNING};
use serde_json::{json, Map, Value};
use tempfile::TempDir;

pub const LOCAL: IpAddr = IpAddr::V4(Ipv4Addr::new(192, 168, 1, 50));

pub struct FakePhone {
    signing: EcdsaKeyPair,
    pub pkcs8: Vec<u8>,
    /// The root that attests this phone's key when it is added, and what the attestation says; none for a phone that shows no chain.
    pub attester: Option<(std::sync::Arc<Root>, Claims)>,
    pub public: String,
    pub name: String,
    pub token: String,
    pub id: String,
    push: Option<EphemeralPrivateKey>,
    push_public: Vec<u8>,
    auth: [u8; 16],
}

impl FakePhone {
    pub fn new(name: &str) -> FakePhone {
        let rng = SystemRandom::new();
        let pkcs8 = EcdsaKeyPair::generate_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, &rng).unwrap();
        let signing = EcdsaKeyPair::from_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, pkcs8.as_ref(), &rng).unwrap();
        let push = EphemeralPrivateKey::generate(&ECDH_P256, &rng).unwrap();
        let push_public = push.compute_public_key().unwrap().as_ref().to_vec();
        let public = hex(signing.public_key().as_ref());
        FakePhone { signing, pkcs8: pkcs8.as_ref().to_vec(), attester: None, public, name: name.into(), token: String::new(), id: String::new(), push: Some(push), push_public, auth: [9u8; 16] }
    }

    /// A phone whose secure hardware, signing as ``root``, says what ``claims`` says when the phone is added.
    pub fn attested(name: &str, root: &std::sync::Arc<Root>, claims: Claims) -> FakePhone {
        FakePhone { attester: Some((root.clone(), claims)), ..FakePhone::new(name) }
    }

    pub fn fingerprint(&self) -> String {
        sha256_hex(key_bytes(&self.public).as_slice())
    }

    /// The signature (hex, DER) of this phone's key over ``bytes``.
    pub fn sign(&self, bytes: &[u8]) -> String {
        hex(self.signing.sign(&SystemRandom::new(), bytes).unwrap().as_ref())
    }

    /// The body of its `add` request for the node ``node_fp`` with the code ``secret``.
    pub fn add_body(&self, node_fp: &str, secret: &str) -> Value {
        let mut p = Map::new();
        for (k, v) in [("secret", json!(secret)), ("name", json!(self.name)), ("key", json!(self.public)), ("p256dh", json!(hex(&self.push_public))),
                       ("auth", json!(hex(&self.auth))), ("at_ms", json!(now()))] {
            p.insert(k.into(), v);
        }
        if let Some((root, claims)) = &self.attester {
            let chain = root.chain(&self.pkcs8, &poolhouse_node::phone::attest::challenge(node_fp, secret), *claims);
            p.insert("attestation".into(), json!(chain));
        }
        let proof = self.sign(&add_bytes(node_fp, &p));
        p.insert("proof".into(), json!(proof));
        Value::Object(p)
    }

    /// The `decide` body for the request ``view`` (as `list` or `get` gives it): the signature over its challenge.
    pub fn decision(&self, view: &Value, decision: &str) -> Value {
        let bytes = challenge::bytes(&view["challenge"], decision, &self.fingerprint());
        json!({"id": view["id"], "decision": decision, "key": self.fingerprint(), "sig": self.sign(&bytes), "text_sha256": sha256_hex(view["text"].as_str().unwrap().as_bytes())})
    }

    /// Open a push body the node sealed to this phone (a phone's push key opens one body: the test makes a phone for each).
    pub fn open_push(&mut self, body: &[u8]) -> Value {
        let own = self.push.take().expect("this phone's push key was used already");
        let ecdh = agree_ephemeral(own, &UnparsedPublicKey::new(&ECDH_P256, &body[21..86]), |s| s.to_vec()).unwrap();
        serde_json::from_slice(&decrypt_with(&ecdh, body, &self.push_public, &self.auth).unwrap()).unwrap()
    }
}

fn key_bytes(public: &str) -> Vec<u8> {
    poolhouse_node::fsutil::unhex(public).unwrap()
}

pub fn now() -> u64 {
    super::grants::now_ms()
}

/// The one-time secret inside the data of a link a person scans (`poolhouse://add-phone?data=...`, URL-safe base64 of JSON).
pub fn secret_of(data: &str) -> String {
    const ALPHABET: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let (mut bits, mut have, mut bytes) = (0u32, 0u32, Vec::new());
    for c in data.chars() {
        bits = bits << 6 | ALPHABET.find(c).expect("a URL-safe base64 character") as u32;
        have += 6;
        if have >= 8 {
            have -= 8;
            bytes.push((bits >> have & 0xff) as u8);
        }
    }
    serde_json::from_slice::<Value>(&bytes).unwrap()["secret"].as_str().unwrap().to_string()
}

/// A node in a temporary directory, that pushes into a recording service, and the phones and sessions of a test.
pub struct Rig {
    pub node: Mutex<Node>,
    pub dir: TempDir,
    pub pushed: Recording,
}

impl Rig {
    pub fn new() -> Rig {
        let dir = super::short_dir();
        let mut node = super::node(dir.path());
        let pushed = Recording::default();
        node.pusher = Box::new(pushed.clone());
        Rig { node: Mutex::new(node), dir, pushed }
    }

    /// A call of the local API.
    pub fn call(&self, method: &str, board: &str, token: &str, params: Value) -> Value {
        super::req(&mut self.node.lock().unwrap(), method, board, token, params)
    }

    /// A request to the phone endpoint, as it comes in from ``LOCAL``.
    pub fn phone(&self, http: &str, op: &str, token: &str, body: Value) -> (u16, Value) {
        self.phone_from(LOCAL, http, op, token, body)
    }

    /// A request to the phone endpoint as it comes in from the address ``ip``. A decision that names no number carries the one the agent
    /// showed, as a person who looked at the agent's screen would choose it; a test of a wrong choice names its own.
    pub fn phone_from(&self, ip: IpAddr, http: &str, op: &str, token: &str, mut body: Value) -> (u16, Value) {
        if op == "decide" && body.get("pick").is_none() {
            let shown = body["id"].as_str().and_then(|id| self.node.lock().unwrap().approvals.get(id).map(|a| a.pick_code.clone())).unwrap_or_default();
            if !shown.is_empty() {
                body["pick"] = json!(shown);
            }
        }
        poolhouse_node::phone::http::handle(&self.node, ip, http, &format!("/phone/v1/{op}"), token, body.to_string().as_bytes())
    }

    /// The number the agent showed for the request ``id``, empty when none had to be matched.
    pub fn shown(&self, id: &str) -> String {
        self.node.lock().unwrap().approvals.get(id).map(|a| a.pick_code.clone()).unwrap_or_default()
    }

    pub fn device(&self) -> String {
        self.node.lock().unwrap().cert.fingerprint()
    }

    /// A one-time code for the next phone: for the first, the secret the prompt of the operating system makes (a test build says yes at
    /// once); for a later one, what the node makes when a person's key asks.
    pub fn offer(&self) -> String {
        if !self.node.lock().unwrap().persons.is_empty() {
            return make_offer(&mut self.node.lock().unwrap(), now()).unwrap();
        }
        let (_, token) = self.session("demo", "first-phone-setup");
        first_secret(&mut |m, b, t, p| self.call(m, b, t, p), &token, "phone", "")
    }

    /// Add ``phone``: with the code the first phone's prompt makes or the code the node makes, then, for a later phone, the approval of
    /// ``by``, an added phone.
    pub fn add(&self, phone: &mut FakePhone, by: Option<&FakePhone>) {
        let secret = self.offer();
        let (status, reply) = self.phone("POST", "add", "", phone.add_body(&self.device(), &secret));
        assert_eq!((status, &reply["ok"]), (200, &json!(true)), "{reply}");
        phone.token = reply["result"]["token"].as_str().unwrap().into();
        phone.id = reply["result"]["phone"].as_str().unwrap().into();
        if let Some(by) = by {
            assert_eq!(reply["result"]["state"], "pending", "a later phone waits for a key to approve it");
            let approval = reply["result"]["approval"].as_str().unwrap().to_string();
            let seen = self.phone("POST", "get", &by.token, json!({"id": approval})).1["result"].clone();
            let done = self.phone("POST", "decide", "", by.decision(&seen, "approve")).1;
            assert_eq!(done["ok"], true, "{done}");
        } else {
            assert_eq!(reply["result"]["state"], "active");
        }
    }

    /// Register a session; its name and token.
    pub fn session(&self, board: &str, id: &str) -> (String, String) {
        super::session(&mut self.node.lock().unwrap(), board, id)
    }

    /// Register where this phone listens for pushes.
    pub fn push_to(&self, phone: &FakePhone, endpoint: &str) {
        let reply = self.phone("POST", "push", &phone.token, json!({"endpoint": endpoint})).1;
        assert_eq!(reply["ok"], true, "{reply}");
    }

    /// The request ``id`` as a phone reads it.
    pub fn get(&self, phone: &FakePhone, id: &str) -> Value {
        let reply = self.phone("POST", "get", &phone.token, json!({"id": id})).1;
        assert_eq!(reply["ok"], true, "{reply}");
        reply["result"].clone()
    }

    /// Ask for ``asks`` as the session ``token`` of ``board``; the reply of the node.
    pub fn ask(&self, board: &str, token: &str, asks: Value, extra: Value) -> Value {
        let mut params = json!({"asks": asks});
        for (k, v) in extra.as_object().cloned().unwrap_or_default() {
            params[k] = v;
        }
        self.call("approval_request", board, token, params)
    }
}

/// One ask for ``action`` on ``resource`` for ten minutes, once.
pub fn ask(action: &str, resource: &str) -> Value {
    json!({"action": action, "resource": resource, "lasts_ms": 600_000, "uses": 1})
}
