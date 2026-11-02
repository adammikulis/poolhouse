//! The step of the operating system that only the person at this computer can answer, and the cards it answers.
//!
//! Nothing can sign for the first key, so something else has to say that a person is at this computer and agrees. The node asks the
//! operating system itself, in a prompt of its own (the system's password or fingerprint dialog, which a process that runs as the
//! person cannot click or type into), and only a yes from it makes what the prompt was for: the one-time secret that adds the first key or the
//! first phone, or the ticket for a change the node drafted. Everything around it, which session asked and what happens next, can be done by
//! an agent: the prompt is the one step that cannot. What a yes makes lives in the node's memory, never in a file, so a program that can write
//! the state directory cannot leave one.
//!
//! Who may ask is proven, not claimed: only the person's own session (`register_person` with the secret the node keeps for the person,
//! `identity::person_secret`) drafts a change and raises its prompt, and every operation the person's session does without a grant is one
//! prompt for exactly that operation (the card of a pool, a removal, a join window, a join). What the prompt does not do is make an
//! unsandboxed program that runs as the person unable to sign: that is the weakness of a `computer` key, and why governance is held.
//!
//! There is no switch that turns the prompt off. A node started with no `--presence` has no way to ask and refuses (`Closed`). `--presence os`
//! asks with the operating system's own prompt where the system has one, and otherwise (a computer with no prompt of its own, `os::can_prompt`)
//! on the person's terminal, and what that makes counts as a typed yes; the launcher passes it only for the installed software on the real home
//! (`poolhouse.real_machine`), so a test, a checkout or a rig never raises a dialog. `--presence terminal` asks on the person's terminal device
//! that the node itself reads (`terminal::Terminal`). Tests of this crate put their own `Presence` on `Node::firsts` (a library field no binary
//! sets), and the pool check answers the terminal the way a person does.

pub mod card;
mod firsts;
mod ledger;
mod os;
pub mod terminal;

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::node::Node;

pub use firsts::Firsts;
pub use ledger::Ledger;
pub use os::{can_prompt, default_presence, Closed, Mode, Os};
pub(crate) use os::{on_real_home, plain};

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
    /// None while the person has not answered, then whether the answer was yes.
    fn poll(&mut self) -> Option<bool>;

    /// How many milliseconds ago the person answered, once they have: what a yes makes lives from the yes and not from the poll that found
    /// it. A prompt that cannot say (a stand-in answering as it is asked) says 0.
    fn age_ms(&self) -> u64 {
        0
    }
}

/// Where the question is put to the person.
pub trait Presence: Send {
    /// `os` for the operating system's own prompt, `terminal` for the person's terminal device.
    fn kind(&self) -> &'static str;
    /// Open a prompt that says ``what``; ``terminal`` is the terminal device the asker named, which only a terminal prompt uses.
    fn begin(&mut self, what: &str, terminal: &str) -> Result<Box<dyn Asking>>;
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
        if matches!(purpose, Purpose::PoolKey | Purpose::Card) {
            if !person_session(node, &holder) {
                return Err(Error::Denied("Only the person's own session asks for a change to be answered at the computer's prompt; an agent asks the person to run the command.".into()));
            }
            if !text(p, "public")?.is_empty() {
                return Err(Error::Invalid("A change names no key: the node makes the key a card enrols, and a card asks for no other.".into()));
            }
            node.firsts.ask_card(purpose, &by, text(p, "card")?, text(p, "terminal")?, now)?;
        } else {
            if !node.persons.is_empty() {
                return Err(Error::Denied("A person key is enrolled already: a further key or phone is approved by one that is, not by this prompt.".into()));
            }
            node.firsts.ask(purpose, &by, text(p, "public")?, text(p, "terminal")?, now)?;
        }
    }
    let mut out = phone_status(node, &by, now, "0.0.0.0:7449".parse().expect("fixed phone address"))?;
    if out["state"] == "confirmed" && out["purpose"] == "pool_key" {
        if let Some(made) = node.firsts.take_enrolment(&by) {
            let claim = node.firsts.pool_key_claim();
            node.persons.add_claimed_key(crate::grants::person::ED25519, &made.public, "this computer's key for a pool", now, claim)?;
            out["public"] = json!(made.public);
            out["seed"] = json!(crate::fsutil::hex(&made.seed));
        }
    }
    Ok(out)
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
