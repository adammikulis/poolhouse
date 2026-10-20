//! Only an OS-selected folder reaches the daemon; the page receives an opaque project.
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use tauri_plugin_dialog::DialogExt;

const PURPOSE: &str = "coding-project";
const ERROR: &str = "The coding project selection could not be confirmed. Choose the folder again.";

fn identity(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}
fn token(value: &str) -> bool {
    (32..=128).contains(&value.len()) && value.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
}
fn exact(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|obj| obj.len() == keys.len() && keys.iter().all(|key| obj.contains_key(*key)))
}
fn public(value: &Value) -> Result<Value, &'static str> {
    if value["purpose"] != PURPOSE { return Err(ERROR); }
    match value["status"].as_str() {
        Some("choosing" | "cancelled") if exact(value, &["status", "purpose"]) => Ok(value.clone()),
        Some("complete" | "needs-project") if exact(value, &["status", "purpose", "project"]) => {
            let project = &value["project"];
            let id = project["id"].as_str().ok_or(ERROR)?;
            let title = project["title"].as_str().ok_or(ERROR)?;
            if !exact(project, &["id", "title"]) || id.is_empty() || id.len() > 256
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
        choose: impl FnOnce() -> Result<Option<PathBuf>, &'static str>, nonce: &str) -> Result<Value, &'static str> {
    let begin = exchange("claim", json!({"nonce":nonce}))?;
    if !exact(&begin, &["status", "mode", "purpose", "claim"]) { return public(&begin); }
    if begin["status"] != "choosing" || begin["mode"] != "folder" || begin["purpose"] != PURPOSE { return Err(ERROR); }
    let claim = begin["claim"].as_str().filter(|value| token(value)).ok_or(ERROR)?;
    match choose() {
        Ok(Some(path)) => {
            let path = path.to_str().filter(|_| path.is_absolute()).ok_or(ERROR)?;
            public(&exchange("complete", json!({"nonce":nonce,"claim":claim,"path":path}))?)
        }
        Ok(None) | Err(_) => public(&exchange("cancel", json!({"nonce":nonce,"claim":claim}))?),
    }
}
fn request(root: &Path, port: u16, id: &str, action: &str, payload: Value) -> Result<Value, &'static str> {
    crate::native_picker::request(root, port, crate::native_picker::Kind::CodingProject, id, action, payload).map_err(|_| ERROR)
}
pub fn choose(app: tauri::AppHandle, root: PathBuf, port: u16, id: String, nonce: String) -> Result<Value, &'static str> {
    if !identity(&id) || !token(&nonce) { return Err(ERROR); }
    let Some(_selection) = crate::native_picker::acquire() else { return Ok(json!({"status":"choosing","purpose":PURPOSE})); };
    flow(|action, payload| request(&root, port, &id, action, payload), || {
        app.dialog().file().set_title("Choose a coding project").blocking_pick_folder()
            .map(|file| file.into_path().map_err(|_| ERROR)).transpose()
    }, &nonce)
}

#[cfg(test)]
#[path = "coding_project_tests.rs"]
mod tests;
