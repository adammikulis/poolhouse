//! The client side: talk to the node, and start it when the socket (the pipe) is dead.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::api::API_VERSION;
use crate::error::{Error, Result};
use crate::sys::{connect, lock, spawn_detached, Stream};
use crate::wire::{read_frame, write_frame};

pub const START_LOCK: &str = "start.lock";
const START_WAIT: Duration = Duration::from_secs(10);

pub struct Client {
    stream: Stream,
    state: PathBuf,
}

/// Whether `ensure_running` found a node or started one.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Started {
    Found,
    Spawned,
}

impl Client {
    pub fn connect(state: &Path) -> Result<Client> {
        #[cfg(unix)]
        let state = state.canonicalize()?;
        #[cfg(not(unix))]
        let state = state.to_path_buf();
        let stream = connect(&state)?;
        stream.set_read_timeout(Some(Duration::from_secs(30)))?;
        Ok(Client { stream, state })
    }

    /// Send one request and return its result; a refusal comes back as the matching error.
    pub fn call(&mut self, method: &str, board: &str, token: &str, params: Value) -> Result<Value> {
        let mut request = json!({"v": API_VERSION, "id": 1, "method": method, "params": params});
        if !board.is_empty() {
            request["board"] = json!(board);
        }
        if !token.is_empty() {
            request["token"] = json!(token);
        }
        if method == "register" {
            let challenge = self.send(&json!({"v": API_VERSION, "id": 1, "method": "root_challenge", "params": {}}))?;
            let nonce = challenge["nonce"].as_str().ok_or_else(|| Error::Invalid("root challenge has no nonce".into()))?;
            #[cfg(unix)]
            let server = crate::sys::peer_pid(&self.stream);
            #[cfg(windows)]
            let server = self.stream.server_pid();
            let proof = server.ok_or_else(|| Error::Denied("the node's process could not be read".into()))
                .and_then(|pid| crate::root_auth::sign(&self.state, nonce, pid, &request));
            match proof {
                Ok(proof) => request["params"]["root_proof"] = json!(proof),
                Err(error) if token.is_empty() => return Err(error),
                Err(_) => {} // A child with its parent's token does not need the root credential.
            }
        }
        self.send(&request)
    }

    /// Send a request exactly as given (tests send ones the helper would not).
    pub fn send(&mut self, request: &Value) -> Result<Value> {
        let reply = match write_frame(&mut self.stream, request) {
            Ok(()) => read_frame(&mut self.stream)?.ok_or_else(|| Error::Io(std::io::ErrorKind::UnexpectedEof.into()))?,
            Err(Error::Io(error)) if matches!(error.kind(), std::io::ErrorKind::BrokenPipe | std::io::ErrorKind::ConnectionReset | std::io::ErrorKind::NotConnected) => {
                match read_frame(&mut self.stream) {
                    Ok(Some(reply)) if reply["v"] == API_VERSION && reply["id"].is_null() && reply["ok"] == false
                        && reply["error"]["code"] == "denied" && reply["error"]["message"] == "denied: another user" => reply,
                    _ => return Err(Error::Io(error)),
                }
            }
            Err(error) => return Err(error),
        };
        if reply["ok"] == true {
            return Ok(reply["result"].clone());
        }
        let message = reply["error"]["message"].as_str().unwrap_or("refused").to_string();
        Err(match reply["error"]["code"].as_str() {
            Some("denied") => Error::Denied(message),
            Some("quota") => crate::limits::relayed(message),
            Some("damaged") => Error::Damaged(message),
            _ => Error::Invalid(message),
        })
    }
}

fn hello(state: &Path) -> Result<Value> {
    Client::connect(state)?.call("hello", "", "", json!({}))
}

/// Connect to the node of ``state``, starting ``node_bin`` first when nothing answers. Starting
/// is single-flight: callers queue on a lock file, and the second finds the first's node.
pub fn ensure_running(state: &Path, node_bin: &Path) -> Result<Started> {
    if hello(state).is_ok() {
        return Ok(Started::Found);
    }
    crate::fsutil::private_dir(state)?;
    let _turn = lock(&state.join(START_LOCK))?;
    if hello(state).is_ok() {
        return Ok(Started::Found);
    }
    let mut command = Command::new(node_bin);
    command.arg("run").arg("--state").arg(state).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    spawn_detached(&mut command)?;
    let deadline = Instant::now() + START_WAIT;
    while Instant::now() < deadline {
        if hello(state).is_ok() {
            return Ok(Started::Spawned);
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    Err(Error::Io(std::io::Error::new(std::io::ErrorKind::TimedOut, "the node did not answer after being started")))
}

/// A connected client, with the node started if need be.
pub fn connect_or_start(state: &Path, node_bin: &Path) -> Result<Client> {
    ensure_running(state, node_bin)?;
    Client::connect(state)
}
