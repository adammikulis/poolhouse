//! A pool of one computer's key, end to end with no phone and no terminal: it is founded with one answer at the computer's prompt, the founding
//! computer admits a second device with a code the person alone sees and no further answer, and a removal is one more answer at the prompt.
//! A stand-in presence plays the person at the prompt (`Node::firsts`, the crate's seam); the second node is a real node over a real socket.

mod kit;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use kit::netkit::{device, Dev};
use poolhouse_node::error::Result as NodeResult;
use poolhouse_node::fsutil::{sha256_hex, unhex};
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::presence::{Asking, Firsts, Presence};
use serde_json::{json, Value};

const ANN: u8 = 7;

/// A person at the prompt who says yes to everything the node shows, and counts the prompts.
struct Yes {
    raised: Arc<AtomicUsize>,
}

struct Said;

impl Asking for Said {
    fn poll(&mut self) -> Option<bool> {
        Some(true)
    }
}

impl Presence for Yes {
    fn kind(&self) -> &'static str {
        "os"
    }

    fn begin(&mut self, _what: &str, _terminal: &str) -> NodeResult<Box<dyn Asking>> {
        self.raised.fetch_add(1, Ordering::SeqCst);
        Ok(Box::new(Said))
    }
}

fn person(dev: &Dev, session: &str) -> String {
    dev.ok("register_person", "demo", "", json!({"session": session}))["token"].as_str().unwrap().to_string()
}

/// Answer the card ``card`` at the prompt as the person ``token`` and spend the ticket on ``bytes``; the key signs them.
fn signed_under_a_ticket(dev: &Dev, token: &str, purpose: &str, card: &str, bytes: &[u8]) -> String {
    let asked = dev.ok("presence_ask", "", token, json!({"purpose": purpose, "public": RigSigner::public(ANN), "card": card, "terminal": ""}));
    let said = if asked["state"] == "confirmed" { asked } else { dev.ok("presence_status", "", token, json!({})) };
    assert_eq!(said["state"], "confirmed", "{said}");
    dev.ok("ticket_spend", "", token, json!({"ticket": said["secret"], "payload": sha256_hex(bytes)}));
    RigSigner::sign_as(ANN, bytes)
}

#[test]
fn a_pool_with_no_phone_is_founded_joined_and_pruned_by_the_computers_key() {
    let (a, b) = (device(), device());
    let raised = Arc::new(AtomicUsize::new(0));
    {
        let mut node = a.node.lock().unwrap();
        node.firsts = Firsts::new(Box::new(Yes { raised: raised.clone() }));
        node.rig_bind_device_key();
    }
    let me = person(&a, "the-person");
    // Found: one answer is the key, the ticket and the grant to found.
    let draft = a.ok("pool_draft_found", "", &me, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": RigSigner::public(ANN), "person": "owner", "label": "this computer"}]}));
    assert_eq!(draft["counting"], true, "{draft}");
    let bytes = unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap();
    let acceptance = signed_under_a_ticket(&a, &me, "pool_key", draft["card"].as_str().unwrap(), &bytes);
    let mut charter = draft["charter"].clone();
    charter["admins"][0]["acceptance"] = json!(acceptance);
    let founded = a.ok("pool_found", "", &me, json!({"charter": charter}));
    assert_eq!(founded["epoch"], 1, "{founded}");
    assert_eq!(raised.load(Ordering::SeqCst), 1, "founding is one prompt");

    // Join: the founding computer is the pool's delegate for thirty days, so the person sees a code and nobody is asked again.
    let code = a.ok("add_device", "", &me, json!({}))["code"].as_str().unwrap().to_string();
    let guest = person(&b, "the-guest-person");
    let joined = b.ok("join_pool", "", &guest, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": code}));
    assert!(joined["joined"].as_bool().unwrap_or(true), "{joined}");
    assert!(a.is_member(&b.fp()) && b.is_member(&a.fp()), "both devices list each other");
    assert_eq!(raised.load(Ordering::SeqCst), 1, "a join with a live delegate asks nobody");

    // An agent of the founding computer cannot open a window, and cannot learn a code.
    let (_, agent) = a.session("demo", "an-agent");
    let refused = a.call("add_device", "", &agent, json!({}));
    assert_eq!(refused["ok"], false, "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains("Only you open a window"), "{refused}");

    // Remove: one more answer, for exactly that act.
    let removal = a.ok("pool_draft", "", &me, json!({"kind": "remove", "body": {"fingerprint": b.fp()}}));
    let sign_bytes = unhex(removal["sign_bytes"].as_str().unwrap()).unwrap();
    let sig = signed_under_a_ticket(&a, &me, "card", removal["card"].as_str().unwrap(), &sign_bytes);
    let mut act = removal["act"].clone();
    act["signatures"] = json!([{"kind": "admin", "key": RigSigner::public(ANN), "sig": sig}]);
    let submitted = a.ok("pool_submit", "", &me, json!({"act": act}));
    assert_eq!(submitted["status"], "effective", "{submitted}");
    assert_eq!(raised.load(Ordering::SeqCst), 2, "founding and the removal: two prompts in all");
    let gone: Value = a.member(&b.fp()).unwrap();
    assert_eq!(gone["status"], "revoked", "{gone}");
}
