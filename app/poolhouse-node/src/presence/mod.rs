//! The step that says a person at this computer agrees, and the cards it answers.
//!
//! A node started with no `--presence` asks nobody (`Auto`): the user of this computer runs it, nothing waits for a person, and what a yes
//! makes (the one-time secret that adds the first key or the first phone, or the ticket for a change the node drafted) is made at once. What
//! a yes makes lives in the node's memory, never in a file, so a program that can write the state directory cannot leave one. The node
//! raises no dialog of the operating system. `--presence terminal` asks on the person's terminal device that the node itself reads
//! (`terminal::Terminal`); it is for a rig that plays the person at a pseudo-terminal and is never the launcher's.
//!
//! Who may ask is proven, not claimed: the person's own session (`register_person` with the secret the node keeps for the person,
//! `identity::person_secret`) drafts a change and asks for it, and a live main session does too while nobody is asked (`may_ask`). Tests of
//! this crate put their own `Presence` on `Node::firsts` (a library field no binary sets).

pub mod card;
mod firsts;
mod mode;
pub mod terminal;
pub mod tty;

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::node::Node;

pub use firsts::Firsts;
pub use mode::{default_presence, Auto, Mode};
pub(crate) use mode::{on_real_home, plain};

pub const METHODS: [&str; 3] = ["presence_ask", "presence_status", "ticket_spend"];

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "presence_ask" => &["purpose", "public", "terminal", "card"],
        "presence_status" => &[],
        "ticket_spend" => &["ticket", "payload"],
        _ => return None,
    })
}

pub fn boardless(method: &str) -> bool {
    METHODS.contains(&method)
}

/// What the confirmed prompt makes possible.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Purpose {
    /// The first person key, enrolled with `person_enroll`.
    Key,
    /// The first phone, added with the code in the link a phone scans.
    Phone,
    /// A key the node made for a pool, for one change the node drafted: the yes enrols it and is the ticket for the change.
    PoolKey,
    /// A change the node drafted that keys already enrolled sign: the yes is the ticket for it.
    Card,
}

impl Purpose {
    fn named(name: &str) -> Result<Purpose> {
        match name {
            "key" => Ok(Purpose::Key),
            "phone" => Ok(Purpose::Phone),
            "pool_key" => Ok(Purpose::PoolKey),
            "card" => Ok(Purpose::Card),
            _ => Err(Error::Invalid("purpose is key (the first person key), phone (the first phone), pool_key (the key a pool's card makes) or card (a change to sign)".into())),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Purpose::Key => "key",
            Purpose::Phone => "phone",
            Purpose::PoolKey => "pool_key",
            Purpose::Card => "card",
        }
    }

    fn words(self) -> &'static str {
        match self {
            Purpose::Key => "your first person key",
            Purpose::Phone => "your first phone",
            Purpose::PoolKey => "a key for a pool",
            Purpose::Card => "a change to a pool",
        }
    }
}

/// A prompt that is open.
pub trait Asking: Send {
    /// None while the person has not answered, then whether the answer was yes. A dialog that could not be shown or that failed has given no
    /// answer: it says why in `failed`, and is never a no.
    fn poll(&mut self) -> Option<bool>;

    /// Why the prompt ended with no answer from the person, in the system's own words, when it did: the dialog could not be shown, or it failed.
    /// The person declining is `Some(false)` from `poll`, and time running out is neither.
    fn failed(&self) -> Option<String> {
        None
    }

    /// How many milliseconds ago the person answered, once they have: what a yes makes lives from the yes and not from the poll that found
    /// it. A prompt that cannot say (a stand-in answering as it is asked) says 0.
    fn age_ms(&self) -> u64 {
        0
    }
}

/// Where the question is put to the person.
pub trait Presence: Send {
    /// `auto` when nobody is asked, `terminal` for the person's terminal device.
    fn kind(&self) -> &'static str;
    /// Open a prompt that says ``what``; ``terminal`` is the terminal device the asker named, which only a terminal prompt uses.
    fn begin(&mut self, what: &str, terminal: &str) -> Result<Box<dyn Asking>>;
    /// Whether this presence asks only on the controlling terminal of the process that asks, which the node finds itself and the asker does not
    /// name: a computer whose own node has no prompt of the system's to ask with (`os::can_prompt`).
    fn own_terminal(&self) -> bool {
        false
    }
}

fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text."))),
    }
}

pub fn call(node: &mut Node, method: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("A registered session asks for the first key or phone.".into()))?;
    let (by, now) = (format!("{}/{}", holder.board, holder.name), node.now_ms());
    if method == "ticket_spend" {
        return node.firsts.spend_payload(&by, text(p, "ticket")?, text(p, "payload")?, now).map(|()| json!({"spent": true})).map_err(|why| Error::Denied(why.into()));
    }
    if method == "presence_ask" {
        let purpose = Purpose::named(text(p, "purpose")?)?;
        // The first key and the first phone are the person's own session's to ask. A change (a pool's card, a window, a join) is asked by the
        // person's own session too, and also by a live main session when the prompt is the system's own dialog, which no process can answer; where
        // the only prompt is a typed yes on a terminal, a session an agent started could open a terminal of its own and answer it, so it is refused.
        let person = person_session(node, &holder);
        if !person && !(matches!(purpose, Purpose::PoolKey | Purpose::Card) && may_ask(node, &holder)) {
            return Err(Error::Denied("Only the person's own session asks the person at this computer's prompt for that. An agent asks for a pool's change only where the computer shows the system's own dialog for the person's password or fingerprint; here the person runs the command in a terminal of their own.".into()));
        }
        // Where the system has no prompt of its own the question is put on the terminal of the process that asks, which the node finds itself.
        let terminal = if node.firsts.asks_on_own_terminal() {
            node.asking.peer.pid.and_then(tty::of).map(|t| t.to_string_lossy().into_owned()).ok_or_else(|| {
                Error::Denied("This computer asks you on your terminal, and this request does not come from a process that has one: run the command in a terminal of yours.".into())
            })?
        } else {
            text(p, "terminal")?.to_string()
        };
        if matches!(purpose, Purpose::PoolKey | Purpose::Card) {
            if !text(p, "public")?.is_empty() {
                return Err(Error::Invalid("A change names no key: the node makes the key a card enrols, and a card asks for no other.".into()));
            }
            node.firsts.ask_card(purpose, &by, text(p, "card")?, &terminal, now)?;
        } else {
            // This computer's own key is enrolled by this prompt unless one is enrolled already, even when a phone's key came first, so that the
            // computer never waits for a phone; a phone, or another key, is approved by a key that is enrolled.
            let held = if purpose == Purpose::Key { node.persons.has_computer_key() } else { !node.persons.is_empty() };
            if held {
                return Err(Error::Denied("A person key is enrolled already: a further key or phone is approved by one that is, not by this prompt.".into()));
            }
            node.firsts.ask(purpose, &by, text(p, "public")?, &terminal, now)?;
        }
    }
    let mut out = phone_status(node, &by, now, "0.0.0.0:7449".parse().expect("fixed phone address"))?;
    if out["state"] == "confirmed" && out["purpose"] == "pool_key" {
        if let Some(made) = node.firsts.take_key(&by) {
            out["public"] = json!(made.public);
            out["seed"] = json!(crate::fsutil::hex(&made.seed));
        }
    }
    Ok(out)
}

/// Whether the node puts a change to the person for ``holder``: the person's own session, or a live main session (not a subagent, not retired)
/// while nobody is asked (`auto`). A typed yes can be given by whoever owns a terminal, so it is never an agent's to ask.
pub(crate) fn may_ask(node: &mut Node, holder: &crate::identity::Holder) -> bool {
    if person_session(node, holder) {
        return true;
    }
    matches!(node.firsts.kind(), "os" | "auto") && node.host(&holder.board).ok().and_then(|hosted| hosted.names.get(&holder.name).map(|i| i.parent.is_empty() && !i.retired)).unwrap_or(false)
}

/// Whether ``holder`` is the person's own session: registered with the secret the node keeps for the person (`Node::register_person`), which
/// no claim sets.
pub(crate) fn person_session(node: &mut Node, holder: &crate::identity::Holder) -> bool {
    node.host(&holder.board).ok().and_then(|hosted| hosted.names.get(&holder.name).map(|i| i.person)).unwrap_or(false)
}

fn phone_status(node: &mut Node, by: &str, now: u64, listen: std::net::SocketAddr) -> Result<Value> {
    if node.firsts.phone_ready(by, now) && node.phone_port == 0 {
        if let Some(shared) = node.local_server.upgrade() {
            crate::phone::http::start_locked(node, shared, listen)?;
            node.pusher = Box::new(crate::phone::push::Web::start(&node.dir));
        }
    }
    let mut out = node.firsts.status(by, now);
    if out["state"] == "confirmed" && out["purpose"] == "phone" && out["secret"].is_string() {
        let secret = out["secret"].as_str().unwrap_or_default().to_string();
        out["link"] = json!(crate::phone::calls::link(node, &secret, out["expires_ms"].as_u64().unwrap_or(0)));
    }
    out["presence"] = json!(node.firsts.kind());
    Ok(out)
}

#[cfg(test)]
mod tests;
#[cfg(test)]
mod tests_asking;
