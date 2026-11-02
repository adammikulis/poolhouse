//! A pool of one computer's key, end to end with no phone and no terminal: it is founded with one answer at the computer's prompt, the founding
//! computer admits a second device with a window the person opened with one more answer for exactly that window, and a removal is one more
//! answer at the prompt. A stand-in presence plays the person at the prompt (`Node::firsts`, the crate's seam); the second node is a real node over
//! a real socket. No session is the person's because it says so: the person's own session holds the secret the node keeps for the person, and even
//! it opens a window or joins only with a yes for that very operation.

mod kit;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use ed25519_dalek::{Signer as _, SigningKey};
use kit::govnode::pass;
use kit::netkit::{device, Dev};
use poolhouse_node::error::Result as NodeResult;
use poolhouse_node::fsutil::{hex, sha256_hex, unhex};
use poolhouse_node::presence::{Asking, Firsts, Presence};
use serde_json::{json, Value};

/// A person at the prompt who says yes to everything the node shows, and counts the prompts and keeps the sentences.
struct Yes {
    kind: &'static str,
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
        self.kind
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
    node.firsts = Firsts::new(Box::new(Yes { kind: "os", raised: raised.clone(), shown: shown.clone() }));
    node.rig_bind_device_key();
    (raised, shown)
}

/// ``dev`` is a computer with no prompt of its own, whose person is asked on a terminal and whose device key is a file: the pool it founds is approved
/// by a typed yes.
fn at_a_terminal(dev: &Dev) -> (Arc<AtomicUsize>, Arc<Mutex<Vec<String>>>) {
    let (raised, shown) = (Arc::new(AtomicUsize::new(0)), Arc::new(Mutex::new(Vec::new())));
    dev.node.lock().unwrap().firsts = Firsts::new(Box::new(Yes { kind: "terminal", raised: raised.clone(), shown: shown.clone() }));
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

/// Open a window on ``dev`` as the person ``me``: the one yes for the window card also signs, with the key of ``seed``, the act that lets this device
/// admit what proves the code; the code the call gives back.
fn open_window(dev: &Dev, me: &str, seed: &str, args: Value) -> String {
    let drawn = dev.ok("op_draft", "", me, json!({"op": "add_device", "args": args}));
    let ticket = yes_for(dev, me, "card", drawn["card"].as_str().unwrap())["secret"].as_str().unwrap().to_string();
    let bytes = unhex(drawn["window"]["sign_bytes"].as_str().unwrap()).unwrap();
    dev.ok("ticket_spend", "", me, json!({"ticket": ticket, "payload": sha256_hex(&bytes)}));
    let mut act = drawn["window"]["act"].clone();
    let key = hex(SigningKey::from_bytes(&unhex(seed).unwrap().try_into().unwrap()).verifying_key().as_bytes());
    act["signatures"] = json!([{"kind": "admin", "key": key, "sig": sign(seed, &bytes)}]);
    let mut params = args.as_object().unwrap().clone();
    params.insert("ticket".into(), json!(ticket));
    params.insert("window".into(), act);
    dev.ok("add_device", "", me, Value::Object(params))["code"].as_str().unwrap().to_string()
}

/// ``guest`` joins the pool at ``host`` with ``code``, with the yes of its person for exactly that join.
fn join(guest: &Dev, who: &str, host: &Dev, code: &str) -> Value {
    let args = json!({"host": "127.0.0.1", "port": host.net.port, "passphrase": code});
    let mut params = args.as_object().unwrap().clone();
    params.insert("ticket".into(), json!(ticket_for(guest, who, "join_pool", args)));
    guest.ok("join_pool", "", who, Value::Object(params))
}

/// The person ``me`` of ``dev`` removes the device ``fingerprint`` with the key of ``seed``, at one prompt for exactly that removal.
fn remove(dev: &Dev, me: &str, seed: &str, fingerprint: &str) {
    let removal = dev.ok("pool_draft", "", me, json!({"kind": "remove", "body": {"fingerprint": fingerprint}}));
    let sign_bytes = unhex(removal["sign_bytes"].as_str().unwrap()).unwrap();
    let said = yes_for(dev, me, "card", removal["card"].as_str().unwrap());
    dev.ok("ticket_spend", "", me, json!({"ticket": said["secret"], "payload": sha256_hex(&sign_bytes)}));
    let key = hex(SigningKey::from_bytes(&unhex(seed).unwrap().try_into().unwrap()).verifying_key().as_bytes());
    let mut act = removal["act"].clone();
    act["signatures"] = json!([{"kind": "admin", "key": key, "sig": sign(seed, &sign_bytes)}]);
    assert_eq!(dev.ok("pool_submit", "", me, json!({"act": act}))["status"], "effective");
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

#[test]
fn a_pool_approved_by_a_typed_yes_is_founded_opens_a_window_admits_a_second_device_and_removes_it() {
    let (a, b) = (device(), device());
    let (raised, shown) = at_a_terminal(&a);
    at_a_terminal(&b);
    let me = person(&a, "the-person");
    let seed = found(&a, &me, "home");
    let gov = a.status()["governance"].clone();
    assert_eq!((gov["floors"]["sensitive"].clone(), gov["delegates"].clone()), (json!("weak"), json!([])), "approved by a typed yes, and no device is a delegate: {gov}");
    assert!(shown.lock().unwrap()[0].contains("typed yes") && !shown.lock().unwrap()[0].contains("30 days"), "{:?}", shown.lock().unwrap());
    // the ticket alone opens nothing here: no device is a delegate and the node holds no key
    let other = person(&a, "another-terminal");
    let drawn = a.ok("op_draft", "", &other, json!({"op": "add_device", "args": {}}));
    let ticket = yes_for(&a, &other, "card", drawn["card"].as_str().unwrap())["secret"].as_str().unwrap().to_string();
    let refused = a.call("add_device", "", &other, json!({"ticket": ticket}));
    assert_eq!(refused["ok"], false, "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains("I need your OK"), "{refused}");
    // the person's yes for the window is also the key's signature of the act that lets this device admit what proves the code
    let code = open_window(&a, &me, &seed, json!({}));
    let guest = person(&b, "the-guest-person");
    let joined = join(&b, &guest, &a, &code);
    assert!(joined["approved"].as_str().unwrap().contains("typed yes"), "the joiner is told how the pool is approved: {joined}");
    assert!(a.is_member(&b.fp()) && b.is_member(&a.fp()), "both devices list each other");
    remove(&a, &me, &seed, &b.fp());
    assert_eq!(a.member(&b.fp()).unwrap()["status"], "revoked");
    assert_eq!(raised.load(Ordering::SeqCst), 4, "founding, a window's yes that opens nothing, the window and the removal");
}

#[test]
fn a_computer_whose_delegate_has_lapsed_still_opens_a_window_with_the_persons_yes() {
    let (a, b) = (device(), device());
    let (raised, _) = at_the_prompt(&a);
    at_the_prompt(&b);
    let me = person(&a, "the-person");
    let seed = found(&a, &me, "home");
    pass(&a, 31 * 24 * 3_600_000);
    assert!(a.status()["governance"]["delegates"].as_array().unwrap().is_empty(), "the 30 days are over");
    let other = person(&a, "another-terminal");
    let drawn = a.ok("op_draft", "", &other, json!({"op": "add_device", "args": {}}));
    let ticket = yes_for(&a, &other, "card", drawn["card"].as_str().unwrap())["secret"].as_str().unwrap().to_string();
    assert_eq!(a.call("add_device", "", &other, json!({"ticket": ticket}))["ok"], false, "with no delegate and no signed act the ticket opens nothing");
    let code = open_window(&a, &me, &seed, json!({}));
    join(&b, &person(&b, "the-guest-person"), &a, &code);
    assert!(a.is_member(&b.fp()));
    assert_eq!(raised.load(Ordering::SeqCst), 3, "founding, the refused window's yes and the window");
}

/// A fresh ticket for a window on ``dev`` as ``me``, with the window act the card drafted and the key that would sign it.
fn window_card(dev: &Dev, me: &str) -> (String, Value, Vec<u8>) {
    let drawn = dev.ok("op_draft", "", me, json!({"op": "add_device", "args": {}}));
    let ticket = yes_for(dev, me, "card", drawn["card"].as_str().unwrap())["secret"].as_str().unwrap().to_string();
    let bytes = unhex(drawn["window"]["sign_bytes"].as_str().unwrap()).unwrap();
    dev.ok("ticket_spend", "", me, json!({"ticket": ticket, "payload": sha256_hex(&bytes)}));
    (ticket, drawn["window"]["act"].clone(), bytes)
}

fn signed_by(seed: &str, act: &mut Value, bytes: &[u8]) {
    let key = hex(SigningKey::from_bytes(&unhex(seed).unwrap().try_into().unwrap()).verifying_key().as_bytes());
    act["signatures"] = json!([{"kind": "admin", "key": key, "sig": sign(seed, bytes)}]);
}

#[test]
fn a_window_act_must_name_this_device_be_signed_by_a_key_and_belong_to_the_yes_it_came_with() {
    let (a, b) = (device(), device());
    at_a_terminal(&a);
    at_a_terminal(&b);
    let me = person(&a, "the-person");
    let seed = found(&a, &me, "home");
    // an act that names another device opens no window here
    let (p2, p3, p4) = (person(&a, "p2"), person(&a, "p3"), person(&a, "p4"));
    let (ticket, mut act, _) = window_card(&a, &p2);
    act["body"]["fingerprint"] = json!(b.fp());
    let bytes = serde_json::from_value::<poolhouse_node::gov::act::Act>(act.clone()).unwrap().sign_bytes();
    signed_by(&seed, &mut act, &bytes);
    let wrong = a.call("add_device", "", &p2, json!({"ticket": ticket, "window": act}));
    assert_eq!(wrong["ok"], false, "{wrong}");
    // an act no key signed opens none either
    let (ticket, act, _) = window_card(&a, &p3);
    let unsigned = a.call("add_device", "", &p3, json!({"ticket": ticket, "window": act}));
    assert_eq!(unsigned["ok"], false, "{unsigned}");
    assert!(a.status()["join_code_open"] != json!(true), "no window opened");
    // a key that signs the act of one yes cannot sign a second act under it: the ticket names the bytes of the card
    let (ticket, mut act, bytes) = window_card(&a, &p4);
    signed_by(&seed, &mut act, &bytes);
    let opened = a.ok("add_device", "", &p4, json!({"ticket": ticket, "window": act}));
    assert!(opened["code"].is_string(), "{opened}");
}

#[test]
fn a_yes_for_this_devices_window_opens_no_window_for_another_member() {
    let (a, b) = (device(), device());
    at_a_terminal(&a);
    at_a_terminal(&b);
    let me = person(&a, "the-person");
    let seed = found(&a, &me, "home");
    let code = open_window(&a, &me, &seed, json!({}));
    join(&b, &person(&b, "the-guest-person"), &a, &code);
    assert!(a.is_member(&b.fp()), "the second device is a member");
    // the person's yes was for a window of this device: the key's signature of a window of the member opens it for nobody
    let later = person(&a, "later");
    let (ticket, mut act, _) = window_card(&a, &later);
    act["body"]["fingerprint"] = json!(b.fp());
    let bytes = serde_json::from_value::<poolhouse_node::gov::act::Act>(act.clone()).unwrap().sign_bytes();
    signed_by(&seed, &mut act, &bytes);
    let refused = a.call("add_device", "", &later, json!({"ticket": ticket, "window": act}));
    assert_eq!(refused["ok"], false, "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains("names this device"), "{refused}");
}

#[test]
fn the_yes_for_one_window_or_join_covers_no_other_pool_host_port_code_or_device() {
    use poolhouse_node::window::describe;
    let same = |a: &Value, b: &Value| describe("join_pool", "", "", a.as_object().unwrap()).unwrap().bytes == describe("join_pool", "", "", b.as_object().unwrap()).unwrap().bytes;
    let base = json!({"host": "10.0.0.5", "port": 7447, "passphrase": "123456", "fingerprint": ""});
    assert!(same(&base, &base.clone()));
    for (field, other) in [("host", json!("10.0.0.6")), ("port", json!(7448)), ("passphrase", json!("654321")), ("fingerprint", json!("ab".repeat(32)))] {
        let mut changed = base.clone();
        changed[field] = other;
        assert!(!same(&base, &changed), "a yes for one {field} is no yes for another");
    }
    let window = |pool: &str, ttl: u64, code: &str, presigned: bool| describe("add_device", pool, "home", json!({"ttl_s": ttl, "passphrase": code, "presigned": presigned}).as_object().unwrap()).unwrap().bytes;
    let base = window("pool-a", 120, "", false);
    assert_ne!(base, window("pool-b", 120, "", false), "a yes for a window of one pool opens none of another");
    assert_ne!(base, window("pool-a", 60, "", false), "nor one of another length");
    assert_ne!(base, window("pool-a", 120, "my-own-code", false), "nor with a code of the asker's choosing");
    assert_ne!(base, window("pool-a", 120, "", true), "nor a presigned one");
    assert_ne!(describe("join_pool", "", "", base_join().as_object().unwrap()).unwrap().sentence, describe("join_pool", "", "", {
        let mut other = base_join();
        other["host"] = json!("10.0.0.6");
        other
    }.as_object().unwrap()).unwrap().sentence, "the prompt names the host");
}

fn base_join() -> Value {
    json!({"host": "10.0.0.5", "port": 7447, "passphrase": "123456", "fingerprint": ""})
}
