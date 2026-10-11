//! Devices for the network tests: each a real node with a real TLS listener on loopback.

use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use poolhouse_node::beacon::BeaconConfig;
use poolhouse_node::net::{Net, NetConfig};
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use tempfile::TempDir;

pub struct Dev {
    pub dir: TempDir,
    pub node: Arc<Mutex<Node>>,
    pub net: Arc<Net>,
    pub token: String,
}

pub fn config(beacon: Option<BeaconConfig>) -> NetConfig {
    NetConfig { listen: "127.0.0.1:0".parse().unwrap(), beacon, sync_every: None, project: None }
}

/// A device with a session registered on board `demo`.
pub fn device() -> Dev {
    device_with(config(None))
}

pub fn device_with(cfg: NetConfig) -> Dev {
    let dir = tempfile::tempdir().unwrap();
    let node = Arc::new(Mutex::new(Node::open(dir.path()).unwrap()));
    let net = Net::start(node.clone(), cfg).unwrap();
    let mut d = Dev { dir, node, net, token: String::new() };
    let (name, token) = d.session("demo", "main");
    d.allow("demo", &token, &name, &[]);
    d.token = token;
    d
}

/// A free UDP port on loopback.
pub fn udp_port() -> u16 {
    std::net::UdpSocket::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

/// A beacon on loopback: listens on `bind`, sends to `to`, advertises loopback.
pub fn beacon(bind: u16, to: &[u16]) -> BeaconConfig {
    BeaconConfig {
        bind: format!("127.0.0.1:{bind}").parse().unwrap(),
        send_to: to.iter().map(|p| format!("127.0.0.1:{p}").parse().unwrap()).collect(),
        advertise: Some("127.0.0.1".parse().unwrap()),
        interval: Duration::from_millis(100),
        allow_loopback: true,
        interface: None,
        broadcast: false,
    }
}

impl Dev {
    pub fn fp(&self) -> String {
        self.net.me.fingerprint()
    }

    pub fn addr(&self) -> SocketAddr {
        format!("127.0.0.1:{}", self.net.port).parse().unwrap()
    }

    pub fn call(&self, method: &str, board: &str, token: &str, params: Value) -> Value {
        let mut r = json!({"v": 1, "id": 1, "method": method, "params": params});
        if !board.is_empty() {
            r["board"] = json!(board);
        }
        if !token.is_empty() {
            r["token"] = json!(token);
        }
        self.net.call(&r, &super::me())
    }

    pub fn ok(&self, method: &str, board: &str, token: &str, params: Value) -> Value {
        let r = self.call(method, board, token, params);
        assert_eq!(r["ok"], true, "{method}: {r}");
        r["result"].clone()
    }

    pub fn session(&self, board: &str, id: &str) -> (String, String) {
        let r = self.ok("register", board, "", json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": id}));
        (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
    }

    /// The test person grants the session ``name`` of ``board`` (``token`` is its own) the actions ``only`` names, or all of them.
    pub fn allow(&self, board: &str, token: &str, name: &str, only: &[&str]) {
        let mut call = |m: &str, b: &str, t: &str, p: Value| self.call(m, b, t, p);
        super::grants::enrolled(self.dir.path(), &mut call, token);
        super::grants::person().allow(&mut call, board, token, name, only);
    }

    pub fn say(&self, board: &str, token: &str, text: &str) {
        self.ok("post", board, token, json!({"kind": "message", "fields": {"type": "status", "body": text}}));
    }

    /// The bodies of the messages of `board`, in merged order.
    pub fn texts(&self, board: &str, token: &str) -> Vec<String> {
        let r = self.ok("read", board, token, json!({"limit": 1000}));
        r["entries"].as_array().unwrap().iter().filter(|e| e["kind"] == "message").map(|e| e["fields"]["body"].as_str().unwrap().to_string()).collect()
    }

    pub fn status(&self) -> Value {
        self.ok("pool_status", "", "", json!({}))
    }

    pub fn member(&self, fp: &str) -> Option<Value> {
        self.status()["members"].as_array().unwrap().iter().find(|m| m["fingerprint"] == fp).cloned()
    }

    pub fn is_member(&self, fp: &str) -> bool {
        self.member(fp).is_some_and(|m| m["status"] == "active")
    }

    pub fn pool(&self) -> String {
        self.status()["pool"].as_str().unwrap().to_string()
    }

    /// This device (in a pool of the test person's, founded here when it is in none) lists `other` as a member, as its owner could after
    /// joining it: members do not teach each other who is in the pool, so a test that needs two members that never joined one another
    /// says so here. When `other` is in no pool it joins this one with a code (and so learns its address); when it is in one, only
    /// this device lists it.
    pub fn know(&self, other: &Dev) {
        if self.is_member(&other.fp()) {
            return;
        }
        if other.node.lock().unwrap().members.is_unfounded() {
            self.join_with_code(other);
            return;
        }
        self.found_if_unfounded();
        let name = other.node.lock().unwrap().members.get(&other.fp()).map(|d| d.name.clone()).unwrap_or_default();
        self.node.lock().unwrap().enrol_device(&other.net.me.der, &name, "join-code").unwrap();
    }

    /// Found a secure pool for the test person here when this device is in none; a device that joined a pool of the test person's and
    /// holds no signer is given the person's, so it can admit in turn.
    pub fn found_if_unfounded(&self) {
        let (unfounded, cannot_admit) = {
            let n = self.node.lock().unwrap();
            (n.members.is_unfounded(), n.can_admit().is_err())
        };
        if unfounded {
            super::govnode::found(self, super::gov::person(super::govnode::ANN, "ann"), "secure");
        } else if cannot_admit {
            self.node.lock().unwrap().set_signer(super::gov::person(super::govnode::ANN, "ann"));
        }
    }

    /// Join `joiner` into this device's pool with a code (this device founds a secure pool of the test person first when it is in none).
    pub fn join_with_code(&self, joiner: &Dev) -> Value {
        self.found_if_unfounded();
        let code = self.ok("add_device", "", &self.token, json!({}))["code"].as_str().unwrap().to_string();
        joiner.ok("join_pool", "", &joiner.token, json!({"host": "127.0.0.1", "port": self.net.port, "passphrase": code}))
    }
}

/// Wait until `f` holds, or fail with `what`.
pub fn eventually(what: &str, secs: u64, mut f: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end {
        if f() {
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    panic!("timed out waiting for: {what}");
}
