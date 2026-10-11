//! The phone methods of the local API: where a phone finds this node, a code for a further phone, and the phones held.

use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::node::Node;
use crate::phone::enrol::{b64url, make_offer, OFFER_TTL_MS};
use crate::phone::shown;

pub const METHODS: [&str; 3] = ["phone_info", "phone_offer", "phones"];
const MOST_OFFERS_PER_HOUR: usize = 3;
const MOST_ENDPOINTS: usize = 4;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "phone_info" => &["secret"],
        "phone_offer" | "phones" => &[],
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
            node.tokens.resolve(token).ok_or_else(|| Error::Denied("A registered session asks for a code for a phone.".into()))?;
            if node.persons.is_empty() {
                return Err(Error::Denied("The first phone is added by a person at the computer with `poolhouse-workspace phone add`; this works once one phone or key is added.".into()));
            }
            if node.phone_port == 0 {
                return Err(Error::Invalid("This device is not listening for phones; `poolhouse-workspace phone add` turns that on.".into()));
            }
            if !node.rates.permits("phone offer", MOST_OFFERS_PER_HOUR, Duration::from_secs(3600)) {
                return Err(Error::Quota(format!("{MOST_OFFERS_PER_HOUR} codes an hour is the most this device makes for phones; try again later.")));
            }
            node.rates.record("phone offer", Duration::from_secs(3600));
            let secret = make_offer(&node.dir, node.now_ms())?;
            Ok(info(node, &secret))
        }
        _ => {
            node.tokens.resolve(token).ok_or_else(|| Error::Denied("A registered session lists the phones.".into()))?;
            Ok(json!({"phones": node.phones.all().map(|f| shown(f, "")).collect::<Vec<_>>()}))
        }
    }
}
