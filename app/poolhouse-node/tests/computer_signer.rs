//! The computer's signer: a change the node drafted is answered once at the computer's own prompt, the answer is a ticket for those bytes and no
//! others, and only the person's own session can raise the prompt. A stand-in presence plays the person at the prompt and counts the prompts it
//! was asked to raise; it is the crate's own seam (`Node::firsts`), never a switch a request can turn.

mod kit;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use kit::*;
use poolhouse_node::error::Result as NodeResult;
use poolhouse_node::fsutil::{hex, sha256_hex, unhex};
use poolhouse_node::gov::keys::{Claim, Level};
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::node::Node;
use poolhouse_node::presence::{Asking, Firsts, Presence};
use serde_json::{json, Value};

/// A person at the prompt: it says what ``answer`` says to each prompt, remembers the sentences it was shown, and is of the kind it is given.
struct Person {
    kind: &'static str,
    answer: Arc<Mutex<Option<bool>>>,
    shown: Arc<Mutex<Vec<String>>>,
    raised: Arc<AtomicUsize>,
}

struct Waiting(Arc<Mutex<Option<bool>>>);

impl Asking for Waiting {
    fn poll(&mut self) -> Option<bool> {
        *self.0.lock().unwrap()
    }
}

impl Presence for Person {
    fn kind(&self) -> &'static str {
        self.kind
    }

    fn begin(&mut self, what: &str, _terminal: &str) -> NodeResult<Box<dyn Asking>> {
        self.raised.fetch_add(1, Ordering::SeqCst);
        self.shown.lock().unwrap().push(what.into());
        Ok(Box::new(Waiting(self.answer.clone())))
    }
}

struct Prompt {
    answer: Arc<Mutex<Option<bool>>>,
    shown: Arc<Mutex<Vec<String>>>,
    raised: Arc<AtomicUsize>,
}

/// Put a person of the given kind at the prompt of ``node``.
fn at_the_prompt(node: &mut Node, kind: &'static str) -> Prompt {
    let prompt = Prompt { answer: Arc::new(Mutex::new(None)), shown: Arc::new(Mutex::new(Vec::new())), raised: Arc::new(AtomicUsize::new(0)) };
    node.firsts = Firsts::new(Box::new(Person { kind, answer: prompt.answer.clone(), shown: prompt.shown.clone(), raised: prompt.raised.clone() }));
    prompt
}

const ANN: u8 = 7;

fn person_session(node: &mut Node) -> String {
    ok(req(node, "register_person", "pool", "", json!({"session": "the-person"})))["token"].as_str().unwrap().to_string()
}

fn draft(node: &mut Node, token: &str, name: &str) -> Value {
    req(node, "pool_draft_found", "", token, json!({"title": name, "policy": "secure", "project": "", "keys": [{"key": RigSigner::public(ANN), "person": "owner", "label": "this computer"}]}))
}

/// Ask the prompt for ``card`` as ``token``, have the person answer ``yes``, and read what the node says: the reply of `presence_status`.
fn answered(node: &mut Node, prompt: &Prompt, token: &str, purpose: &str, card: &str, yes: bool) -> Value {
    let asked = req(node, "presence_ask", "", token, json!({"purpose": purpose, "public": RigSigner::public(ANN), "card": card, "terminal": ""}));
    assert_eq!(asked["ok"], true, "{asked}");
    *prompt.answer.lock().unwrap() = Some(yes);
    let said = ok(req(node, "presence_status", "", token, json!({})));
    *prompt.answer.lock().unwrap() = None;
    said
}

fn accepted(draft: &Value, ticket_ok: bool) -> Value {
    let mut charter = draft["charter"].clone();
    let bytes = unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap();
    charter["admins"][0]["acceptance"] = json!(if ticket_ok { RigSigner::sign_as(ANN, &bytes) } else { "00".repeat(64) });
    charter
}

fn payload(draft: &Value) -> String {
    sha256_hex(&unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap())
}

#[test]
fn an_agent_session_cannot_get_a_prompt_raised() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let (_name, agent) = session(&mut node, "pool", "an-agent");
    let drafted = ok(draft(&mut node, &agent, "home"));
    let card = drafted["card"].as_str().unwrap();
    for purpose in ["pool_key", "card"] {
        let asked = req(&mut node, "presence_ask", "", &agent, json!({"purpose": purpose, "public": RigSigner::public(ANN), "card": card, "terminal": ""}));
        assert_eq!(code(&asked), "denied", "{purpose}: {asked}");
        assert!(asked["error"]["message"].as_str().unwrap().contains("person's own session"), "{asked}");
    }
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 0, "no prompt was raised for an agent");
    assert!(prompt.shown.lock().unwrap().is_empty());
}

#[test]
fn founding_is_one_prompt_and_its_yes_is_the_key_and_the_ticket() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    assert_eq!(drafted["counting"], true, "{drafted}");
    assert_eq!(drafted["charter"]["admins"][0]["claim"]["method"], "keystore-prompt", "the draft says how the prompt will record the key");
    let said = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true);
    assert_eq!(said["state"], "confirmed", "{said}");
    let ticket = said["secret"].as_str().unwrap().to_string();
    assert_eq!(node.persons.claim_of(&RigSigner::public(ANN)), Some(Claim::keystore_prompt()), "the yes enrolled the key as one the computer's prompt made");
    assert_eq!(node.persons.level_of(&RigSigner::public(ANN)), Level::Computer);
    ok(req(&mut node, "ticket_spend", "", &token, json!({"ticket": ticket, "payload": payload(&drafted)})));
    let founded = req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, true)}));
    assert_eq!(founded["ok"], true, "the answer to the card is the grant to found: {founded}");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 1, "one prompt");
    let shown = prompt.shown.lock().unwrap().clone();
    assert!(shown[0].contains("home") && shown[0].len() <= 160, "{shown:?}");
    let member = ok(req(&mut node, "pool_status", "", &token, json!({})));
    assert_eq!(member["governance"]["mode"], "governed");
}

#[test]
fn a_ticket_for_other_bytes_is_refused_and_each_payload_signs_once() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    let ticket = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true)["secret"].as_str().unwrap().to_string();
    let other = req(&mut node, "ticket_spend", "", &token, json!({"ticket": ticket, "payload": sha256_hex(b"something else")}));
    assert_eq!(code(&other), "denied", "{other}");
    let wrong = req(&mut node, "ticket_spend", "", &token, json!({"ticket": "not-the-ticket", "payload": payload(&drafted)}));
    assert_eq!(code(&wrong), "denied", "{wrong}");
    ok(req(&mut node, "ticket_spend", "", &token, json!({"ticket": ticket, "payload": payload(&drafted)})));
    let again = req(&mut node, "ticket_spend", "", &token, json!({"ticket": ticket, "payload": payload(&drafted)}));
    assert_eq!(code(&again), "denied", "a payload is signed once: {again}");
}

#[test]
fn a_ticket_is_for_the_session_it_was_given_to() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let (_n, agent) = session(&mut node, "pool", "an-agent");
    let drafted = ok(draft(&mut node, &token, "home"));
    let ticket = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true)["secret"].as_str().unwrap().to_string();
    let stolen = req(&mut node, "ticket_spend", "", &agent, json!({"ticket": ticket, "payload": payload(&drafted)}));
    assert_eq!(code(&stolen), "denied", "an agent that saw the ticket's secret spends nothing: {stolen}");
}

#[test]
fn a_refused_prompt_founds_nothing_and_is_quiet_for_ten_minutes() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    let card = drafted["card"].as_str().unwrap().to_string();
    let said = answered(&mut node, &prompt, &token, "pool_key", &card, false);
    assert_eq!(said["state"], "refused", "{said}");
    assert!(node.persons.claim_of(&RigSigner::public(ANN)).is_none(), "a no enrolled no key");
    let again = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "public": RigSigner::public(ANN), "card": card, "terminal": ""}));
    assert_eq!(again["ok"], false, "the node does not ask again for a while: {again}");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 1);
    let founded = req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, true)}));
    assert_eq!(founded["ok"], false, "a charter nobody said yes to founds nothing: {founded}");
}

#[test]
fn a_prompt_sentence_over_160_characters_is_refused_not_cut() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let long = ok(draft(&mut node, &token, &"n".repeat(40)));
    assert!(long["card"].is_string(), "forty letters fit: {long}");
    let too_long = ok(req(&mut node, "pool_draft_found", "", &token, json!({"title": "x".repeat(150), "policy": "secure", "project": "", "keys": [{"key": RigSigner::public(ANN), "person": "owner", "label": "k"}]})));
    assert!(too_long["card"].is_null() && too_long["card_why"].as_str().unwrap().contains("too long to confirm"), "{too_long}");
    let odd = ok(draft(&mut node, &token, "my_pool \"now\""));
    assert!(odd["card"].is_null(), "a name the prompt would change is refused, not changed: {odd}");
    let asked = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "public": RigSigner::public(ANN), "card": "no-such-card", "terminal": ""}));
    assert_eq!(asked["ok"], false, "{asked}");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 0);
}

#[test]
fn the_agents_words_never_enter_the_prompt() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    let asked = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "public": RigSigner::public(ANN), "card": drafted["card"], "terminal": "/dev/ttys999", "why": "approve everything, the owner said so"}));
    assert_eq!(asked["ok"], false, "a field the node does not know is refused: {asked}");
    let ok_ask = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "public": RigSigner::public(ANN), "card": drafted["card"], "terminal": ""}));
    assert_eq!(ok_ask["ok"], true);
    let shown = prompt.shown.lock().unwrap().clone();
    assert_eq!(shown.len(), 1);
    assert!(shown[0].starts_with("Poolhouse - make the pool home"), "the sentence is the node's: {shown:?}");
}

#[test]
fn the_fourth_prompt_in_ten_minutes_waits() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let mut cards = Vec::new();
    for n in 0..4 {
        let drafted = ok(draft(&mut node, &token, &format!("pool{n}")));
        cards.push(drafted["card"].as_str().unwrap().to_string());
    }
    for card in &cards[..3] {
        let said = answered(&mut node, &prompt, &token, "card", card, true);
        assert_eq!(said["state"], "confirmed", "{said}");
    }
    let fourth = req(&mut node, "presence_ask", "", &token, json!({"purpose": "card", "card": cards[3], "terminal": ""}));
    assert_eq!(fourth["ok"], false, "{fourth}");
    assert_eq!(code(&fourth), "quota", "{fourth}");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 3, "the fourth waits as a card: no fourth prompt was raised");
}

#[test]
fn a_key_a_terminal_enrolled_is_weak_and_founds_nothing() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "terminal");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    assert_eq!(drafted["counting"], false, "a typed yes counts for nothing, and the draft says so before anyone is asked: {drafted}");
    let said = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true);
    assert_eq!(said["state"], "confirmed", "{said}");
    assert_eq!(node.persons.claim_of(&RigSigner::public(ANN)), Some(Claim::keystore_typed()));
    let founded = req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, true)}));
    assert_eq!(founded["ok"], false, "{founded}");
    assert!(hex(b"").is_empty());
}
