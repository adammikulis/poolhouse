//! The local API server (a Unix socket, a Windows named pipe): one node per state directory, one thread per connection.

use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde_json::json;

use crate::api::{handle, API_VERSION};
use crate::device::Supplied;
use crate::error::{Error, Result};
use crate::lineage::Peer;
use crate::net::Net;
use crate::node::Node;
use crate::node::update_activity::{Connection, Registry};
use crate::node::update_activity::Source;
use crate::sys::{peer, Listener, Principal, Stream};
use crate::wire::{read_frame, write_frame};

pub use crate::node::INSTANCE_LOCK;

// Count invalid responses per connection for an audit notice; valid registrations are never held back.
const BAD_WINDOW: Duration = Duration::from_secs(60);
const BAD_NOTICE: usize = 3;

/// The socket path (on Windows the pipe name) of the node whose state is ``state``.
pub fn socket_path(state: &Path) -> PathBuf {
    crate::sys::endpoint(state)
}

pub struct Server {
    node: Arc<Mutex<Node>>,
    listener: Listener,
    allowed: Principal,
    net: Option<Arc<Net>>,
    activity: Registry,
}

impl Server {
    /// Take the single-instance lock of ``state`` and bind the socket; `Denied` when another node runs.
    pub fn bind(state: &Path) -> Result<Server> {
        Server::bind_with(state, None)
    }

    /// `bind` for the device ``supplied`` is the key of (`Node::open_with`).
    pub fn bind_with(state: &Path, supplied: Option<&Supplied>) -> Result<Server> {
        let node = Node::open_with(state, supplied)?;
        let listener = Listener::bind(state)?;
        crate::sys::watch_stop(state, node.stop.clone());
        let activity = node.activity.clone();
        let node = Arc::new(Mutex::new(node));
        node.lock().map_err(|_| Error::Damaged("node poisoned".into()))?.local_server = Arc::downgrade(&node);
        Ok(Server { node, listener, allowed: Principal::me()?, net: None, activity })
    }

    /// Only this user may connect (tests use another to see the refusal).
    pub fn allow_only(mut self, who: Principal) -> Server {
        self.allowed = who;
        self
    }

    /// Answer the network methods of the local API through ``net`` (the node must be the one it serves).
    pub fn attach(mut self, net: Arc<Net>) -> Server {
        self.net = Some(net);
        self
    }

    pub fn node(&self) -> Arc<Mutex<Node>> {
        self.node.clone()
    }

    /// Serve until a `shutdown` request.
    pub fn serve(self) -> Result<()> {
        let stop = self.node.lock().map_err(|_| Error::Damaged("node poisoned".into()))?.stop.clone();
        while !stop.load(Ordering::SeqCst) {
            match self.listener.accept() {
                Ok(stream) => {
                    let (node, who, net) = (self.node.clone(), self.allowed.clone(), self.net.clone());
                    let connection_lifetime = self.activity.connection()
                        .map_err(|_| Error::say("unavailable", "node activity registry unavailable", "none", "retry"))?;
                    std::thread::spawn(move || {
                        let _ = connection(stream, &node, &who, net.as_deref(), connection_lifetime);
                    });
                }
                // `accept` itself waits a short while for a client, so a request is taken the moment it comes.
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(e) => return Err(e.into()),
            }
        }
        Ok(())
    }
}

fn connection(mut stream: Stream, node: &Mutex<Node>, allowed: &Principal, net: Option<&Net>, _lifetime: Connection) -> Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
    if peer(&stream)? != *allowed {
        let refusal = json!({"v": API_VERSION, "id": null, "ok": false, "error": {"code": "denied", "message": "denied: another user"}});
        return write_frame(&mut stream, &refusal);
    }
    let who = Peer::of(&stream);
    let mut challenge: Option<(String, Instant)> = None;
    let mut failure_count = 0usize;
    let mut failure_window = Instant::now();
    while let Some(request) = read_frame(&mut stream)? {
        if request["method"] == "root_challenge" {
            let nonce = crate::fsutil::random_hex(32)?;
            challenge = Some((nonce.clone(), Instant::now()));
            write_frame(&mut stream, &crate::api::respond(&request, Ok(json!({"nonce": nonce}))))?;
            continue;
        }
        if request["method"] == "register" {
            let offered = challenge.take();
            let (reply, bad) = if net.is_some() {
                match crate::net::tracked_node(node, Source::Network) {
                    Ok(mut n) => register(&mut n, &request, &who, offered),
                    Err(error) => (crate::api::respond(&request, Err(error)), false),
                }
            } else {
                match node.lock() {
                    Ok(mut n) => register(&mut n, &request, &who, offered),
                    Err(_) => (crate::api::respond(&request, Err(Error::Damaged("node poisoned".into()))), false),
                }
            };
            if bad {
                let now = Instant::now();
                if now.duration_since(failure_window) >= BAD_WINDOW { failure_count = 0; failure_window = now; }
                failure_count = failure_count.saturating_add(1);
                if failure_count == BAD_NOTICE {
                    if let Ok(mut n) = node.lock() {
                        if n.rates.permits("root-auth-notice", 1, BAD_WINDOW) {
                            n.rates.record("root-auth-notice", BAD_WINDOW);
                            let _ = n.record_event("root_auth.rejected", "registration", "repeated invalid root credential responses");
                        }
                    }
                }
            }
            write_frame(&mut stream, &reply)?;
            continue;
        }
        let reply = crate::lease::rpc::serve(&|r| match net {
            Some(net) => Ok(net.call(r, &who)),
            None => match node.lock() {
                Ok(mut n) => Ok(handle(&mut n, r, &who)),
                Err(_) => Err(Error::Damaged("node poisoned".into())),
            },
        }, &request, &who)?;
        write_frame(&mut stream, &reply)?;
    }
    Ok(())
}

/// Check and dispatch under one node lock, so a token cannot change from a child to a root between the two.
fn register(node: &mut Node, request: &serde_json::Value, peer: &Peer, challenge: Option<(String, Instant)>) -> (serde_json::Value, bool) {
    let mut clean = request.clone();
    let proof = clean.get_mut("params").and_then(|p| p.as_object_mut()).and_then(|p| p.remove("root_proof"))
        .and_then(|p| p.as_str().map(str::to_string)).unwrap_or_default();
    if crate::root_auth::needs_proof(node, &clean) {
        let verified = challenge.filter(|(_, at)| at.elapsed() <= crate::root_auth::CHALLENGE_TTL)
            .is_some_and(|(nonce, _)| crate::root_auth::verifies(&node.dir, &nonce, &clean, &proof).unwrap_or(false));
        if !verified {
            return (crate::api::respond(request, Err(Error::Denied("a main session needs a fresh root credential response".into()))), true);
        }
    }
    (handle(node, &clean, peer), false)
}
