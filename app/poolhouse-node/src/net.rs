//! The network side of the node: the one TLS listener, the beacon, the sync loop, and the local
//! API calls that reach other machines (`add_device`, `join_pool`, `sync_now`).
//!
//! The node's lock is never held across network I/O: a request takes it, decides, and lets go.

use std::collections::{BTreeMap, BTreeSet};
use std::ops::{Deref, DerefMut};
use crate::node::update_activity::{Registry, Source, Writer};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock, Weak};
use std::time::Duration;

use rustls::ServerConfig;
use serde_json::{json, Value};

use crate::api::{parse, respond, Call};
use crate::beacon::{self, Advert, Beacon, BeaconConfig, BeaconStats, Hooks};
use crate::cert::{board_fingerprint, Identity};
use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, unhex};
use crate::lineage::Peer;
use crate::membership::{Device, Standing};
use crate::netsync::sync_all;
use crate::node::Node;
use crate::join_code::{context_for, new_code, Session, Window};
use crate::peer::{self, PeerClient};
use crate::poolapi::{authorize_from, NETWORK};
use crate::poolops::wall_ms;
use crate::shard::{self, Shards};
use crate::tls::{server_config, StandingFn};

/// How long a device does not ask again a pool that refused it a join.
const REFUSED_FOR: Duration = Duration::from_secs(10);

/// How this node reaches other machines.
#[derive(Clone, Debug)]
pub struct NetConfig {
    pub listen: SocketAddr,
    pub beacon: Option<BeaconConfig>,
    /// How often to swap pool records and boards with every member; None for only on request.
    pub sync_every: Option<Duration>,
    /// The project key (`projectid::project_of`) of this device: while it is in no pool it joins an `open` pool of that project that
    /// it hears. A device never founds a pool by itself; founding is a person's approval. None leaves the device as it is.
    pub project: Option<String>,
}

impl NetConfig {
    /// Listen on every interface on ``port`` with the default beacon on ``beacon_port``.
    pub fn standard(port: u16, beacon_port: u16) -> NetConfig {
        NetConfig { listen: SocketAddr::from(([0, 0, 0, 0], port)), beacon: Some(BeaconConfig::multicast(beacon_port)), sync_every: Some(Duration::from_secs(20)), project: None }
    }
}

pub struct Net {
    pub node: Arc<Mutex<Node>>,
    pub(crate) activity: Registry,
    pub me: Identity,
    pub server_config: Arc<ServerConfig>,
    pub port: u16,
    pub stop: Arc<AtomicBool>,
    pub shards: Arc<Shards>,
    window: Mutex<Option<Window>>,
    joining: Mutex<BTreeSet<String>>,
    /// Devices whose pool refused this one a join lately, and when.
    refused: Mutex<BTreeMap<String, std::time::Instant>>,
    beacon_stats: OnceLock<Arc<BeaconStats>>,
    /// The last attempt to join a pool automatically, and how it ended.
    last_join: Mutex<Value>,
    /// Members that opened a connection to this device and keep it open for requests to go down (they cannot be dialed: they are behind NAT).
    reverse: Mutex<BTreeMap<String, Arc<Mutex<PeerClient>>>>,
    /// Members this device is keeping such a connection to, so each is attached once, with a handle that closes the connection.
    attaching: Mutex<BTreeMap<String, Option<TcpStream>>>,
    this: OnceLock<Weak<Net>>,
}

fn locked<T>(m: &Mutex<T>) -> Result<MutexGuard<'_, T>> {
    m.lock().map_err(|_| Error::Damaged("node poisoned".into()))
}

/// Node lock first, then the shared phase registry. The phase mutex is released before use.
pub(crate) struct NodeWriteGuard<'a> {
    node: MutexGuard<'a, Node>, // Drops first; writer accounting conservatively outlives the lock.
    _writer: Writer,
}
impl Deref for NodeWriteGuard<'_> { type Target=Node; fn deref(&self) -> &Node { &self.node } }
impl DerefMut for NodeWriteGuard<'_> { fn deref_mut(&mut self) -> &mut Node { &mut self.node } }
pub(crate) fn tracked_node(node: &Mutex<Node>, source: Source) -> Result<NodeWriteGuard<'_>> {
    let node=locked(node)?;
    let writer=node.activity.maintenance(source).map_err(activity_error)?;
    Ok(NodeWriteGuard { node, _writer:writer })
}
pub(crate) fn activity_error(_: crate::node::update_activity::Refusal) -> Error {
    Error::say("update_pending", "network activity waits for the node update", "none", "retry")
}

fn addr_of(host: &str, port: u64) -> Result<SocketAddr> {
    let port = u16::try_from(port).ok().filter(|p| *p > 0).ok_or_else(|| Error::Invalid("port is 1 to 65535".into()))?;
    (host, port).to_socket_addrs()?.next().ok_or_else(|| Error::Invalid("host does not resolve".into()))
}

/// ``base`` with the keys of ``more`` added (both are objects).
fn merged(mut base: Value, more: Value) -> Value {
    if let (Some(b), Value::Object(m)) = (base.as_object_mut(), more) {
        b.extend(m);
    }
    base
}

/// A join failure as a person should read it: what happened and what to do about it.
fn join_hint(e: &Error) -> String {
    let text = e.to_string();
    let blocked = matches!(e, Error::Io(io) if matches!(io.kind(), std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock | std::io::ErrorKind::PermissionDenied | std::io::ErrorKind::ConnectionRefused));
    if blocked {
        return format!("{text}: the other device did not accept the connection; allow this program through its firewall on the private network (macOS: Privacy & Security > Local Network)");
    }
    text
}

/// A dial's failure as the device that made it should read it: a TLS alert at the handshake is how a member refuses a device it put out of
/// the pool (or whose certificate it does not know), and that alert alone says nothing a person can act on.
fn refusal_hint(e: &Error) -> String {
    let text = e.to_string();
    if text.contains("received fatal alert") {
        return format!("{text}: the member refused this device's certificate at the handshake; it may have put this device out of the pool, so ask a member for the pool's list");
    }
    text
}

impl Net {
    /// Start listening (and beaconing and syncing, as configured) for ``node``.
    pub fn start(node: Arc<Mutex<Node>>, cfg: NetConfig) -> Result<Arc<Net>> {
        let (me, stop, dir, activity) = {
            let n = tracked_node(&node, Source::Network)?;
            (n.cert.clone(), n.stop.clone(), n.dir.clone(), n.activity.clone())
        };
        let shards = Shards::new(node.clone(), &dir, stop.clone())?;
        let standing_of = node.clone();
        let standing: StandingFn = Arc::new(move |fp| tracked_node(&standing_of, Source::Peer).map_or(Standing::Revoked, |n| n.members.standing(fp)));
        let listener = TcpListener::bind(cfg.listen)?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let net = Arc::new(Net {
            node, activity, server_config: server_config(&me, standing)?, me, port, stop, shards,
            window: Mutex::new(None), joining: Mutex::new(BTreeSet::new()), refused: Mutex::new(BTreeMap::new()), beacon_stats: OnceLock::new(), last_join: Mutex::new(Value::Null),
            reverse: Mutex::new(BTreeMap::new()), attaching: Mutex::new(BTreeMap::new()), this: OnceLock::new(),
        });
        let _ = net.this.set(Arc::downgrade(&net));
        tracked_node(&net.node, Source::Network)?.facts.listen = Some(listener.local_addr()?.to_string());
        let n = net.clone();
        std::thread::spawn(move || n.accept_loop(listener));
        if let Some(project) = cfg.project.as_deref().filter(|_| cfg.beacon.is_some()) {
            tracked_node(&net.node, Source::Network)?.members.claim_project(project);
        }
        if let Some(b) = cfg.beacon {
            net.start_beacon(b)?;
        }
        if let Some(every) = cfg.sync_every {
            let n = net.clone();
            std::thread::spawn(move || n.sync_loop(every));
        }
        Ok(net)
    }

    pub fn shutdown(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    fn accept_loop(self: Arc<Net>, listener: TcpListener) {
        while !self.stop.load(Ordering::SeqCst) {
            match listener.accept() {
                Ok((tcp, _)) => {
                    let _ = tcp.set_nonblocking(false);
                    let n = self.clone();
                    std::thread::spawn(move || {
                        let _ = peer::serve(&n, tcp);
                    });
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(10)),
                Err(_) => std::thread::sleep(Duration::from_millis(100)),
            }
        }
    }

    pub fn session_opened(&self, fp: &str) {
        if let Ok(mut n) = tracked_node(&self.node, Source::Network) {
            if n.members.is_active(fp) {
                let f = n.facts.peer(fp);
                f.sessions += 1;
                f.last_seen_ms = wall_ms();
            }
        }
    }

    pub fn session_closed(&self, fp: &str) {
        if let Ok(mut n) = tracked_node(&self.node, Source::Network) {
            if let Some(f) = n.facts.peers.get_mut(fp) {
                f.sessions = f.sessions.saturating_sub(1);
            }
        }
    }

    // -- beacon -----------------------------------------------------------
    fn start_beacon(self: &Arc<Net>, cfg: BeaconConfig) -> Result<()> {
        let key = tracked_node(&self.node, Source::Network)?.key.clone();
        let (a, b) = (self.clone(), self.clone());
        let hooks = Hooks {
            identity: Box::new(move || {
                tracked_node(&a.node, Source::Beacon).map(|n| Advert {
                    pool: n.members.id.clone(), fingerprint: n.cert.fingerprint(), port: a.port,
                    project: n.members.project.clone(),
                }).unwrap_or_default()
            }),
            on_beacon: Box::new(move |beacon| b.heard(beacon)),
        };
        tracked_node(&self.node, Source::Network)?.facts.beacon = true;
        let stats = beacon::start(cfg, key, hooks, self.stop.clone(), wall_ms)?;
        let _ = self.beacon_stats.set(stats);
        Ok(())
    }

    /// What the beacon has sent and heard, and how the last automatic join ended.
    fn network_facts(&self) -> Value {
        let count = |f: fn(&BeaconStats) -> &std::sync::atomic::AtomicU64| self.beacon_stats.get().map_or(0, |s| f(s).load(Ordering::Relaxed));
        let text = |f: fn(&BeaconStats) -> &Mutex<String>| self.beacon_stats.get().and_then(|s| f(s).lock().ok().map(|t| t.clone())).unwrap_or_default();
        json!({
            "beacon_sent": count(|s| &s.sent), "beacon_send_errors": count(|s| &s.send_errors), "beacon_heard": count(|s| &s.heard),
            "beacon_own": count(|s| &s.own), "beacon_rejected": count(|s| &s.rejected),
            "beacon_last_send_error": text(|s| &s.last_send_error), "beacon_last_reject": text(|s| &s.last_reject),
            "last_join": self.last_join.lock().map(|j| j.clone()).unwrap_or(Value::Null),
        })
    }

    fn record_join(&self, fingerprint: &str, addr: SocketAddr, result: &Result<()>) {
        let Ok(_activity) = self.activity.maintenance(Source::Network) else { return };
        let (ok, error) = match result {
            Ok(()) => (true, String::new()),
            Err(e) => (false, join_hint(e)),
        };
        if let Ok(mut j) = self.last_join.lock() {
            *j = json!({"fingerprint": fingerprint, "addr": addr.to_string(), "ok": ok, "error": error, "at": wall_ms()});
        }
    }

    /// A valid beacon was heard. A member's address is remembered; a non-member is answered only by a device in no pool yet, only when
    /// the sender's pool is founded (has an id) and is of this device's project, and not again for ten seconds after a refusal (a
    /// pool that is not `open` refuses it). Two devices in no pool never join each other, and a device in a pool joins no other.
    fn heard(self: &Arc<Net>, b: Beacon) {
        let Ok(mut node) = tracked_node(&self.node, Source::Beacon) else { return };
        if b.fingerprint == node.cert.fingerprint() {
            return;
        }
        let addr = SocketAddr::new(b.addr, b.port);
        match node.members.standing(&b.fingerprint) {
            Standing::Revoked => {}
            Standing::Active => {
                let proven = node.members.get(&b.fingerprint).and_then(Device::der).and_then(|d| board_fingerprint(&d).ok()) == Some(sha256_hex(&b.public));
                if proven && b.pool == node.members.id {
                    let _ = node.facts.set_addr(&b.fingerprint, &addr.to_string());
                    node.facts.peer(&b.fingerprint).last_seen_ms = wall_ms();
                }
            }
            Standing::Unknown => {
                let eligible = node.members.can_join_other() && b.pool.len() == 32 && b.project == node.members.project && crate::netif::on_segment(b.addr);
                drop(node);
                let waiting = self.refused.lock().is_ok_and(|r| r.get(&b.fingerprint).is_some_and(|at| at.elapsed() < REFUSED_FOR));
                let admitted = self.activity.new_effect(Source::Beacon);
                if eligible && !waiting && admitted.is_ok() && self.joining.lock().is_ok_and(|mut j| j.insert(b.fingerprint.clone())) {
                    let admitted = admitted.expect("checked admission");
                    let (net, fp) = (self.clone(), b.fingerprint.clone());
                    std::thread::spawn(move || {
                        let _admitted = admitted; // Owned before spawn; retained through result and cleanup.
                        let result = net.join_open(addr, &fp);
                        net.record_join(&fp, addr, &result);
                        if result.is_err() {
                            if let Ok(mut r) = net.refused.lock() {
                                r.insert(fp.clone(), std::time::Instant::now());
                            }
                        }
                        if let Ok(mut j) = net.joining.lock() {
                            j.remove(&fp);
                        }
                    });
                }
            }
        }
    }

    // -- joining ----------------------------------------------------------
    /// Join the pool of the device at ``addr`` whose certificate has fingerprint ``expect``,
    /// under its policy `open`; the device is enrolled here and this one there.
    pub fn join_open(&self, addr: SocketAddr, expect: &str) -> Result<()> {
        let _effect = self.activity.new_effect(Source::Network).map_err(activity_error)?;
        if !crate::netif::on_segment(addr.ip()) {
            return Err(Error::Denied(format!("{} is not on a network this device is on; a device joins without a code only from the same network, so use a join code", addr.ip())));
        }
        let mut c = PeerClient::connect(&self.me, addr, Some(expect))?;
        let name = tracked_node(&self.node, Source::Network)?.members.get(&self.me.fingerprint()).map(|d| d.name.clone()).unwrap_or_default();
        let ask = peer::hello_request(&*tracked_node(&self.node, Source::Network)?);
        let hello = c.call(&ask)?;
        tracked_node(&self.node, Source::Network)?.observe_build(&c.fingerprint, &hello);
        if hello["project"].as_str().unwrap_or("") != tracked_node(&self.node, Source::Network)?.members.project {
            return Err(Error::Denied("that pool belongs to another project".into()));
        }
        if hello["policy"] != "open" {
            return Err(Error::Denied("that pool does not take a device without a code".into()));
        }
        let reply = c.call(&json!({"op": "join_open", "name": name, "port": self.port}))?;
        self.adopt(&c, &reply, addr)
    }

    /// Take the pool a device's reply describes, when this device has joined it: this device becomes a member of it (a device in no
    /// pool yet), or takes the acts the reply holds into the pool it is already in.
    fn adopt(&self, c: &PeerClient, reply: &Value, addr: SocketAddr) -> Result<()> {
        let read = |key: &str| -> Result<Vec<crate::gov::act::Act>> { serde_json::from_value(reply[key].clone()).map_err(|_| Error::Invalid(format!("{key} is a list of acts"))) };
        let found: crate::gov::act::Act = serde_json::from_value(reply["found"].clone()).map_err(|_| Error::Invalid("found is an act".into()))?;
        let (settled, delegates, rest) = (read("settled")?, read("delegates")?, read("acts")?);
        let mut node = tracked_node(&self.node, Source::Network)?;
        if reply["pool"].as_str() != Some(found.pool.as_str()) {
            return Err(Error::Denied("the pool the device named is not the pool its founding act makes".into()));
        }
        if node.members.gov().is_some_and(|g| g.id == found.pool) {
            node.gov_ingest_many(delegates.into_iter().chain(rest).collect(), false)?;
        } else {
            node.adopt_governed(found, settled, delegates, rest)?;
        }
        node.facts.set_addr(&c.fingerprint, &addr.to_string())?;
        node.facts.peer(&c.fingerprint).last_seen_ms = wall_ms();
        Ok(())
    }

    // -- join code ----------------------------------------------------------
    /// The server half of the join-code exchange, run under the node lock by `peer::dispatch`.
    pub fn join_code_step(&self, node: &mut Node, der: &[u8], op: &str, req: &Value) -> Result<Value> {
        let fp = crate::cert::cert_fingerprint(der);
        let mut window = locked(&self.window)?;
        let w = window.as_mut().filter(|w| w.open(wall_ms())).ok_or_else(|| Error::Denied("this device is not accepting a join code".into()))?;
        if op == "join_exchange" {
            let nonce = req.get("nonce").and_then(Value::as_str).filter(|n| n.len() <= 64).ok_or_else(|| Error::Invalid("a nonce is sent".into()))?;
            let name = req.get("name").and_then(Value::as_str).unwrap_or("").to_string();
            let mut s = Session::start(false, &w.code, &context_for(nonce), &node.cert.fingerprint(), &fp);
            if let Err(e) = s.receive(req.get("message").and_then(Value::as_str).unwrap_or("")) {
                w.failed();
                return Err(e);
            }
            let message = s.message();
            w.session = Some((fp, name, s));
            return Ok(json!({"message": message}));
        }
        let Some((_, name, session)) = w.session.take().filter(|(who, _, _)| *who == fp) else {
            return Err(Error::Denied("no exchange is open for this device".into()));
        };
        if !session.check(req.get("confirmation").and_then(Value::as_str).unwrap_or("")) {
            let left = w.failed();
            if !left {
                *window = None;
                node.facts.join_code_until_ms = 0;
            }
            return Err(Error::Denied(if left { "wrong code".into() } else { "wrong code; the join-code window is closed".into() }));
        }
        peer::record_json(node, "")?;
        node.enrol_device(der, &name, "join-code")?;
        let mut grant = peer::record_json(node, &fp)?;
        grant["v"] = json!(1);
        let grant = grant.to_string().into_bytes();
        *window = None;
        node.facts.join_code_until_ms = 0;
        Ok(json!({"confirmation": session.confirmation()?, "grant": crate::fsutil::hex(&grant), "tag": session.seal(&grant)?}))
    }

    /// Open a join-code window: returns the code (``passphrase`` when given) and seconds it lasts. ``presigned``: the admit of the
    /// device that will use the window was signed (and submitted here) before, so this device needs no signer to open it; the window
    /// still admits nothing a person's key did not sign for.
    pub fn add_device(&self, passphrase: &str, ttl_s: u64, presigned: bool) -> Result<Value> {
        let _effect = self.activity.new_effect(Source::Network).map_err(activity_error)?;
        let code = if passphrase.is_empty() { new_code()? } else { passphrase.to_string() };
        if code.len() < 6 || code.len() > 200 {
            return Err(Error::Invalid("a passphrase is 6 to 200 characters".into()));
        }
        let until = wall_ms() + ttl_s.clamp(1, 600) * 1000;
        let mut node = tracked_node(&self.node, Source::Network)?;
        if !presigned {
            node.can_admit()?;
        }
        *locked(&self.window)? = Some(Window::new(&code, until));
        node.facts.join_code_until_ms = until;
        Ok(json!({"code": code, "expires_in_s": ttl_s.clamp(1, 600), "port": self.port, "fingerprint": self.me.fingerprint()}))
    }

    /// Join the device at ``addr`` using its code (without one, join its pool under policy
    /// `open`, which needs its certificate fingerprint); this device must be alone in its pool.
    pub fn join_pool(&self, addr: SocketAddr, passphrase: &str, fingerprint: &str) -> Result<Value> {
        let _effect = self.activity.new_effect(Source::Network).map_err(activity_error)?;
        let (alone, name) = {
            let n = tracked_node(&self.node, Source::Network)?;
            (n.members.can_join_other(), n.members.get(&self.me.fingerprint()).map(|d| d.name.clone()).unwrap_or_default())
        };
        if !alone {
            return Err(Error::Denied("a device that is in a pool does not change pool".into()));
        }
        if passphrase.is_empty() {
            if !crate::cert::valid_fingerprint(fingerprint) {
                return Err(Error::Invalid("without a passphrase the device is named by its fingerprint".into()));
            }
            self.join_open(addr, fingerprint)?;
            return Ok(json!({"pool": tracked_node(&self.node, Source::Network)?.members.id, "peer": fingerprint}));
        }
        let mut c = PeerClient::connect(&self.me, addr, None)?;
        let nonce = crate::fsutil::random_hex(16)?;
        let mut s = Session::start(true, passphrase, &context_for(&nonce), &self.me.fingerprint(), &c.fingerprint);
        let reply = c.call(&json!({"op": "join_exchange", "message": s.message(), "nonce": nonce, "name": name}))?;
        s.receive(reply["message"].as_str().unwrap_or(""))?;
        let reply = c.call(&json!({"op": "join_confirm", "confirmation": s.confirmation()?, "port": self.port}))?;
        if !s.check(reply["confirmation"].as_str().unwrap_or("")) {
            return Err(Error::Denied("the device did not prove it knew the code".into()));
        }
        let grant = reply["grant"].as_str().and_then(unhex).ok_or_else(|| Error::Invalid("the grant is hex".into()))?;
        if !s.open(&grant, reply["tag"].as_str().unwrap_or("")) {
            return Err(Error::Denied("the grant does not carry the exchange's tag".into()));
        }
        let grant: Value = serde_json::from_slice(&grant)?;
        self.adopt(&c, &grant, addr)?;
        let ask = peer::hello_request(&*tracked_node(&self.node, Source::Network)?);
        if let Ok(hello) = c.call(&ask) {
            tracked_node(&self.node, Source::Network)?.observe_build(&c.fingerprint, &hello);
        }
        Ok(json!({"pool": grant["pool"], "peer": c.fingerprint}))
    }

    // -- sync -------------------------------------------------------------
    /// Swap pool records and boards with every active member we can reach; the number reached. A member whose last dials failed is left
    /// alone until its backoff ends (a second, then twice as long, up to a minute) unless ``force``: a device nothing can reach does not
    /// hold up the members that can be reached. A member put out of the pool while the cycle runs (heard from an earlier dial of it) is
    /// not dialed, and one put out during its dial is not recorded as reached or attached to.
    pub fn sync_once(&self, force: bool) -> Result<usize> {
        let _effect = self.activity.new_effect(Source::Sync).map_err(activity_error)?;
        let targets: Vec<(String, String)> = {
            let mut n = tracked_node(&self.node, Source::Network)?;
            n.gov_tick()?;
            let me = self.me.fingerprint();
            let now = wall_ms();
            n.members.active().into_iter().filter(|d| d.fingerprint != me)
                .filter(|d| force || n.facts.peers.get(&d.fingerprint).is_none_or(|f| f.retry_at_ms <= now))
                .filter_map(|d| n.facts.peers.get(&d.fingerprint).and_then(|f| f.addr.clone()).map(|a| (d.fingerprint, a))).collect()
        };
        let mut reached = 0;
        for (fp, addr) in targets {
            if !tracked_node(&self.node, Source::Network)?.members.is_active(&fp) {
                continue;
            }
            let result = addr.parse::<SocketAddr>().map_err(|e| Error::Invalid(e.to_string()))
                .and_then(|a| PeerClient::connect(&self.me, a, Some(&fp)))
                .and_then(|mut c| self.compatible(&mut c).and_then(|()| sync_all(&self.node, &mut c)));
            let mut n = tracked_node(&self.node, Source::Network)?;
            if !n.members.is_active(&fp) {
                continue;
            }
            let f = n.facts.peer(&fp);
            match result {
                Ok(reports) => {
                    (f.last_sync_ms, f.last_seen_ms, f.last_error, f.fails, f.retry_at_ms) = (wall_ms(), wall_ms(), crate::netsync::refused_note(&reports), 0, 0);
                    reached += 1;
                    drop(n);
                    if let Ok(a) = addr.parse::<SocketAddr>() {
                        self.attach(&fp, a);
                    }
                }
                Err(e) => {
                    f.fails = f.fails.saturating_add(1);
                    f.retry_at_ms = wall_ms() + (1000u64 << f.fails.min(6)).min(60_000);
                    f.last_error = refusal_hint(&e);
                }
            }
        }
        self.drop_removed();
        Ok(reached)
    }

    /// Keep one connection open to ``fp`` for it to send this device requests down, unless one is already kept: this device may be behind
    /// NAT, where the member cannot dial it but this device can dial the member. Returns the thread that keeps it, None when one is kept.
    pub fn attach(&self, fp: &str, addr: SocketAddr) -> Option<std::thread::JoinHandle<()>> {
        let net = self.this.get().and_then(Weak::upgrade)?;
        let admitted = self.activity.new_effect(Source::Network).ok()?;
        let mut kept = locked(&self.attaching).ok()?;
        if kept.contains_key(fp) {
            return None;
        }
        kept.insert(fp.to_string(), None);
        drop(kept);
        let fp = fp.to_string();
        Some(std::thread::spawn(move || {
            let _ = net.attach_run(&fp, addr, admitted);
            let Ok(_cleanup) = net.activity.maintenance(Source::Network) else { return };
            if let Ok(mut kept) = locked(&net.attaching) {
                kept.remove(&fp);
            }
        }))
    }

    fn attach_run(&self, fp: &str, addr: SocketAddr, admitted: Writer) -> Result<()> {
        if !self.is_member(fp) {
            return Ok(());
        }
        let mut c = PeerClient::connect_long(&self.me, addr, Some(fp))?;
        if let Some(slot) = locked(&self.attaching)?.get_mut(fp) {
            *slot = c.closer();
        }
        c.call(&json!({"op": "attach"}))?;
        self.session_opened(fp);
        drop(admitted);
        let ended = c.serve(self, addr.ip());
        self.session_closed(fp);
        ended.map(|_| ())
    }

    fn is_member(&self, fp: &str) -> bool {
        tracked_node(&self.node, Source::Network).is_ok_and(|n| n.members.is_active(fp))
    }

    /// Close the connections kept with devices this one no longer lists as active members: the ones it keeps open to a member (`attach`)
    /// and the ones a member opened to it.
    pub fn drop_removed(&self) {
        let Ok(_activity) = self.activity.maintenance(Source::Network) else { return };
        let mut held: BTreeSet<String> = self.attaching.lock().map(|m| m.keys().cloned().collect()).unwrap_or_default();
        held.extend(self.reverse.lock().map(|m| m.keys().cloned().collect::<Vec<_>>()).unwrap_or_default());
        let Ok(node) = tracked_node(&self.node, Source::Network) else { return };
        let gone: Vec<String> = held.into_iter().filter(|fp| !node.members.is_active(fp)).collect();
        drop(node);
        for fp in gone {
            if let Ok(kept) = self.attaching.lock() {
                if let Some(Some(stream)) = kept.get(&fp) {
                    let _ = stream.shutdown(Shutdown::Both);
                }
            }
            let link = self.reverse.lock().ok().and_then(|m| m.get(&fp).cloned());
            if let Some(link) = link {
                self.drop_reverse(&fp, &link);
            }
        }
    }

    /// Hold the connection ``client`` (a member's, opened to this device) as the way to send that member requests; one from a device that is
    /// not an active member is closed.
    pub fn register_reverse(&self, client: PeerClient) {
        let Ok(_activity) = self.activity.maintenance(Source::Network) else { return };
        let fp = client.fingerprint.clone();
        if !self.is_member(&fp) {
            self.session_closed(&fp);
            return;
        }
        let replaced = self.reverse.lock().ok().and_then(|mut m| m.insert(fp.clone(), Arc::new(Mutex::new(client))));
        if replaced.is_some() {
            self.session_closed(&fp);
        }
        self.mark_channel(&fp, true);
    }

    /// Record in the peer's facts whether it keeps a connection open to this device (`pool_status` says `channel`).
    fn mark_channel(&self, fp: &str, open: bool) {
        let Ok(_activity) = self.activity.maintenance(Source::Network) else { return };
        if let Ok(mut n) = tracked_node(&self.node, Source::Network) {
            n.facts.peer(fp).channel = open;
        }
    }

    fn drop_reverse(&self, fp: &str, link: &Arc<Mutex<PeerClient>>) {
        let Ok(_activity) = self.activity.maintenance(Source::Network) else { return };
        let removed = self.reverse.lock().ok().is_some_and(|mut m| m.get(fp).is_some_and(|l| Arc::ptr_eq(l, link)) && m.remove(fp).is_some());
        if removed {
            self.session_closed(fp);
            self.mark_channel(fp, false);
        }
    }

    /// Send one request to the member ``fp``: down the connection it opened to this device when it keeps one (it cannot be dialed), else
    /// to its address.
    pub fn call_member(&self, fp: &str, addr: Option<SocketAddr>, request: &Value) -> Result<Value> {
        let _activity = if matches!(request["op"].as_str(), Some("shard_caps" | "shard_status" | "shard_cancel" | "hello" | "boards" | "vector" | "pull")) {
            self.activity.maintenance(Source::Network)
        } else { self.activity.new_effect(Source::Network) }.map_err(activity_error)?;
        let link = locked(&self.reverse)?.get(fp).cloned();
        if let Some(link) = link {
            if !tracked_node(&self.node, Source::Network)?.members.is_active(fp) {
                self.drop_reverse(fp, &link);
                return Err(Error::Denied("this device was put out of the pool".into()));
            }
            let sent = locked(&link)?.call(request);
            match sent {
                Err(Error::Io(_)) => self.drop_reverse(fp, &link),
                Err(Error::Denied(m)) if m == "the peer closed the connection" => self.drop_reverse(fp, &link),
                other => return other,
            }
        }
        let addr = addr.ok_or_else(|| Error::Invalid("this device has no address for it and it has not connected to this device: a device behind NAT cannot be dialed, it connects to the members whose address it has; wait for its next sync, or run `sync_now` on it".into()))?;
        let mut c = PeerClient::connect(&self.me, addr, Some(fp)).map_err(|e| match e {
            Error::Io(io) => Error::Invalid(format!("{io} at {addr}, and it has not connected to this device: a device behind NAT cannot be dialed, it connects to the members whose address it has; wait for its next sync")),
            other => other,
        })?;
        c.call(request)
    }

    /// Ask the peer for its build and remember it; refuse to sync with a peer on another wire version, saying how to fix it.
    fn compatible(&self, c: &mut PeerClient) -> Result<()> {
        let ask = peer::hello_request(&*tracked_node(&self.node, Source::Network)?);
        let hello = c.call(&ask)?;
        let mut node = tracked_node(&self.node, Source::Network)?;
        node.observe_build(&c.fingerprint, &hello);
        let theirs = crate::build::Seen::read(&hello);
        if theirs.wire != node.build.wire {
            let name = node.members.get(&c.fingerprint).map(|d| d.name.clone()).unwrap_or_default();
            return Err(Error::Denied(format!("needs update: {}", crate::build::fix_for(&node.build, &theirs, &name))));
        }
        Ok(())
    }

    fn sync_loop(self: Arc<Net>, every: Duration) {
        while !self.stop.load(Ordering::SeqCst) {
            let _ = self.sync_once(false);
            let mut waited = Duration::ZERO;
            while waited < every && !self.stop.load(Ordering::SeqCst) {
                std::thread::sleep(Duration::from_millis(50));
                waited += Duration::from_millis(50);
            }
        }
    }

    // -- the local API ----------------------------------------------------
    /// Answer a local API request: the network methods here, the rest by the node under its lock.
    pub fn call(&self, request: &Value, peer: &Peer) -> Value {
        let parsed = parse(request);
        match parsed {
            Ok(c) if NETWORK.contains(&c.method.as_str()) => respond(request, self.network(&c, peer)),
            Ok(c) if c.method == "pool_status" => match tracked_node(&self.node, Source::Network) {
                Ok(mut n) => respond(request, n.gov_tick().map(|_| merged(n.pool_status(), self.network_facts()))),
                Err(error) => respond(request, Err(error)),
            },
            _ => {
                let reply = match tracked_node(&self.node, Source::Network) {
                    Ok(mut n) => crate::api::handle(&mut n, request, peer),
                    Err(error) => respond(request, Err(error)),
                };
                if request["method"] == "member_remove" {
                    self.drop_removed();
                }
                reply
            }
        }
    }

    fn network(&self, c: &Call, peer: &Peer) -> Result<Value> {
        let text = |k: &str| c.params.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let number = |k: &str, d: u64| c.params.get(k).and_then(Value::as_u64).unwrap_or(d);
        if c.method != "shard_call" {
            let resource = if c.method == "join_pool" { text("host") } else { "*".into() };
            let mut node = tracked_node(&self.node, Source::Network)?;
            node.gov_tick()?;
            authorize_from(&mut node, &c.token, peer, &c.method, &resource)?;
        }
        match c.method.as_str() {
            "add_device" => self.add_device(&text("passphrase"), number("ttl_s", 120), c.params.get("presigned") == Some(&Value::Bool(true))),
            "join_pool" => self.join_pool(addr_of(&text("host"), number("port", 0))?, &text("passphrase"), &text("fingerprint")),
            "shard_call" => self.shard_call(c, peer),
            _ => Ok(json!({"reached": self.sync_once(true)?})),
        }
    }

    // -- test shards --------------------------------------------------------
    /// Pass one shard op to the pool member named by ``device`` (a name or a fingerprint). The
    /// node adds `op` and `by` (the session behind the token); the peer decides from the
    /// certificate this device shows, so neither the session nor an argument can claim another.
    fn shard_call(&self, c: &Call, peer: &Peer) -> Result<Value> {
        let name = c.params.get("device").and_then(Value::as_str).unwrap_or("");
        let op = c.params.get("op").and_then(Value::as_str).unwrap_or("");
        let mut request = match c.params.get("args") {
            None => serde_json::Map::new(),
            Some(Value::Object(a)) => a.clone(),
            Some(_) => return Err(Error::Invalid("args is an object".into())),
        };
        if !shard::OPS.contains(&op) {
            return Err(Error::Invalid(format!("op is one of {}", shard::OPS.join(", "))));
        }
        shard::host::passable(&request)?;
        let (fp, addr, by) = {
            let mut node = tracked_node(&self.node, Source::Network)?;
            let who = authorize_from(&mut node, &c.token, peer, "shard_call", name)?;
            let (fp, addr) = self.device_named(&node, name)?;
            (fp, addr, format!("{}/{}", who.board, who.name))
        };
        request.insert("op".into(), json!(op));
        request.insert("by".into(), json!(by));
        self.call_member(&fp, addr, &Value::Object(request))
    }

    /// The active member other than this device that ``name`` names, and where it was last reached.
    fn device_named(&self, node: &Node, name: &str) -> Result<(String, Option<SocketAddr>)> {
        let me = self.me.fingerprint();
        let found: Vec<Device> = node.members.active().into_iter().filter(|d| d.fingerprint != me && (d.fingerprint == name || d.name == name)).collect();
        let device = match found.as_slice() {
            [one] => one,
            [] => return Err(Error::Invalid(format!("no other active device of this pool is called {}", name.chars().take(60).collect::<String>()))),
            _ => return Err(Error::Invalid("two devices have that name: name one by its fingerprint".into())),
        };
        let addr = node.facts.peers.get(&device.fingerprint).and_then(|f| f.addr.as_deref()).and_then(|a| a.parse().ok());
        Ok((device.fingerprint.clone(), addr))
    }
}

#[cfg(test)]
mod activity_tests {
    use super::*;
    struct Running { net: Arc<Net>, _directory: tempfile::TempDir }
    impl Drop for Running { fn drop(&mut self) { self.net.shutdown(); } }
    fn running() -> Running {
        let directory=tempfile::tempdir().unwrap();
        let node=Arc::new(Mutex::new(Node::open(directory.path()).unwrap()));
        let net=Net::start(node,NetConfig { listen:"127.0.0.1:0".parse().unwrap(),
            beacon:None,sync_every:None,project:None }).unwrap();
        Running { net, _directory:directory }
    }
    #[test]
    fn tracked_node_counts_actual_mutation_guard_and_releases_on_drop() {
        let dir=tempfile::tempdir().unwrap(); let node=Mutex::new(Node::open(dir.path()).unwrap());
        let registry=node.lock().unwrap().activity.clone();
        let mut held=tracked_node(&node,Source::Sync).unwrap();
        assert_eq!(registry.active_writer_count_for_test(),1);
        held.facts.listen=Some("127.0.0.1:1".into());
        assert!(node.try_lock().is_err()); drop(held);
        assert_eq!(registry.active_writer_count_for_test(),0); assert!(node.try_lock().is_ok());
    }
    #[test]
    fn real_loopback_peer_reads_and_error_cleanup_survive_work_drain_without_stop() {
        let owned=running(); let net=&owned.net;
        let caller_dir=tempfile::tempdir().unwrap(); let caller=Node::open(caller_dir.path()).unwrap();
        let addr=SocketAddr::from(([127,0,0,1],net.port));
        let mut peer=PeerClient::connect(&caller.cert,addr,Some(&net.me.fingerprint())).unwrap();
        assert_eq!(peer.call(&json!({"op":"hello"})).unwrap()["fingerprint"],net.me.fingerprint());
        net.activity.drain_for_test();
        assert!(!net.stop.load(Ordering::SeqCst));
        assert!(net.sync_once(true).is_err(),"a new outgoing sync round must not start");
        let status=net.call(&json!({"v":1,"id":1,"method":"pool_status","params":{}}),&Peer::none());
        assert_eq!(status["ok"],true,"{status}");
        assert!(peer.call(&json!({"op":"hello"})).is_ok(),"incoming read continues");
        assert!(peer.call(&json!({"op":"unknown"})).is_err());
        let deadline=std::time::Instant::now()+Duration::from_secs(2);
        while net.activity.active_writer_count_for_test()!=0 && std::time::Instant::now()<deadline { std::thread::yield_now(); }
        assert_eq!(net.activity.active_writer_count_for_test(),0,"idle peer is not active work");
        assert!(!net.stop.load(Ordering::SeqCst));
    }
}
