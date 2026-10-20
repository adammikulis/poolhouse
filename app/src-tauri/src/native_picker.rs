//! Fixed private native-picker transport and one dialog at a time.
use serde_json::Value;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

const LIMIT: usize = 64 * 1024;
const DEADLINE: Duration = Duration::from_secs(5);
const ERROR: &str = "The selection could not be confirmed.";
static OPEN: AtomicBool = AtomicBool::new(false);

pub(crate) enum Kind { Studio, CodingProject }
impl Kind {
    fn prefix(&self) -> &'static str {
        match self { Self::Studio => "/ui/studio/native-picks", Self::CodingProject => "/ui/setup/projects/native-picks" }
    }
    fn accepts(&self, action: &str) -> bool {
        matches!((self, action), (Self::Studio, "begin" | "complete" | "cancel") | (Self::CodingProject, "claim" | "complete" | "cancel"))
    }
}

pub(crate) struct Selection;
impl Drop for Selection {
    fn drop(&mut self) { OPEN.store(false, Ordering::Release); }
}
pub(crate) fn acquire() -> Option<Selection> {
    OPEN.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire).ok().map(|_| Selection)
}

fn identity(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}


pub(crate) fn response(bytes: &[u8]) -> Result<Value, &'static str> {
    if bytes.len() > LIMIT { return Err(ERROR); }
    let split = bytes.windows(4).position(|part| part == b"\r\n\r\n").ok_or(ERROR)?;
    let header = std::str::from_utf8(&bytes[..split]).map_err(|_| ERROR)?;
    let mut lines = header.lines();
    let status: Vec<_> = lines.next().ok_or(ERROR)?.split_whitespace().collect();
    if status.len() < 2 || !matches!(status[0], "HTTP/1.0" | "HTTP/1.1") || status[1] != "200" { return Err(ERROR); }
    let mut length = None;
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(ERROR)?;
        if name.eq_ignore_ascii_case("transfer-encoding") { return Err(ERROR); }
        if name.eq_ignore_ascii_case("content-length") {
            if length.is_some() { return Err(ERROR); }
            length = Some(value.trim().parse::<usize>().map_err(|_| ERROR)?);
        }
    }
    let body = &bytes[split + 4..];
    if length != Some(body.len()) { return Err(ERROR); }
    serde_json::from_slice(body).map_err(|_| ERROR)
}


pub(crate) fn request(root: &Path, port: u16, kind: Kind, id: &str, action: &str, payload: Value) -> Result<Value, &'static str> {
    if !identity(id) || !kind.accepts(action) { return Err(ERROR); }
    let prefix = kind.prefix();
    let secret = crate::launch::native_secret(root, port).ok_or(ERROR)?;
    let body = serde_json::to_vec(&payload).map_err(|_| ERROR)?;
    if body.len() > LIMIT { return Err(ERROR); }
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let started = Instant::now();
    let mut socket = TcpStream::connect_timeout(&address, DEADLINE).map_err(|_| ERROR)?;
    socket.set_write_timeout(Some(DEADLINE)).map_err(|_| ERROR)?;
    let header = format!("POST {prefix}/{id}/{action} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nX-Poolhouse-UI: 1\r\nX-Poolhouse-Launch: {secret}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n", body.len());
    socket.write_all(header.as_bytes()).and_then(|_| socket.write_all(&body)).map_err(|_| ERROR)?;
    let mut bytes = Vec::new();
    loop {
        let remaining = DEADLINE.checked_sub(started.elapsed()).filter(|d| !d.is_zero()).ok_or(ERROR)?;
        socket.set_read_timeout(Some(remaining)).map_err(|_| ERROR)?;
        let mut chunk = [0_u8; 4096];
        let count = socket.read(&mut chunk).map_err(|_| ERROR)?;
        if count == 0 { break; }
        if bytes.len() + count > LIMIT { return Err(ERROR); }
        bytes.extend_from_slice(&chunk[..count]);
    }
    response(&bytes)
}

