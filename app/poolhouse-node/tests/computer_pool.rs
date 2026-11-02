//! A pool of one computer's key, end to end with no phone and no terminal: it is founded with one answer at the computer's prompt, the founding
//! computer admits a second device with a window the person opened with one more answer for exactly that window, and a removal is one more
//! answer at the prompt. A stand-in presence plays the person at the prompt (`Node::firsts`, the crate's seam); the second node is a real node over
//! a real socket. No session is the person's because it says so: the person's own session holds the secret the node keeps for the person, and even
//! it opens a window or joins only with a yes for that very operation.

mod kit;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use ed25519_dalek::{Signer as _, SigningKey};
use kit::netkit::{device, Dev};
use poolhouse_node::error::Result as NodeResult;
use poolhouse_node::fsutil::{hex, sha256_hex, unhex};
use poolhouse_node::presence::{Asking, Firsts, Presence};
use serde_json::{json, Value};

/// A person at the prompt who says yes to everything the node shows, and counts the prompts and keeps the sentences.
struct Yes {
    raised: Arc<AtomicUsize>,
    shown: Arc<Mutex<Vec<String>>>,
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

    fn begin(&mut self, what: &str, _terminal: &str) -> NodeResult<Box<dyn Asking>> {
        self.raised.fetch_add(1, Ordering::SeqCst);
        self.shown.lock().unwrap().push(what.into());
        Ok(Box::new(Said))
    }
}

fn at_the_prompt(dev: &Dev) -> (Arc<AtomicUsize>, Arc<Mutex<Vec<String>>>) {
    let (raised, shown) = (Arc::new(AtomicUsize::new(0)), Arc::new(Mutex::new(Vec::new())));
    let mut node = dev.node.lock().unwrap();
    node.firsts = Firsts::new(Box::new(Yes { raised: raised.clone(), shown: shown.clone() }));
    node.rig_bind_device_key();
    (raised, shown)
}

fn person(dev: &Dev, session: &str) -> String {
    let proof = kit::proof_in(&dev.node.lock().unwrap().dir);
    dev.ok("register_person", "demo", "", json!({"session": session, "proof": proof}))["token"].as_str().unwrap().to_string()
}

/// The person's yes for ``card`` as ``token``: the node's reply, which carries the ticket (and, for a key the card made, its seed).
fn yes_for(dev: &Dev, token: &str, purpose: &str, card: &str) -> Value {
    let asked = dev.ok("presence_ask", "", token, json!({"purpose": purpose, "card": card, "terminal": ""}));
    let said = if asked["state"] == "confirmed" { asked } else { dev.ok("presence_status", "", token, json!({})) };
    assert_eq!(said["state"], "confirmed", "{said}");
    said
}

/// The ticket for opening a window (or joining) with ``args`` as ``token``.
fn ticket_for(dev: &Dev, token: &str, op: &str, args: Value) -> String {
    let drawn = dev.ok("op_draft", "", token, json!({"op": op, "args": args}));
    yes_for(dev, token, "card", drawn["card"].as_str().unwrap())["secret"].as_str().unwrap().to_string()
}

fn sign(seed: &str, bytes: &[u8]) -> String {
    hex(&SigningKey::from_bytes(&unhex(seed).unwrap().try_into().unwrap()).sign(bytes).to_bytes())
}

/// Found the pool ``name`` on ``dev`` as the person ``me``; the seed of the key the node made for it.
fn found(dev: &Dev, me: &str, name: &str) -> String {
    let draft = dev.ok("pool_draft_found", "", me, json!({"title": name, "policy": "secure", "project": "", "keys": [{"computer": true, "person": "owner", "label": "this computer"}]}));
    assert_eq!(draft["counting"], true, "{draft}");
    let said = yes_for(dev, me, "pool_key", draft["card"].as_str().unwrap());
    let seed = said["seed"].as_str().unwrap().to_string();
    let bytes = unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap();
    dev.ok("ticket_spend", "", me, json!({"ticket": said["secret"], "payload": sha256_hex(&bytes)}));
    let mut charter = draft["charter"].clone();
    charter["admins"][0]["acceptance"] = json!(sign(&seed, &bytes));
    let founded = dev.ok("pool_found", "", me, json!({"charter": charter}));
    assert_eq!(founded["epoch"], 1, "{founded}");
    seed
}

#[test]
fn a_pool_with_no_phone_is_founded_joined_and_pruned_by_the_computers_key() {
    let (a, b) = (device(), device());
    let (raised, shown) = at_the_prompt(&a);
    let (joiner_raised, _) = at_the_prompt(&b);
    let me = person(&a, "the-person");
    // Found: one answer is the key, the ticket and the grant to found.
    let seed = found(&a, &me, "home");
    assert_eq!(raised.load(Ordering::SeqCst), 1, "founding is one prompt");
    assert!(shown.lock().unwrap()[0].contains("30 days"), "the prompt says that this computer may admit devices for 30 days");

    // A window: the founding computer is the pool's delegate for thirty days, so no admit is asked of anyone, but opening the window is one answer
    // for that window, and the code comes back to the session that was given that answer.
    let window = json!({"ttl_s": 120});
    let ticket = ticket_for(&a, &me, "add_device", window.clone());
    let code = a.ok("add_device", "", &me, json!({"ttl_s": 120, "ticket": ticket}))["code"].as_str().unwrap().to_string();
    assert_eq!(raised.load(Ordering::SeqCst), 2, "founding and one window");

    // Join: the joining computer's person says yes to joining at that address, and types the code.
    let guest = person(&b, "the-guest-person");
    let join = json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": code});
    let joined = b.ok("join_pool", "", &guest, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": code, "ticket": ticket_for(&b, &guest, "join_pool", join)}));
    assert!(joined["joined"].as_bool().unwrap_or(true), "{joined}");
    assert_eq!(joined["approved"], "approved from this computer; a program running as you could approve it without asking", "the joiner's screen says how this pool is approved: {joined}");
    assert!(a.is_member(&b.fp()) && b.is_member(&a.fp()), "both devices list each other");
    assert_eq!(raised.load(Ordering::SeqCst), 2, "a join with a live delegate asks the founding computer nothing more");
    assert_eq!(joiner_raised.load(Ordering::SeqCst), 1, "the joining computer's person answered once");

    // An agent of the founding computer cannot open a window, and cannot learn a code.
    let (_, agent) = a.session("demo", "an-agent");
    let refused = a.call("add_device", "", &agent, json!({}));
    assert_eq!(refused["ok"], false, "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains("Only you open a window"), "{refused}");

    // Remove: one more answer, for exactly that act.
    let removal = a.ok("pool_draft", "", &me, json!({"kind": "remove", "body": {"fingerprint": b.fp()}}));
    let sign_bytes = unhex(removal["sign_bytes"].as_str().unwrap()).unwrap();
    let said = yes_for(&a, &me, "card", removal["card"].as_str().unwrap());
    a.ok("ticket_spend", "", &me, json!({"ticket": said["secret"], "payload": sha256_hex(&sign_bytes)}));
    let key = hex(SigningKey::from_bytes(&unhex(&seed).unwrap().try_into().unwrap()).verifying_key().as_bytes());
    let mut act = removal["act"].clone();
    act["signatures"] = json!([{"kind": "admin", "key": key, "sig": sign(&seed, &sign_bytes)}]);
    let submitted = a.ok("pool_submit", "", &me, json!({"act": act}));
    assert_eq!(submitted["status"], "effective", "{submitted}");
    assert_eq!(raised.load(Ordering::SeqCst), 3, "founding, one window and the removal");
    let gone: Value = a.member(&b.fp()).unwrap();
    assert_eq!(gone["status"], "revoked", "{gone}");
    let sentence = shown.lock().unwrap()[2].clone();
    assert!(sentence.contains("remove the device") && !sentence.contains("laptop"), "the removal names the device by the words of its fingerprint: {sentence}");
}

#[test]
fn a_session_that_says_it_is_the_person_opens_no_window_and_joins_no_pool() {
    let (a, b) = (device(), device());
    let (raised, _) = at_the_prompt(&a);
    at_the_prompt(&b);
    let me = person(&a, "the-person");
    found(&a, &me, "home");
    let before = raised.load(Ordering::SeqCst);
    // The attack of the review: a process of the same user registers as the person and asks for a window and a join with no grant, on any pool.
    let forged = a.call("register_person", "demo", "", json!({"session": "forged"}));
    assert_eq!(forged["ok"], false, "{forged}");
    let guessed = a.call("register_person", "demo", "", json!({"session": "forged", "proof": "ab".repeat(32)}));
    assert_eq!(guessed["ok"], false, "{guessed}");
    let as_agent = a.call("register", "demo", "", json!({"model": "person", "harness": "poolhouse-ui", "session": "forged"}));
    assert_eq!(as_agent["ok"], false, "{as_agent}");
    let (_, agent) = a.session("demo", "an-agent");
    for token in [agent.as_str(), ""] {
        let window = a.call("add_device", "", token, json!({}));
        assert_eq!(window["ok"], false, "{window}");
        let with_a_ticket = a.call("add_device", "", token, json!({"ticket": "a-guess"}));
        assert_eq!(with_a_ticket["ok"], false, "a ticket is for the person's own session: {with_a_ticket}");
        assert!(a.status()["join_code_open"] != json!(true), "no window opened");
        let join = a.call("join_pool", "", token, json!({"host": "127.0.0.1", "port": b.net.port, "passphrase": "123456"}));
        assert_eq!(join["ok"], false, "{join}");
    }
    // Even the person's own session, which does hold the secret, holds no grant: with no yes for the operation it opens nothing and joins nothing.
    for params in [json!({}), json!({"ticket": "guess"}), json!({"ticket": ""})] {
        let window = a.call("add_device", "", &me, params.clone());
        assert_eq!(window["ok"], false, "{params}: {window}");
    }
    let guest = person(&b, "the-guest");
    let join = b.call("join_pool", "", &guest, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": "123456"}));
    assert_eq!(join["ok"], false, "{join}");
    assert!(join["error"]["grant"].is_object(), "the refusal names the grant the session lacks: {join}");
    assert!(!b.is_member(&a.fp()) && !a.is_member(&b.fp()));
    assert_eq!(raised.load(Ordering::SeqCst), before, "none of it raised a prompt");
}

#[test]
fn a_yes_opens_one_window_for_the_session_it_was_given_to_and_nothing_else() {
    let a = device();
    at_the_prompt(&a);
    let me = person(&a, "the-person");
    found(&a, &me, "home");
    let other = person(&a, "the-other-person");
    let ticket = ticket_for(&a, &me, "add_device", json!({"ttl_s": 60}));
    let by_another = a.call("add_device", "", &other, json!({"ttl_s": 60, "ticket": ticket}));
    assert_eq!(by_another["ok"], false, "a ticket is for the session it was given to: {by_another}");
    let other_window = a.call("add_device", "", &me, json!({"ttl_s": 600, "ticket": ticket}));
    assert_eq!(other_window["ok"], false, "a yes for sixty seconds opens no window of ten minutes: {other_window}");
    let own_code = a.call("add_device", "", &me, json!({"ttl_s": 60, "passphrase": "my-own-code", "ticket": ticket}));
    assert_eq!(own_code["ok"], false, "nor one with a code the yes did not name: {own_code}");
    let opened = a.ok("add_device", "", &me, json!({"ttl_s": 60, "ticket": ticket}));
    assert_eq!(opened["expires_in_s"], 60, "{opened}");
    let again = a.call("add_device", "", &me, json!({"ttl_s": 60, "ticket": ticket}));
    assert_eq!(again["ok"], false, "a yes opens one window: {again}");
    let no_card = a.call("op_draft", "", &me, json!({"op": "pool_leave", "args": {}}));
    assert_eq!(no_card["ok"], false, "{no_card}");
}
