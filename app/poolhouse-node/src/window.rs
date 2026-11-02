//! What the person's own session does with no grant: open a join window (`add_device`) and join a pool (`join_pool`).
//!
//! Neither is allowed because of who asks. Each is one answer at the computer's own prompt for exactly that operation: the node drafts a card
//! from the operation's own parameters (`op_draft`), writes the sentence the prompt shows, and the person's yes makes a ticket for the SHA-256
//! of the operation's bytes. The call then carries the ticket, and the node recomputes the bytes from the call it received and spends the
//! ticket on them once, so a ticket opens one window or joins one pool and nothing else, within two minutes of the yes. A session that holds a
//! grant for the action (an agent whose phone approved it) needs no ticket, and a session with neither does nothing.

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fsutil::sha256_hex;
use crate::node::Node;
use crate::presence::card::Draft;
use crate::row::canonical;

/// The operations a ticket opens.
pub const OPS: [&str; 2] = ["add_device", "join_pool"];
const DOMAIN: &str = "poolhouse/person-op/v1\n";
/// How long a window lasts when the call names none, and the most it may last.
const WINDOW_S: u64 = 120;
const MOST_WINDOW_S: u64 = 600;
/// The most characters of a host the prompt names.
const MOST_HOST: usize = 45;

/// What the person says yes to: the bytes whose hash the ticket names, and the sentence the prompt shows.
pub struct Op {
    pub bytes: Vec<u8>,
    pub sentence: String,
}

fn text<'a>(args: &'a Map<String, Value>, key: &str) -> &'a str {
    args.get(key).and_then(Value::as_str).unwrap_or("")
}

fn digest(secret: &str) -> String {
    sha256_hex(secret.as_bytes())
}

/// Whether ``args`` hold only the params the call ``op`` takes besides its ticket.
fn only(args: &Map<String, Value>, allowed: &[&str]) -> Result<()> {
    match args.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(k) => Err(Error::Invalid(format!("{} is not a param of that operation", k.chars().take(40).collect::<String>()))),
        None => Ok(()),
    }
}

/// The operation ``op`` with ``args`` (the params of the call, less its ticket) in the pool ``pool`` called ``name`` (empty when the device is in none).
pub fn describe(op: &str, pool: &str, name: &str, args: &Map<String, Value>) -> Result<Op> {
    let (body, sentence) = match op {
        "add_device" => {
            only(args, &["passphrase", "ttl_s", "presigned"])?;
            let secs = args.get("ttl_s").and_then(Value::as_u64).unwrap_or(WINDOW_S).clamp(1, MOST_WINDOW_S);
            if pool.is_empty() {
                return Err(Error::Invalid("This device is in no pool, so there is nothing for another device to join: make a pool first.".into()));
            }
            let body = json!({"op": op, "pool": pool, "passphrase": digest(text(args, "passphrase")), "ttl_s": secs, "presigned": args.get("presigned") == Some(&Value::Bool(true))});
            (body, format!("Poolhouse - let a new device join the pool {name} with a code, for {} minutes. Say yes only if you are adding a device now.", secs.div_ceil(60)))
        }
        "join_pool" => {
            only(args, &["host", "port", "passphrase", "fingerprint"])?;
            let host = text(args, "host");
            if host.is_empty() || host.chars().count() > MOST_HOST {
                return Err(Error::Invalid("A pool is joined at a host of at most 45 letters, digits, dots and dashes.".into()));
            }
            let body = json!({"op": op, "host": host, "port": args.get("port").and_then(Value::as_u64).unwrap_or(0), "passphrase": digest(text(args, "passphrase")),
                              "fingerprint": text(args, "fingerprint")});
            (body, format!("Poolhouse - put this computer in the pool at {host}. Say yes only if you are joining it now."))
        }
        _ => return Err(Error::Invalid("a ticket opens add_device or join_pool".into())),
    };
    Ok(Op { bytes: format!("{DOMAIN}{}", canonical(&body)).into_bytes(), sentence })
}

impl Node {
    /// The pool this device is in and its name, or two empty words.
    fn pool_words(&self) -> (String, String) {
        self.members.gov().map(|g| (g.id.clone(), g.state().chain.latest().charter.name.clone())).unwrap_or_default()
    }

    /// Draft the card for the operation ``op`` of ``args`` for the person's session ``by``: the sentence the prompt shows and the bytes a yes
    /// is for. `card_why` says why there is no card when the prompt could not show the operation whole.
    pub fn op_draft(&mut self, by: &str, op: &str, args: &Value) -> Result<Value> {
        let args = args.as_object().ok_or_else(|| Error::Invalid("args is the params of the operation".into()))?;
        let (pool, name) = self.pool_words();
        let done = describe(op, &pool, &name, args)?;
        let card = self.firsts.cards.add(by, Draft { sentence: &done.sentence, payloads: &[done.bytes.clone()], founding: None, generated: None }, self.now_ms());
        let (card_id, card_why) = match card {
            Ok(id) => (Value::String(id), Value::Null),
            Err(why) => (Value::Null, Value::String(why.into())),
        };
        Ok(json!({"card": card_id, "card_why": card_why, "sentence": done.sentence}))
    }

    /// Spend the ticket ``ticket`` of ``by`` on the operation ``op`` of ``args`` (the params of the call, less its ticket).
    pub fn spend_op(&mut self, by: &str, ticket: &str, op: &str, args: &Map<String, Value>) -> Result<()> {
        let (pool, name) = self.pool_words();
        let done = describe(op, &pool, &name, args)?;
        let now = self.now_ms();
        self.firsts.spend_payload(by, ticket, &sha256_hex(&done.bytes), now).map_err(|why| Error::Denied(why.into()))
    }
}
