//! What a session tells its board it did that only the session can name: an audit entry for one of a fixed list of events, written
//! under the session's own name.

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::node::Node;
use crate::row::Kind;

pub const METHODS: [&str; 1] = ["audit_event"];
/// The events a session may record about itself.
pub const EVENTS: [&str; 2] = ["agent.effort", "authority.set_denied"];

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "audit_event" => &["event", "subject", "detail"],
        _ => return None,
    })
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text"))),
    }
}

fn clip(s: &str) -> String {
    s.chars().filter(|c| !c.is_control()).take(300).collect()
}

pub fn call(node: &mut Node, _method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    access.of_board(board, "records its own events")?;
    let event = text(p, "event")?;
    if !EVENTS.contains(&event) {
        return Err(Error::Invalid(format!("an event is one of {}", EVENTS.join(", "))));
    }
    let body = json!({"event": event, "subject": clip(text(p, "subject")?), "detail": clip(text(p, "detail")?)});
    node.host(board)?.board.append(Kind::Audit, &access.actor, body, "")?;
    Ok(json!({"recorded": true}))
}
