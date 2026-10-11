//! The peer protocol over a TLS 1.3 connection: length-prefixed JSON requests, one reply each,
//! any number on one connection.
//!
//! Who is asking is the certificate shown in the handshake and nothing in the request. Every
//! request is checked against the pool record when it arrives, not when the connection was made,
//! so a device put out is refused at its next request on a connection it already holds.
//!
//! | op | who | what |
//! |---|---|---|
//! | `hello` | anyone not revoked | pool id, join policy, fingerprints, build and wire version, how the device is attached to the network (`network`); the boards held, to members |
//! | `join_open` | anyone, under policy `open`; a member gets the record again | enrol the caller; reply with the pool record |
//! | `join_exchange`, `join_confirm` | anyone, while a join code is open | SPAKE2 (`join_code`) |
//! | `members` | members | swap the builds members report; no row and no policy in it is read |
//! | `boards`, `vector`, `pull`, `push` | members | board sync (`netsync`) |
//! | `shard_caps`, `shard_put`, `shard_start`, `shard_status`, `shard_cancel` | members | test shards (`shard`) |

use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream};
use std::time::Duration;

use serde_json::{json, Value};

use crate::board::Tip;
use crate::cert::{board_fingerprint, cert_fingerprint, Identity};
use crate::error::{Error, Result};
use crate::membership::{Policy, Standing};
use crate::net::Net;
use crate::node::Node;
use crate::row::{valid_origin, valid_name, Row};
use crate::sync::{rows_since, seqs, take, Report};
use crate::tls::{self, Secured};
use crate::wire::{read_frame_max, write_frame_max, MAX_PEER_FRAME};

pub const IO_TIMEOUT: Duration = Duration::from_secs(30);

fn text<'a>(req: &'a Value, key: &str) -> Result<&'a str> {
    req.get(key).and_then(Value::as_str).ok_or_else(|| Error::Invalid(format!("{key} is text")))
}

fn vector_of(req: &Value, key: &str) -> Result<BTreeMap<String, Tip>> {
    serde_json::from_value(req.get(key).cloned().unwrap_or(json!({}))).map_err(|_| Error::Invalid(format!("{key} maps origins to tips")))
}

fn reply_of(result: Result<Value>) -> Value {
    match result {
        Ok(v) => json!({"ok": true, "result": v}),
        Err(e) => json!({"ok": false, "error": {"code": e.code(), "message": e.to_string()}}),
    }
}

/// The policy attribute as it is handed to a device that joins.
pub fn policy_json(node: &Node) -> Value {
    json!({"policy": node.members.policy.name(), "at": node.members.policy_at, "by": node.members.policy_by})
}

/// The policy a joining device is handed, read back: the policy, when it was set and by whom; None when it is not all there.
pub fn policy_of(v: &Value) -> Option<(Policy, u64, String)> {
    let policy = Policy::parse(v.get("policy")?.as_str()?).ok()?;
    Some((policy, v.get("at")?.as_u64()?, v.get("by")?.as_str()?.chars().take(80).collect()))
}

fn hello(node: &Node, standing: Standing) -> Value {
    let boards: Vec<&String> = if standing == Standing::Active { node.boards.keys().collect() } else { Vec::new() };
    let mut out = json!({"pool": node.members.id, "policy": node.members.policy.name(), "fingerprint": node.cert.fingerprint(), "project": node.members.project,
           "board_fingerprint": crate::device::fingerprint(&node.key), "member": standing == Standing::Active, "boards": boards, "network": node.network});
    out.as_object_mut().expect("an object").extend(node.build.json().as_object().cloned().unwrap_or_default());
    out
}

/// The hello a device sends: its own build, so the peer learns it too.
pub fn hello_request(node: &Node) -> Value {
    let mut out = json!({"op": "hello", "network": node.network});
    out.as_object_mut().expect("an object").extend(node.build.json().as_object().cloned().unwrap_or_default());
    out
}

/// What a device that has just joined is handed: the pool's id, its record and its policy.
pub fn record_json(node: &Node) -> Value {
    json!({"pool": node.members.id, "rows": node.members.export(), "policy": policy_json(node)})
}

/// A device that asks to join under `open`: it is enrolled and handed the pool's record. A device that is a member already is handed
/// the record again and nothing else changes.
fn join_open(node: &mut Node, der: &[u8], req: &Value, ip: IpAddr, standing: Standing) -> Result<Value> {
    if standing == Standing::Active {
        return Ok(record_json(node));
    }
    if node.members.policy != Policy::Open {
        return Err(Error::Denied("this pool takes a device only through a join code".into()));
    }
    if !crate::netif::on_segment(ip) {
        return Err(Error::Denied("this pool takes a device without a code only from its own network".into()));
    }
    let name = req.get("name").and_then(Value::as_str).unwrap_or("");
    node.enrol_device(der, name, "open")?;
    Ok(record_json(node))
}

/// The builds members report. The rows and the policy a member sends here are not read: who is in the pool and how it is joined are
/// changed by this device's own API and by the joins it takes part in, and by nothing a member says.
fn members(node: &mut Node, fp: &str, req: &Value) -> Result<Value> {
    if req.get("pool").and_then(Value::as_str) != Some(node.members.id.as_str()) {
        return Err(Error::Denied("this is not the same pool".into()));
    }
    if let Some(builds) = req.get("builds") {
        node.take_builds(fp, builds);
    }
    Ok(json!({"pool": node.members.id, "builds": node.builds_known()}))
}

fn hosted<'a>(node: &'a mut Node, req: &Value, bfp: &str) -> Result<&'a mut crate::node::Hosted> {
    let (id, origin) = (text(req, "board")?, text(req, "origin")?);
    if !valid_name(id) || !valid_origin(origin) {
        return Err(Error::Invalid("a board and an origin are named".into()));
    }
    let h = node.boards.get_mut(id).ok_or_else(|| Error::Denied("this device does not hold that board".into()))?;
    if !h.board.bind_peer(bfp, origin)? {
        return Err(Error::Denied("this device writes a different log than the one it presented before".into()));
    }
    Ok(h)
}

fn pull(node: &mut Node, bfp: &str, req: &Value) -> Result<Value> {
    let vector = vector_of(req, "vector")?;
    let h = hosted(node, req, bfp)?;
    h.board.acknowledge(bfp, &vector)?;
    h.board.seal()?;
    let logs = rows_since(&h.board, &seqs(&vector));
    Ok(json!({"origin": h.board.origin(), "trusted_vector": h.board.trusted_vector(), "logs": logs}))
}

fn vector(node: &mut Node, bfp: &str, req: &Value) -> Result<Value> {
    let h = hosted(node, req, bfp)?;
    Ok(json!({"origin": h.board.origin(), "vector": h.board.vector()}))
}

fn push(node: &mut Node, bfp: &str, req: &Value) -> Result<Value> {
    let logs: BTreeMap<String, Vec<Row>> = serde_json::from_value(req.get("logs").cloned().unwrap_or(json!({}))).map_err(|_| Error::Invalid("logs map origins to rows".into()))?;
    let trusted = vector_of(req, "trusted_vector")?;
    let h = hosted(node, req, bfp)?;
    let mut report = Report::default();
    let stored = take(&mut h.board, &logs, bfp, &mut report)?;
    h.board.acknowledge(bfp, &trusted)?;
    Ok(json!({"stored": stored, "refused": report.refused}))
}

/// Answer one request from the device with certificate ``der``. The pool record is read here,
/// under the node's lock, so the check and the work see the same record.
fn dispatch(net: &Net, node: &mut Node, der: &[u8], ip: IpAddr, req: &Value) -> Result<Value> {
    let fp = cert_fingerprint(der);
    let result = answer(net, node, der, ip, req)?;
    // A device that has just joined says which port it listens on; its address is where it called from.
    if matches!(req["op"].as_str(), Some("join_open" | "join_confirm")) {
        if let Some(port) = req["port"].as_u64().and_then(|p| u16::try_from(p).ok()).filter(|p| *p > 0) {
            node.facts.set_addr(&fp, &SocketAddr::new(ip, port).to_string())?;
        }
    }
    Ok(result)
}

fn answer(net: &Net, node: &mut Node, der: &[u8], ip: IpAddr, req: &Value) -> Result<Value> {
    let fp = cert_fingerprint(der);
    let standing = node.members.standing(&fp);
    if standing == Standing::Revoked {
        return Err(Error::Denied("this device was put out of the pool".into()));
    }
    let bfp = board_fingerprint(der)?;
    let op = text(req, "op")?;
    match (op, standing) {
        ("hello", _) => {
            node.observe_build(&fp, req);
            Ok(hello(node, standing))
        }
        ("join_open", _) => join_open(node, der, req, ip, standing),
        ("join_exchange" | "join_confirm", Standing::Unknown) => net.join_code_step(node, der, op, req),
        ("join_exchange" | "join_confirm", _) => Err(Error::Denied("this device is already a member".into())),
        (_, Standing::Unknown) => Err(Error::Denied("this device is not a member of the pool".into())),
        ("members", _) => members(node, &fp, req),
        ("boards", _) => Ok(json!({"boards": node.boards.keys().collect::<Vec<_>>()})),
        ("vector", _) => vector(node, &bfp, req),
        ("pull", _) => pull(node, &bfp, req),
        ("push", _) => push(node, &bfp, req),
        (op, _) if crate::shard::OPS.contains(&op) => {
            if matches!(op, "shard_put" | "shard_start") {
                node.work_gate()?;
            }
            net.shards.handle(node, &fp, op, req)
        }
        _ => Err(Error::Invalid("unknown op".into())),
    }
}

/// How a served connection ended.
pub enum Ended {
    Closed,
    /// The peer asked to be reached over this connection (`attach`): it cannot be dialed, so the requests now go the other way.
    Attached,
}

/// Serve one accepted connection until the peer closes it or is refused; a member that sends `attach` hands the connection over to
/// `Net::register_reverse`, which uses it to send that member requests.
pub fn serve(net: &Net, tcp: TcpStream) -> Result<()> {
    tcp.set_read_timeout(Some(IO_TIMEOUT))?;
    tcp.set_write_timeout(Some(IO_TIMEOUT))?;
    let ip = tcp.peer_addr()?.ip();
    let mut s = tls::accept(net.server_config.clone(), tcp)?;
    let der = s.peer.clone();
    let fp = cert_fingerprint(&der);
    net.session_opened(&fp);
    let result = serve_requests(net, &mut s, &der, ip, true);
    if matches!(result, Ok(Ended::Attached)) {
        net.register_reverse(PeerClient::accepted(s)?);
        return Ok(());
    }
    net.session_closed(&fp);
    result.map(|_| ())
}

/// Answer requests on ``s`` from the device with certificate ``der`` until it closes. ``allow_attach`` is false on a connection this
/// device dialed: only the device that was dialed may be asked to hand the connection over.
pub fn serve_requests<S: Read + Write>(net: &Net, s: &mut S, der: &[u8], ip: IpAddr, allow_attach: bool) -> Result<Ended> {
    while let Some(req) = read_frame_max(s, MAX_PEER_FRAME)? {
        if allow_attach && req["op"] == "attach" {
            let active = net.node.lock().map_err(|_| Error::Damaged("node poisoned".into()))?.members.is_active(&cert_fingerprint(der));
            let reply = if active { reply_of(Ok(json!({"attached": true}))) } else { reply_of(Err(Error::Denied("only a member can be reached over its connection".into()))) };
            write_frame_max(s, &reply, MAX_PEER_FRAME)?;
            if active {
                return Ok(Ended::Attached);
            }
            continue;
        }
        let (result, revoked) = {
            let mut node = net.node.lock().map_err(|_| Error::Damaged("node poisoned".into()))?;
            let result = dispatch(net, &mut node, der, ip, &req);
            (result, node.members.standing(&cert_fingerprint(der)) == Standing::Revoked)
        };
        write_frame_max(s, &reply_of(result), MAX_PEER_FRAME)?;
        if req["op"] == "members" {
            net.drop_removed();
        }
        if revoked {
            return Ok(Ended::Closed);
        }
    }
    Ok(Ended::Closed)
}

/// What a connection is written to and read from.
pub trait Link: Read + Write + Send {}
impl<T: Read + Write + Send> Link for T {}

/// A connection to a peer, authenticated both ways.
pub struct PeerClient {
    link: Box<dyn Link>,
    pub fingerprint: String,
    pub board_fingerprint: String,
    pub der: Vec<u8>,
    closer: Option<TcpStream>,
}

impl PeerClient {
    /// Connect to ``addr`` showing ``me``; with ``expect`` the peer must show exactly that
    /// certificate (its fingerprint), without it any certificate is taken and the caller must
    /// authenticate it (a join code).
    pub fn connect(me: &Identity, addr: SocketAddr, expect: Option<&str>) -> Result<PeerClient> {
        let tcp = TcpStream::connect_timeout(&addr, Duration::from_secs(5))?;
        tcp.set_read_timeout(Some(IO_TIMEOUT))?;
        tcp.set_write_timeout(Some(IO_TIMEOUT))?;
        tcp.set_nodelay(true)?;
        let secured = tls::connect(tls::client_config(me, expect)?, tcp)?;
        let der = secured.peer.clone();
        Ok(PeerClient { fingerprint: cert_fingerprint(&der), board_fingerprint: board_fingerprint(&der)?, der, link: Box::new(secured), closer: None })
    }

    /// Like `connect`, for a connection that stays open for hours: no read timeout (the other end speaks when it has something) and TCP
    /// keepalives, so a path that died is noticed.
    pub fn connect_long(me: &Identity, addr: SocketAddr, expect: Option<&str>) -> Result<PeerClient> {
        let tcp = TcpStream::connect_timeout(&addr, Duration::from_secs(5))?;
        tcp.set_write_timeout(Some(IO_TIMEOUT))?;
        tcp.set_nodelay(true)?;
        socket2::SockRef::from(&tcp).set_tcp_keepalive(&socket2::TcpKeepalive::new().with_time(Duration::from_secs(30)).with_interval(Duration::from_secs(10)))?;
        let closer = tcp.try_clone().ok();
        let secured = tls::connect(tls::client_config(me, expect)?, tcp)?;
        let der = secured.peer.clone();
        Ok(PeerClient { fingerprint: cert_fingerprint(&der), board_fingerprint: board_fingerprint(&der)?, der, link: Box::new(secured), closer })
    }

    /// A second handle on the socket of a connection made with `connect_long`: shutting it down from another thread ends the connection.
    pub fn closer(&self) -> Option<TcpStream> {
        self.closer.as_ref().and_then(|t| t.try_clone().ok())
    }

    /// A connection a member opened to this device, to send that member requests over it.
    pub fn accepted(secured: Secured<rustls::ServerConnection>) -> Result<PeerClient> {
        let der = secured.peer.clone();
        Ok(PeerClient { fingerprint: cert_fingerprint(&der), board_fingerprint: board_fingerprint(&der)?, der, link: Box::new(secured), closer: None })
    }

    /// Answer the requests that come down this connection (the other end holds the channel), until it closes.
    pub fn serve(mut self, net: &Net, ip: IpAddr) -> Result<Ended> {
        let der = self.der.clone();
        serve_requests(net, &mut self.link, &der, ip, false)
    }

    /// Send one request; a refusal comes back as the matching error.
    pub fn call(&mut self, request: &Value) -> Result<Value> {
        write_frame_max(&mut self.link, request, MAX_PEER_FRAME)?;
        let reply = read_frame_max(&mut self.link, MAX_PEER_FRAME)?.ok_or_else(|| Error::Denied("the peer closed the connection".into()))?;
        if reply["ok"] == true {
            return Ok(reply["result"].clone());
        }
        let message = reply["error"]["message"].as_str().unwrap_or("refused").to_string();
        Err(match reply["error"]["code"].as_str() {
            Some("denied") => Error::Denied(message),
            Some("quota") => Error::Quota(message),
            Some("damaged") => Error::Damaged(message),
            Some("gap") => Error::Gap(message),
            _ => Error::Invalid(message),
        })
    }
}
