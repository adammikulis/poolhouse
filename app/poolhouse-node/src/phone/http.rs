//! The endpoint a phone talks to: HTTPS (TLS 1.3, one pinned P-256 certificate), one request a connection, JSON in and out.
//!
//! `POST /phone/v1/<op>` answers `{"ok":true,"result":...}` or `{"ok":false,"error":{"code","message"}}` with status 200, except a token
//! the node does not know, which is 401. `add` and `decide` carry no token: a phone's addition is proved by the one-time code and its key,
//! and a decision is proved by the signature. Every other op needs the token a phone was given when it was added.

use std::io::{Read, Write};
use std::net::{IpAddr, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use rustls::{ServerConfig, ServerConnection, StreamOwned};
use serde_json::{json, Map, Value};

use crate::approvals::calls::pending;
use crate::approvals::decide::{decide, sweep, view, Decision};
use crate::error::{Error, Result};
use crate::node::Node;
use crate::phone::enrol::{ask_to_remove, enrol, make_offer};
use crate::phone::{shown, ACTIVE, PENDING};

const MOST_HEAD: usize = 16 * 1024;
const MOST_BODY: usize = 64 * 1024;
const MOST_CONNECTIONS: usize = 48;
const IO_TIMEOUT: Duration = Duration::from_secs(20);
const MOST_WAIT: Duration = Duration::from_secs(15);
const POLL: Duration = Duration::from_millis(250);
const RECENT: usize = 10;
const MOST_ENDPOINT: usize = 512;

fn lock(node: &Mutex<Node>) -> Result<std::sync::MutexGuard<'_, Node>> {
    node.lock().map_err(|_| Error::Damaged("node poisoned".into()))
}

fn reply(result: Result<Value>) -> (u16, Value) {
    match result {
        Ok(v) => (200, json!({"ok": true, "result": v})),
        Err(e) => (200, json!({"ok": false, "error": {"code": e.code(), "message": e.to_string().split_once(": ").map_or_else(|| e.to_string(), |(_, m)| m.to_string())}})),
    }
}

fn unknown_token() -> (u16, Value) {
    (401, json!({"ok": false, "error": {"code": "denied", "message": "This phone is not added to this device, or it was removed."}}))
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> &'a str {
    p.get(key).and_then(Value::as_str).unwrap_or("")
}

/// Whether ``url`` can be a push address: `https`, a host and a path, plain, no credentials.
pub fn push_address(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("https://") else { return false };
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    url.len() <= MOST_ENDPOINT && url.bytes().all(|b| b.is_ascii_graphic()) && !host.is_empty() && !host.contains('@') && rest.len() > host.len()
}

fn list(node: &mut Node, me: &str) -> Value {
    let now = node.now_ms();
    let mut shown_now = pending(node);
    let mut done: Vec<&crate::approvals::Approval> = node.approvals.all().iter().filter(|a| !a.pending()).collect();
    done.sort_by_key(|a| std::cmp::Reverse(a.seq));
    shown_now.extend(done.into_iter().take(RECENT).map(|a| view(node, a)));
    json!({"approvals": shown_now, "phones": node.phones.all().filter(|p| p.state != crate::phone::REMOVED).map(|p| shown(p, me)).collect::<Vec<_>>(),
           "version": node.approvals.version(), "now_ms": now, "device": node.cert.fingerprint(), "policy": node.members.policy.name()})
}

fn wait(node: &Mutex<Node>, me: &str, p: &Map<String, Value>) -> Result<Value> {
    let after = p.get("after").and_then(Value::as_u64).unwrap_or(u64::MAX);
    let limit = Duration::from_millis(p.get("timeout_ms").and_then(Value::as_u64).unwrap_or(10_000)).min(MOST_WAIT);
    let start = Instant::now();
    loop {
        {
            let mut n = lock(node)?;
            sweep(&mut n)?;
            if n.approvals.version() != after || start.elapsed() >= limit {
                return Ok(list(&mut n, me));
            }
        }
        std::thread::sleep(POLL);
    }
}

fn offer(node: &mut Node) -> Result<Value> {
    if !node.rates.permits("phone offer", 3, Duration::from_secs(3600)) {
        return Err(Error::Quota("3 codes an hour is the most this device makes for phones; try again later.".into()));
    }
    node.rates.record("phone offer", Duration::from_secs(3600));
    let secret = make_offer(&node.dir, node.now_ms())?;
    let expires = node.now_ms() + crate::phone::enrol::OFFER_TTL_MS;
    Ok(json!({"link": crate::phone::calls::link(node, &secret, expires), "expires_ms": expires}))
}

/// The ops of a phone that is added, as a phone reads them.
fn phone_op(node: &Mutex<Node>, id: &str, op: &str, p: &Map<String, Value>) -> Result<Value> {
    if op == "wait" {
        return wait(node, id, p);
    }
    let mut n = lock(node)?;
    sweep(&mut n)?;
    let now = n.now_ms();
    n.phones.touch(id, now)?;
    match op {
        "list" => Ok(list(&mut n, id)),
        "get" => n.approvals.get(text(p, "id")).cloned().map(|a| view(&n, &a)).ok_or_else(|| Error::Invalid("There is no request with that id.".into())),
        "phones" => Ok(json!({"phones": n.phones.all().filter(|f| f.state != crate::phone::REMOVED).map(|f| shown(f, id)).collect::<Vec<_>>()})),
        "push" => {
            let endpoint = text(p, "endpoint");
            if !endpoint.is_empty() && !push_address(endpoint) {
                return Err(Error::Invalid("A push address is an https address.".into()));
            }
            n.phones.set_endpoint(id, endpoint)?;
            Ok(json!({"push": !endpoint.is_empty()}))
        }
        "remove" => {
            let by = format!("The phone {}", n.phones.get(id).map(|f| f.name.clone()).unwrap_or_default());
            let made = ask_to_remove(&mut n, &by, text(p, "id"))?;
            n.approvals.get(&made).cloned().map(|a| view(&n, &a)).ok_or_else(|| Error::Invalid("There is no request with that id.".into()))
        }
        "offer" => offer(&mut n),
        _ => Err(Error::Invalid("This device's phone endpoint has no such operation.".into())),
    }
}

/// Answer one request. ``token`` is the bearer token the phone sent, empty when it sent none.
pub fn handle(node: &Mutex<Node>, ip: IpAddr, http: &str, path: &str, token: &str, body: &[u8]) -> (u16, Value) {
    let Some(op) = path.strip_prefix("/phone/v1/") else { return (404, json!({"ok": false, "error": {"code": "invalid", "message": "Not found."}})) };
    let params: Map<String, Value> = match serde_json::from_slice::<Value>(if body.is_empty() { b"{}" } else { body }) {
        Ok(Value::Object(m)) => m,
        _ => return reply(Err(Error::Invalid("The body is a JSON object.".into()))),
    };
    let guard = match lock(node) {
        Ok(g) => g,
        Err(e) => return reply(Err(e)),
    };
    let mut n = guard;
    match (http, op) {
        ("GET", "hello") => {
            let name = n.members.get(&n.cert.fingerprint()).map(|d| d.name.clone()).unwrap_or_default();
            return reply(Ok(json!({"node": "poolhouse-node", "device": n.cert.fingerprint(), "name": name, "policy": n.members.policy.name(), "version": env!("CARGO_PKG_VERSION"), "now_ms": n.now_ms()})));
        }
        ("POST", "add" | "decide") => {
            let key = format!("phone {op} {ip}");
            if !n.rates.permits(&key, 60, Duration::from_secs(60)) {
                return reply(Err(Error::Quota("Too many tries from this address; wait a minute.".into())));
            }
            n.rates.record(&key, Duration::from_secs(60));
            return reply(if op == "add" { enrol(&mut n, &params) } else {
                decide(&mut n, &Decision { id: text(&params, "id"), decision: text(&params, "decision"), key: text(&params, "key"), sig: text(&params, "sig"), text_sha256: text(&params, "text_sha256") })
            });
        }
        ("POST", _) => {}
        _ => return (405, json!({"ok": false, "error": {"code": "invalid", "message": "Method not allowed."}})),
    }
    let Some((id, state)) = n.phones.by_token(token).map(|f| (f.id.clone(), f.state.clone())) else { return unknown_token() };
    if state == PENDING {
        return reply(if op == "status" { n.phones.get(&id).map(|f| json!({"state": f.state})).ok_or_else(|| Error::Invalid("Unknown phone.".into())) } else {
            Err(Error::Denied("This phone is waiting for a phone you already added to approve it.".into()))
        });
    }
    debug_assert_eq!(state, ACTIVE);
    drop(n);
    reply(phone_op(node, &id, op, &params))
}

fn head_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n")
}

/// Read one request: its method, path, bearer token and body.
fn read_request<S: Read>(s: &mut S) -> Result<(String, String, String, Vec<u8>)> {
    let bad = || Error::Invalid("The request is not understood.".into());
    let (mut buf, mut chunk) = (Vec::new(), [0u8; 4096]);
    let end = loop {
        if let Some(e) = head_end(&buf) {
            break e;
        }
        if buf.len() > MOST_HEAD {
            return Err(bad());
        }
        let n = s.read(&mut chunk).map_err(|_| bad())?;
        if n == 0 {
            return Err(bad());
        }
        buf.extend_from_slice(&chunk[..n]);
    };
    let head = String::from_utf8(buf[..end].to_vec()).map_err(|_| bad())?;
    let mut lines = head.split("\r\n");
    let mut first = lines.next().unwrap_or("").split(' ');
    let (method, path, version) = (first.next().unwrap_or(""), first.next().unwrap_or(""), first.next().unwrap_or(""));
    if !version.starts_with("HTTP/1.") || !path.starts_with('/') {
        return Err(bad());
    }
    let (mut length, mut token) = (0usize, String::new());
    for line in lines {
        let (name, value) = line.split_once(':').ok_or_else(bad)?;
        match name.trim().to_ascii_lowercase().as_str() {
            "content-length" => length = value.trim().parse().map_err(|_| bad())?,
            "authorization" => token = value.trim().strip_prefix("Bearer ").unwrap_or("").to_string(),
            "transfer-encoding" => return Err(bad()),
            _ => {}
        }
    }
    if length > MOST_BODY {
        return Err(bad());
    }
    let mut body = buf[end + 4..].to_vec();
    while body.len() < length {
        let n = s.read(&mut chunk).map_err(|_| bad())?;
        if n == 0 {
            return Err(bad());
        }
        body.extend_from_slice(&chunk[..n]);
    }
    body.truncate(length);
    Ok((method.to_string(), path.to_string(), token, body))
}

fn write_response<S: Write>(s: &mut S, status: u16, body: &Value) -> std::io::Result<()> {
    let bytes = body.to_string().into_bytes();
    let reason = match status { 200 => "ok", 401 => "unauthorized", 404 => "not found", 405 => "method not allowed", _ => "bad request" };
    write!(s, "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n", bytes.len())?;
    s.write_all(&bytes)?;
    s.flush()
}

fn serve_one(node: &Mutex<Node>, config: Arc<ServerConfig>, tcp: TcpStream) -> Result<()> {
    let ip = tcp.peer_addr()?.ip();
    tcp.set_read_timeout(Some(IO_TIMEOUT))?;
    tcp.set_write_timeout(Some(IO_TIMEOUT))?;
    let mut conn = ServerConnection::new(config).map_err(|e| Error::Invalid(format!("tls: {e}")))?;
    let mut tcp = tcp;
    while conn.is_handshaking() {
        conn.complete_io(&mut tcp).map_err(|e| Error::Denied(format!("the handshake failed: {e}")))?;
    }
    let mut stream = StreamOwned::new(conn, tcp);
    let (status, body) = match read_request(&mut stream) {
        Ok((method, path, token, body)) => handle(node, ip, &method, &path, &token, &body),
        Err(e) => (400, json!({"ok": false, "error": {"code": "invalid", "message": e.to_string()}})),
    };
    let _ = write_response(&mut stream, status, &body);
    stream.conn.send_close_notify();
    let _ = stream.conn.complete_io(&mut stream.sock);
    Ok(())
}

/// What a running endpoint is.
pub struct PhoneServer {
    pub port: u16,
}

/// Listen on ``listen`` for phones, answering each request from ``node``. The port is recorded on the node so a code can carry it.
pub fn start(node: Arc<Mutex<Node>>, listen: SocketAddr) -> Result<PhoneServer> {
    let (config, stop) = {
        let n = lock(&node)?;
        (n.phone_cert.server_config()?, n.stop.clone())
    };
    let listener = TcpListener::bind(listen)?;
    listener.set_nonblocking(true)?;
    let port = listener.local_addr()?.port();
    lock(&node)?.phone_port = port;
    let live = Arc::new(AtomicUsize::new(0));
    std::thread::spawn(move || {
        while !stop.load(Ordering::SeqCst) {
            match listener.accept() {
                Ok((tcp, _)) if live.load(Ordering::SeqCst) < MOST_CONNECTIONS => {
                    let _ = tcp.set_nonblocking(false);
                    let (node, config, live) = (node.clone(), config.clone(), live.clone());
                    live.fetch_add(1, Ordering::SeqCst);
                    std::thread::spawn(move || {
                        let _ = serve_one(&node, config, tcp);
                        live.fetch_sub(1, Ordering::SeqCst);
                    });
                }
                Ok(_) => std::thread::sleep(Duration::from_millis(50)),
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(10)),
                Err(_) => std::thread::sleep(Duration::from_millis(100)),
            }
        }
    });
    Ok(PhoneServer { port })
}
