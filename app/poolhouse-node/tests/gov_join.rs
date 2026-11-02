//! What a device is given when it joins through the real exchange, who may open a window on a pool no phone governs, and that a member with any name a
//! device can choose can still be removed at the computer's prompt.

mod kit;

use std::sync::{Arc, Mutex};

use kit::gov::*;
use kit::govnode::*;
use kit::netkit::*;
use poolhouse_node::error::Result as NodeResult;
use poolhouse_node::gov::keys::Level;
use poolhouse_node::presence::card::{shows_whole, MOST_SENTENCE};
use poolhouse_node::presence::{Asking, Firsts, Presence};
use serde_json::{json, Value};

#[test]
fn a_device_that_joins_through_the_real_exchange_honors_a_delegate_whose_wait_ran_before_it_came() {
    let (a, b, _) = pool_of_three();
    a.ok("pool_propose", "", &a.token, json!({"kind": "delegate", "body": {"fingerprint": b.fp()}}));
    sync(&b);
    for d in [&a, &b] {
        pass(d, HELD);
    }
    b.node.lock().unwrap().rig_bind_device_key();
    let guest = device();
    b.join_with_code(&guest);
    assert!(b.is_member(&guest.fp()) && guest.is_member(&b.fp()));
    let delegates = guest.status()["governance"]["delegates"].clone();
    assert!(delegates.as_array().unwrap().iter().any(|d| d == b.fp().as_str()), "the joiner takes a delegate that was live before it came as live, not as one whose seven days start again: {delegates}");
}

#[test]
fn an_agent_holding_the_grant_still_opens_no_window_on_a_pool_no_phone_governs() {
    let a = device();
    found_at(&a, person(ANN, "ann"), "secure", Level::Computer);
    let (name, agent) = a.session("demo", "an-agent");
    grant(&a, "demo", &agent, &["add_device"]);
    let held = ok_listing(&a, &agent);
    assert!(held, "the agent does hold the grant to open a window");
    let window = a.call("add_device", "", &agent, json!({}));
    assert_eq!(window["ok"], false, "a window of a computer-only pool is the person's, whatever an agent holds: {window}");
    assert!(window["result"]["code"].is_null(), "and no code reached the agent: {window}");
    assert!(a.node.lock().unwrap().facts.join_code_until_ms == 0, "no window opened ({name})");
}

fn ok_listing(dev: &Dev, token: &str) -> bool {
    let listing = dev.ok("grants", "demo", token, json!({}));
    listing["grants"].as_array().unwrap().iter().any(|g| g["action"] == "add_device" && g["status"] == "live")
}

/// A person at the prompt who says yes and keeps what the prompt said.
struct Recording(Arc<Mutex<Vec<String>>>);

struct Said;

impl Asking for Said {
    fn poll(&mut self) -> Option<bool> {
        Some(true)
    }
}

impl Presence for Recording {
    fn kind(&self) -> &'static str {
        "os"
    }

    fn begin(&mut self, what: &str, _terminal: &str) -> NodeResult<Box<dyn Asking>> {
        self.0.lock().unwrap().push(what.into());
        Ok(Box::new(Said))
    }
}

#[test]
fn a_member_whose_name_the_prompt_could_not_show_can_still_be_removed_at_it() {
    let a = device();
    found_at(&a, person(ANN, "ann"), "secure", Level::Computer);
    let shown = Arc::new(Mutex::new(Vec::new()));
    a.node.lock().unwrap().firsts = Firsts::new(Box::new(Recording(shown.clone())));
    let proof = kit::proof_in(a.dir.path());
    let me = a.ok("register_person", "demo", "", json!({"session": "the-person", "proof": proof}))["token"].as_str().unwrap().to_string();
    let names = [
        format!("it's_caf\u{e9} \"quoted\" {}", "x".repeat(80)),
        "a;b `c` $(d) \n e".to_string(),
        "\u{202e}rtl \u{1F600} emoji ".to_string() + &"_".repeat(60),
    ];
    for name in &names {
        let b = device();
        let der = b.node.lock().unwrap().cert.der.clone();
        a.node.lock().unwrap().gov_admit(&der, name, "join-code").unwrap();
        let drafted: Value = a.ok("pool_draft", "", &me, json!({"kind": "remove", "body": {"fingerprint": b.fp()}}));
        let card = drafted["card"].as_str().unwrap_or_else(|| panic!("{name:?} left a member nobody can remove at the prompt: {drafted}"));
        a.ok("presence_ask", "", &me, json!({"purpose": "card", "card": card, "terminal": ""}));
        let sentence = shown.lock().unwrap().last().cloned().unwrap();
        assert!(sentence.chars().count() <= MOST_SENTENCE && shows_whole(&sentence), "{name:?}: {sentence}");
        assert!(sentence.starts_with("Poolhouse - remove the device ") && !sentence.contains(name.trim()), "the device is named by the words of its fingerprint, not by its own name: {sentence}");
    }
}
