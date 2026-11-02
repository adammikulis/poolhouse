//! An OS-selected original stays private between this app and its own daemon.
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use tauri_plugin_dialog::DialogExt;

const ERROR: &str = "The file selection could not be confirmed. Choose it again in Studio.";

#[derive(Clone, Copy, Debug, PartialEq)]
enum Mode { File, Folder }


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

fn request(root: &Path, port: u16, id: &str, action: &str, payload: Value) -> Result<Value, &'static str> {
    crate::native_picker::request(root, port, crate::native_picker::Kind::Studio, id, action, payload).map_err(|_| ERROR)
}

pub fn choose(app: tauri::AppHandle, root: PathBuf, port: u16, id: String, nonce: String) -> Result<Value, &'static str> {
    if !identity(&id) || !token(&nonce) { return Err(ERROR); }
    let Some(_selection) = crate::native_picker::acquire() else { return Ok(json!({"status": "choosing"})); };
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
use crate::native_picker::response;

#[cfg(test)]
#[path = "studio_watch_tests.rs"]
mod tests;
