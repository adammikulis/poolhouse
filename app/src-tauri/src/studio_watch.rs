//! An OS-selected original stays private between this app and its own daemon.
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use tauri_plugin_dialog::DialogExt;

const LIMIT: usize = 64 * 1024;
const DEADLINE: Duration = Duration::from_secs(5);
static OPEN: AtomicBool = AtomicBool::new(false);
const ERROR: &str = "The file selection could not be confirmed. Choose it again in Studio.";

#[derive(Clone, Copy, Debug, PartialEq)]
enum Mode { File, Folder }

struct Selection;
impl Drop for Selection {
    fn drop(&mut self) { OPEN.store(false, Ordering::Release); }
}

fn token(value: &str) -> bool {
    (32..=128).contains(&value.len()) && value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

fn identity(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

fn exact(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|obj| obj.len() == keys.len() && keys.iter().all(|key| obj.contains_key(*key)))
}

fn public(value: &Value) -> Result<Value, &'static str> {
    match value["status"].as_str() {
        Some("choosing" | "cancelled") if exact(value, &["status"]) => Ok(value.clone()),
        Some("complete") if exact(value, &["status", "source"]) => {
            let source = &value["source"];
            let id = source["id"].as_str().ok_or(ERROR)?;
            let title = source["title"].as_str().ok_or(ERROR)?;
            if !exact(source, &["id", "title"]) || id.is_empty() || id.len() > 256
                || !id.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-:".contains(&b))
                || title.is_empty() || title.len() > 1024 || title.contains(['/', '\\']) || title.chars().any(char::is_control) {
                return Err(ERROR);
            }
            Ok(value.clone())
        }
        _ => Err(ERROR),
    }
}

fn flow(mut exchange: impl FnMut(&str, Value) -> Result<Value, &'static str>,
        choose: impl FnOnce(Mode) -> Result<Option<PathBuf>, &'static str>, nonce: &str) -> Result<Value, &'static str> {
    let begin = exchange("begin", json!({"nonce": nonce}))?;
    if !exact(&begin, &["status", "mode", "claim"]) { return public(&begin); }
    let claim = begin["claim"].as_str().filter(|v| token(v)).ok_or(ERROR)?;
    if begin["status"] != "choosing" { return Err(ERROR); }
    let mode = match begin["mode"].as_str() { Some("file") => Mode::File, Some("folder") => Mode::Folder, _ => return Err(ERROR) };
    let selected = choose(mode);
    match selected {
        Ok(Some(path)) => {
            let path = path.to_str().filter(|p| path.is_absolute() && !p.is_empty()).ok_or(ERROR)?;
            public(&exchange("complete", json!({"nonce": nonce, "claim": claim, "path": path}))?)
        }
        Ok(None) | Err(_) => public(&exchange("cancel", json!({"nonce": nonce, "claim": claim}))?),
    }
}

fn response(bytes: &[u8]) -> Result<Value, &'static str> {
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

fn request(root: &Path, port: u16, id: &str, action: &str, payload: Value) -> Result<Value, &'static str> {
    if !identity(id) || !matches!(action, "begin" | "complete" | "cancel") { return Err(ERROR); }
    let secret = crate::launch::native_secret(root, port).ok_or(ERROR)?;
    let body = serde_json::to_vec(&payload).map_err(|_| ERROR)?;
    if body.len() > LIMIT { return Err(ERROR); }
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let started = Instant::now();
    let mut socket = TcpStream::connect_timeout(&address, DEADLINE).map_err(|_| ERROR)?;
    socket.set_write_timeout(Some(DEADLINE)).map_err(|_| ERROR)?;
    let header = format!("POST /ui/studio/native-picks/{id}/{action} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nX-Poolhouse-UI: 1\r\nX-Poolhouse-Launch: {secret}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n", body.len());
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

pub fn choose(app: tauri::AppHandle, root: PathBuf, port: u16, id: String, nonce: String) -> Result<Value, &'static str> {
    if !identity(&id) || !token(&nonce) { return Err(ERROR); }
    if OPEN.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire).is_err() { return Ok(json!({"status": "choosing"})); }
    let _selection = Selection;
    flow(|action, payload| request(&root, port, &id, action, payload), |mode| {
        let dialog = app.dialog().file().set_title("Choose the original Studio source");
        let chosen = match mode {
            Mode::File => dialog.add_filter("Data files", &["jsonl", "csv"]).blocking_pick_file(),
            Mode::Folder => dialog.blocking_pick_folder(),
        };
        chosen.map(|file| file.into_path().map_err(|_| ERROR)).transpose()
    }, &nonce)
}

#[cfg(test)]
#[path = "studio_watch_tests.rs"]
mod tests;
