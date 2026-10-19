//! The phone methods of the local API: where a phone finds this node, a code for a further phone, and the phones held.

use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::node::Node;
use crate::approvals::fatigue::{requester, Standing};
use crate::phone::enrol::{ask_to_remove, b64url, make_offer_for, offer_is_live, OFFER_TTL_MS};
use crate::phone::shown;

pub const METHODS: [&str; 4] = ["phone_info", "phone_offer", "phones", "phone_remove"];
const MOST_OFFERS_PER_HOUR: usize = 3;
const MOST_REMOVALS_PER_HOUR: usize = 6;
const MOST_ENDPOINTS: usize = 4;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "phone_info" => &["secret"],
        "phone_offer" | "phones" => &[],
        "phone_remove" => &["phone"],
        _ => return None,
    })
}

/// The methods that name no board.
pub fn boardless(method: &str) -> bool {
    METHODS.contains(&method)
}

/// The endpoints a phone can try: this machine's addresses on the local network and the tailnet, with the port the node listens on.
pub fn endpoints(port: u16) -> Vec<String> {
    crate::netif::phone_addresses().into_iter().take(MOST_ENDPOINTS).map(|a| format!("https://{a}:{port}")).collect()
}

/// The link a person scans: everything the phone needs to find this node, be sure it is the right one and add itself.
pub fn link(node: &Node, secret: &str, expires_ms: u64) -> String {
    let device = node.members.get(&node.cert.fingerprint()).map(|d| d.name.clone()).unwrap_or_default();
    let body = json!({"v": 1, "pin": node.phone_cert.fingerprint(), "endpoints": endpoints(node.phone_port), "secret": secret, "expires": expires_ms / 1000,
                      "name": device, "policy": node.members.policy.name()});
    format!("poolhouse://add-phone?data={}", b64url(body.to_string().as_bytes()))
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text."))),
    }
}

fn info(node: &Node, secret: &str) -> Value {
    let device = node.members.get(&node.cert.fingerprint()).map(|d| d.name.clone()).unwrap_or_default();
    let expires = node.now_ms() + OFFER_TTL_MS;
    let mut out = json!({"listening": node.phone_port != 0, "port": node.phone_port, "pin": node.phone_cert.fingerprint(), "endpoints": endpoints(node.phone_port),
                         "device": node.cert.fingerprint(), "name": device, "policy": node.members.policy.name(), "keys": node.persons.all().len(),
                         "phones": node.phones.held()});
    if !secret.is_empty() {
        out["link"] = json!(link(node, secret, expires));
        out["expires_ms"] = json!(expires);
    }
    out
}

pub fn call(node: &mut Node, method: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    match method {
        "phone_info" => Ok(info(node, text(p, "secret")?)),
        "phone_offer" => {
            let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("A registered session asks for a code for a phone.".into()))?;
            if node.persons.is_empty() {
                return Err(Error::Denied("The first phone is added with `poolhouse-workspace phone add`, after a yes at the prompt of this computer; this works once one phone or key is added.".into()));
            }
            let (asker, now) = (format!("{}/{}", holder.board, holder.name), node.now_ms());
            if let Some(until) = node.fatigue.refused_key(&format!("offer:{asker}"), now) {
                return Err(Error::Denied(format!("A code this session made was not approved, so it is blocked from asking for more codes until {}.", crate::approvals::content::utc(until))));
            }
            let root = node.host(&holder.board)?.names.root(&holder.name);
            if matches!(node.fatigue.standing(&requester(&holder.board, &holder.name, &root), "phone_add", now), Standing::Held { .. } | Standing::Refused { .. }) {
                return Err(Error::Denied("A session that is held or blocked asks for no codes for phones either.".into()));
            }
            if node.phone_port == 0 {
                return Err(Error::Invalid("This device is not listening for phones; `poolhouse-workspace phone add` turns that on.".into()));
            }
            if offer_is_live(node, node.now_ms()) {
                return Err(Error::Denied("A code for a phone is already waiting to be scanned; use it or wait for it to run out.".into()));
            }
            let key = format!("phone offer {}/{}", holder.board, holder.name);
            if !node.rates.permits(&key, MOST_OFFERS_PER_HOUR, Duration::from_secs(3600)) {
                return Err(crate::limits::refuse("One session has asked for as many codes as it may; try again later.", format!("{MOST_OFFERS_PER_HOUR} an hour"), "MOST_OFFERS_PER_HOUR", "phone/calls.rs"));
            }
            node.rates.record(&key, Duration::from_secs(3600));
            let secret = make_offer_for(node, now, &format!("the session {} on board {}", holder.name, holder.board), &asker)?;
            Ok(info(node, &secret))
        }
        "phone_remove" => {
            let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("A registered session asks to remove a phone.".into()))?;
            let key = format!("phone remove {}/{}", holder.board, holder.name);
            if !node.rates.permits(&key, MOST_REMOVALS_PER_HOUR, Duration::from_secs(3600)) {
                return Err(crate::limits::refuse("One session has asked to remove as many phones as it may; try again later.", format!("{MOST_REMOVALS_PER_HOUR} an hour"), "MOST_REMOVALS_PER_HOUR", "phone/calls.rs"));
            }
            let by = format!("The session {} on board {}", holder.name, holder.board);
            let id = ask_to_remove(node, &by, &format!("{}/{}", holder.board, holder.name), text(p, "phone")?)?;
            node.rates.record(&key, Duration::from_secs(3600));
            node.approvals.get(&id).cloned().map(|a| crate::approvals::decide::view(node, &a)).ok_or_else(|| Error::Invalid("There is no request with that id.".into()))
        }
        _ => {
            node.tokens.resolve(token).ok_or_else(|| Error::Denied("A registered session lists the phones.".into()))?;
            Ok(json!({"phones": node.phones.all().map(|f| shown(f, "")).collect::<Vec<_>>()}))
        }
    }
}
