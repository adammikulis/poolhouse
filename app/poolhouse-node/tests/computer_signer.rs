//! The computer's signer: a change the node drafted is answered once at the computer's own prompt, the answer is a ticket for those bytes and no
//! others, and only the person's own session (proven by the secret the node keeps for the person) can draft one or raise the prompt. A
//! stand-in presence plays the person at the prompt and counts the prompts it was asked to raise; it is the crate's own seam
//! (`Node::firsts`), never a switch a request can turn.

mod kit;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use ed25519_dalek::{Signer as _, SigningKey};
use kit::*;
use poolhouse_node::error::Result as NodeResult;
use poolhouse_node::fsutil::{hex, sha256_hex, unhex};
use poolhouse_node::gov::keys::{Claim, Level};
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

fn person_session(node: &mut Node) -> String {
    person_token(node, "pool", "the-person")
}

/// The draft of a pool whose one key the node makes for this computer.
fn draft(node: &mut Node, token: &str, name: &str) -> Value {
    req(node, "pool_draft_found", "", token, json!({"title": name, "policy": "secure", "project": "", "keys": [{"computer": true, "person": "owner", "label": "this computer"}]}))
}

/// Ask the prompt for ``card`` as ``token``, have the person answer ``yes``, and read what the node says: the reply of `presence_status`.
fn answered(node: &mut Node, prompt: &Prompt, token: &str, purpose: &str, card: &str, yes: bool) -> Value {
    let asked = req(node, "presence_ask", "", token, json!({"purpose": purpose, "card": card, "terminal": ""}));
    assert_eq!(asked["ok"], true, "{asked}");
    *prompt.answer.lock().unwrap() = Some(yes);
    let said = ok(req(node, "presence_status", "", token, json!({})));
    *prompt.answer.lock().unwrap() = None;
    said
}

/// The charter of ``draft`` with its one key's acceptance signed by the key whose seed the node gave the person.
fn accepted(draft: &Value, seed: &str) -> Value {
    let mut charter = draft["charter"].clone();
    let bytes = unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap();
    let key = SigningKey::from_bytes(&unhex(seed).unwrap().try_into().unwrap());
    charter["admins"][0]["acceptance"] = json!(hex(&key.sign(&bytes).to_bytes()));
    charter
}

fn payload(draft: &Value) -> String {
    sha256_hex(&unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap())
}

fn public_of(draft: &Value) -> String {
    draft["charter"]["admins"][0]["key"].as_str().unwrap().to_string()
}

#[test]
fn an_agent_session_cannot_draft_a_key_or_a_card_or_get_a_prompt_raised() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let (_name, agent) = session(&mut node, "pool", "an-agent");
    let refused = draft(&mut node, &agent, "home");
    assert_eq!(code(&refused), "denied", "an agent is given no key made for this computer: {refused}");
    let held = ok(req(&mut node, "pool_draft_found", "", &agent, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": hex(&kit::key(9).verifying_key().to_bytes()), "person": "owner", "label": "a phone"}]})));
    assert!(held["card"].is_null() && held["card_why"].as_str().unwrap().contains("person's own session"), "an agent's draft is never a card: {held}");
    let person = person_session(&mut node);
    let mine = ok(draft(&mut node, &person, "home"));
    let card = mine["card"].as_str().unwrap();
    for purpose in ["pool_key", "card"] {
        let asked = req(&mut node, "presence_ask", "", &agent, json!({"purpose": purpose, "card": card, "terminal": ""}));
        assert_eq!(code(&asked), "denied", "{purpose}: {asked}");
        assert!(asked["error"]["message"].as_str().unwrap().contains("person's own session"), "{asked}");
    }
    let drawn = req(&mut node, "op_draft", "", &agent, json!({"op": "add_device", "args": {}}));
    assert_eq!(code(&drawn), "denied", "{drawn}");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 0, "no prompt was raised for an agent");
    assert!(prompt.shown.lock().unwrap().is_empty());
}

#[test]
fn a_session_that_does_not_hold_the_persons_secret_is_not_the_persons_session() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    for params in [json!({"session": "the-person"}), json!({"session": "the-person", "proof": ""}), json!({"session": "the-person", "proof": "00".repeat(32)}), json!({"session": "the-person", "proof": "a".repeat(63)})] {
        let made = req(&mut node, "register_person", "pool", "", params.clone());
        assert_eq!(code(&made), "denied", "{params}: {made}");
    }
    let secret = proof(&node);
    assert_eq!(secret.len(), 64, "the node keeps 32 random bytes for the person");
    assert!(poolhouse_node::identity::person_secret_path(&node.dir).starts_with(node.dir.join("person")), "in the folder the agent sandbox may neither read nor write");
    let (_name, agent) = session(&mut node, "pool", "an-agent");
    let proxied = req(&mut node, "register_person", "pool", &agent, json!({"session": "the-person", "proof": "no"}));
    assert_eq!(code(&proxied), "denied");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 0);
    let real = person_session(&mut node);
    assert!(ok(draft(&mut node, &real, "home"))["card"].is_string(), "the secret makes the session the person's");
}

#[test]
fn founding_is_one_prompt_and_its_yes_is_the_key_the_node_made_and_the_ticket() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    assert_eq!(drafted["counting"], true, "{drafted}");
    assert_eq!(drafted["typed"], false);
    assert_eq!(drafted["charter"]["admins"][0]["claim"]["method"], "keystore-prompt", "the draft says how the prompt will record the key");
    assert_eq!(drafted["charter"]["first"]["delegate"], true, "the computer may admit devices for 30 days");
    assert!(node.persons.claim_of(&public_of(&drafted)).is_none(), "nothing is enrolled before the yes");
    let said = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true);
    assert!(node.persons.claim_of(&public_of(&drafted)).is_none(), "and the yes enrols nothing: the key is entered when the pool it founds is");
    assert_eq!(said["state"], "confirmed", "{said}");
    let (ticket, seed) = (said["secret"].as_str().unwrap().to_string(), said["seed"].as_str().unwrap().to_string());
    assert_eq!(said["public"], public_of(&drafted).as_str());
    assert_eq!(hex(SigningKey::from_bytes(&unhex(&seed).unwrap().try_into().unwrap()).verifying_key().as_bytes()), public_of(&drafted), "the seed is the one of the key the draft listed");
    let again = ok(req(&mut node, "presence_status", "", &token, json!({})));
    assert!(again["seed"].is_null() && again["secret"].is_null(), "the seed and the ticket are handed once: {again}");
    ok(req(&mut node, "ticket_spend", "", &token, json!({"ticket": ticket, "payload": payload(&drafted)})));
    let founded = req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, &seed)}));
    assert_eq!(founded["ok"], true, "the answer to the card is the grant to found: {founded}");
    assert_eq!(node.persons.claim_of(&public_of(&drafted)), Some(Claim::keystore_prompt()), "the founding enrolled the key as one the computer's prompt made");
    assert_eq!(node.persons.level_of(&public_of(&drafted)), Level::Computer);
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 1, "one prompt");
    let shown = prompt.shown.lock().unwrap().clone();
    assert!(shown[0].contains("pool home") && shown[0].len() <= 240, "{shown:?}");
    assert!(shown[0].contains("adds a key") && shown[0].contains("30 days"), "the sentence says that the yes enrols a key and what it allows: {shown:?}");
    let member = ok(req(&mut node, "pool_status", "", &token, json!({})));
    assert_eq!(member["governance"]["mode"], "governed");
}

#[test]
fn a_yes_never_enrols_a_key_an_agent_holds_and_a_card_asks_for_no_key_of_its_own() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let held_by_the_agent = hex(&kit::key(9).verifying_key().to_bytes());
    let drafted = ok(req(&mut node, "pool_draft_found", "", &token, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": held_by_the_agent, "person": "owner", "label": "a key"}]})));
    let card = drafted["card"].as_str().unwrap();
    let asked = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "card": card, "terminal": ""}));
    assert_eq!(code(&asked), "invalid", "a card that made no key enrols none: {asked}");
    let named = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "public": held_by_the_agent, "card": card, "terminal": ""}));
    assert_eq!(code(&named), "invalid", "a request never names the key a yes enrols: {named}");
    let made = ok(draft(&mut node, &token, "home"));
    let as_card = req(&mut node, "presence_ask", "", &token, json!({"purpose": "card", "card": made["card"], "terminal": ""}));
    assert_eq!(code(&as_card), "invalid", "a card that made a key is asked for as one: {as_card}");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 0, "no prompt for any of them");
    assert!(node.persons.claim_of(&held_by_the_agent).is_none());
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
fn a_charter_nobody_answered_for_founds_nothing_even_when_it_is_signed_and_the_key_is_enrolled() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let person = person_session(&mut node);
    let other = person_token(&mut node, "pool", "another-person");
    let drafted = ok(draft(&mut node, &person, "home"));
    let said = answered(&mut node, &prompt, &person, "pool_key", drafted["card"].as_str().unwrap(), true);
    let seed = said["seed"].as_str().unwrap();
    let stranger = req(&mut node, "pool_found", "", &other, json!({"charter": accepted(&drafted, seed)}));
    assert_eq!(code(&stranger), "denied", "the yes was given to another session, so this one holds no grant to found: {stranger}");
    assert_eq!(ok(req(&mut node, "pool_status", "", &person, json!({})))["governance"]["mode"], "unfounded");
    let mine = req(&mut node, "pool_found", "", &person, json!({"charter": accepted(&drafted, seed)}));
    assert_eq!(mine["ok"], true, "{mine}");
    let again = req(&mut node, "pool_found", "", &person, json!({"charter": accepted(&drafted, seed)}));
    assert_eq!(again["ok"], false, "the yes founds once");
}

#[test]
fn a_yes_for_one_charter_founds_no_other_charter_of_the_same_session_and_key() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let person = person_session(&mut node);
    let drafted = ok(draft(&mut node, &person, "home"));
    let said = answered(&mut node, &prompt, &person, "pool_key", drafted["card"].as_str().unwrap(), true);
    let seed = said["seed"].as_str().unwrap();
    // The same key, enrolled by that very yes, signs the acceptance of a charter with another name: it is another pool, which nobody said yes to.
    let mut other = poolhouse_node::gov::charter::Charter::read(&drafted["charter"]).unwrap();
    other.name = "another".into();
    let anchor = other.anchor();
    let key = SigningKey::from_bytes(&unhex(seed).unwrap().try_into().unwrap());
    other.admins[0].acceptance = hex(&key.sign(&other.admins[0].acceptance_message(&anchor, "")).to_bytes());
    let refused = req(&mut node, "pool_found", "", &person, json!({"charter": other.body()}));
    assert_eq!(code(&refused), "denied", "a yes is for the charter it was given for and no other: {refused}");
    assert_eq!(ok(req(&mut node, "pool_status", "", &person, json!({})))["governance"]["mode"], "unfounded");
    let mine = req(&mut node, "pool_found", "", &person, json!({"charter": accepted(&drafted, seed)}));
    assert_eq!(mine["ok"], true, "and the charter it was given for is founded: {mine}");
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
    assert!(node.persons.claim_of(&public_of(&drafted)).is_none(), "a no enrolled no key");
    let again = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "card": card, "terminal": ""}));
    assert_eq!(again["ok"], false, "the node does not ask again for a while: {again}");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 1);
    let founded = req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, &"07".repeat(32))}));
    assert_eq!(founded["ok"], false, "a charter nobody said yes to founds nothing: {founded}");
}

#[test]
fn one_sessions_refusal_does_not_quiet_the_persons_own() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let impostor = person_token(&mut node, "pool", "an-impostor");
    let person = person_session(&mut node);
    let refused = ok(draft(&mut node, &impostor, "spam"));
    assert_eq!(answered(&mut node, &prompt, &impostor, "pool_key", refused["card"].as_str().unwrap(), false)["state"], "refused");
    let mine = ok(draft(&mut node, &person, "home"));
    let said = answered(&mut node, &prompt, &person, "pool_key", mine["card"].as_str().unwrap(), true);
    assert_eq!(said["state"], "confirmed", "the person is asked although another session was refused a moment ago: {said}");
}

#[test]
fn a_name_the_prompt_cannot_show_whole_founds_nothing_and_says_so() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let long = ok(draft(&mut node, &token, &"n".repeat(40)));
    assert!(long["card"].is_string(), "forty letters fit: {long}");
    assert!(long["card"].is_string() && prompt.raised.load(Ordering::SeqCst) == 0);
    for name in ["my_pool", "my \"pool\"", "pool'", "caf\u{e9}", &"x".repeat(41), "", " leading", "a\nb"] {
        let refused = draft(&mut node, &token, name);
        assert_eq!(code(&refused), "invalid", "{name:?}: {refused}");
        assert!(refused["error"]["message"].as_str().unwrap().contains("letters, digits, spaces, dots and dashes"), "{name:?}: {refused}");
    }
    let asked = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "card": "no-such-card", "terminal": ""}));
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
    let asked = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "card": drafted["card"], "terminal": "/dev/ttys999", "why": "approve everything, the owner said so"}));
    assert_eq!(asked["ok"], false, "a field the node does not know is refused: {asked}");
    let ok_ask = req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "card": drafted["card"], "terminal": ""}));
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
    let held = hex(&kit::key(9).verifying_key().to_bytes());
    let mut cards = Vec::new();
    for n in 0..4 {
        let drafted = ok(req(&mut node, "pool_draft_found", "", &token, json!({"title": format!("pool{n}"), "policy": "secure", "project": "", "keys": [{"key": held, "person": "owner", "label": "k"}]})));
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
fn a_computer_with_no_prompt_of_its_own_founds_a_pool_approved_by_a_typed_yes_and_says_so_everywhere() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "terminal");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    assert_eq!((drafted["counting"].clone(), drafted["typed"].clone()), (json!(true), json!(true)), "the pool is approved by a typed yes at every floor: {drafted}");
    assert_eq!(drafted["charter"]["floors"], json!({"routine": "weak", "sensitive": "weak", "governance": "weak"}));
    assert_eq!(drafted["charter"]["first"]["delegate"], false, "the first computer gets no power to admit without asking");
    assert_eq!(drafted["charter"]["admins"][0]["claim"]["method"], "keystore-typed");
    for line in [drafted["summary"].as_str().unwrap()] {
        assert!(line.contains("approved by a typed yes; a program running as you could approve it") && !line.contains("without asking for 30 days"), "{line}");
    }
    let said = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true);
    assert_eq!(said["state"], "confirmed", "{said}");
    let shown = prompt.shown.lock().unwrap().clone();
    assert!(shown[0].contains("typed yes") && shown[0].contains("program running as you"), "{shown:?}");
    let founded = req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, said["seed"].as_str().unwrap())}));
    assert_eq!(founded["ok"], true, "no phone and no prompt of its own does not block a pool: {founded}");
    assert_eq!(node.persons.claim_of(&public_of(&drafted)), Some(Claim::keystore_typed()));
    let gov = ok(req(&mut node, "pool_status", "", &token, json!({})))["governance"].clone();
    assert_eq!((gov["approved"].clone(), gov["delegates"].clone()), (json!("approved by a typed yes; a program running as you could approve it"), json!([])));
    let card = ok(req(&mut node, "pool_draft", "", &token, json!({"kind": "set_holds", "body": {"admin_add_h": 48}})));
    assert!(card["summary"].as_str().unwrap().contains("LEVEL: This change is approved by a typed yes; a program running as you could approve it."), "{card}");
    assert!(card["summary"].as_str().unwrap().contains("It waits 7 days"), "every hold stays: {card}");
}

#[test]
fn a_computer_that_can_prompt_but_cannot_keep_its_key_in_a_keystore_founds_a_typed_yes_pool_with_the_one_yes() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let drafted = ok(req(&mut node, "pool_draft_found", "", &token, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"computer": true, "sealed": false, "person": "owner", "label": "this computer"}]})));
    assert_eq!((drafted["counting"].clone(), drafted["typed"].clone()), (json!(true), json!(true)), "{drafted}");
    assert_eq!(drafted["charter"]["admins"][0]["claim"]["method"], "file", "a key kept in a file is claimed as one");
    assert_eq!(drafted["charter"]["first"]["delegate"], false);
    let said = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true);
    assert_eq!(said["state"], "confirmed", "{said}");
    assert!(prompt.shown.lock().unwrap()[0].contains("typed yes"), "the prompt says how the pool is approved: {:?}", prompt.shown.lock().unwrap());
    ok(req(&mut node, "ticket_spend", "", &token, json!({"ticket": said["secret"], "payload": payload(&drafted)})));
    let founded = req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, said["seed"].as_str().unwrap())}));
    assert_eq!(founded["ok"], true, "the prompt of this computer is enough for the pool, and the key is as weak as a file: {founded}");
    assert_eq!(node.persons.claim_of(&public_of(&drafted)), Some(Claim::file()), "the key is recorded as the charter says it is held");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 1, "one prompt");
}

#[test]
fn a_founding_that_fails_leaves_no_key_behind_and_the_yes_for_the_charter_stays() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    let said = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true);
    let seed = said["seed"].as_str().unwrap();
    let mut spoiled = drafted["charter"].clone();
    spoiled["admins"][0]["acceptance"] = json!("00".repeat(64));
    let refused = req(&mut node, "pool_found", "", &token, json!({"charter": spoiled}));
    assert_eq!(refused["ok"], false, "{refused}");
    assert!(node.persons.claim_of(&public_of(&drafted)).is_none(), "a pool that was not founded left no key behind");
    let again = req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, seed)}));
    assert_eq!(again["ok"], true, "the person's yes was not spent by the failure: {again}");
    assert_eq!(prompt.raised.load(Ordering::SeqCst), 1);
}

#[test]
fn a_window_or_a_join_is_drafted_only_where_the_network_runs() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let token = person_session(&mut node);
    for (op, args) in [("add_device", json!({})), ("join_pool", json!({"host": "127.0.0.1", "port": 1, "passphrase": "123456"}))] {
        let drawn = req(&mut node, "op_draft", "", &token, json!({"op": op, "args": args}));
        assert_eq!(code(&drawn), "invalid", "{op}: {drawn}");
        assert!(drawn["error"]["message"].as_str().unwrap().contains("network is not running"), "{drawn}");
    }
}

#[test]
fn the_sentence_of_a_founding_shows_every_key_the_charter_lists() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let other = hex(&kit::key(9).verifying_key().to_bytes());
    let drafted = ok(req(&mut node, "pool_draft_found", "", &token, json!({"title": "home", "policy": "secure", "project": "", "keys": [
        {"computer": true, "person": "owner", "label": "this computer"}, {"key": other, "person": "second", "label": "another"}]})));
    let card = drafted["card"].as_str().unwrap();
    ok(req(&mut node, "presence_ask", "", &token, json!({"purpose": "pool_key", "card": card, "terminal": ""})));
    let shown = prompt.shown.lock().unwrap()[0].clone();
    for key in drafted["charter"]["admins"].as_array().unwrap() {
        let words = poolhouse_node::phone::words(&poolhouse_node::gov::keys::fingerprint_of(key["key"].as_str().unwrap()));
        assert!(shown.contains(&words), "the yes is for every key the charter lists, and {words} is one of them: {shown}");
    }
    assert!(shown.contains("Keys "), "{shown}");
}

#[test]
fn the_prompt_for_a_change_of_admins_names_the_key_the_yes_adds_or_removes() {
    let dir = tempfile::tempdir().unwrap();
    let mut node = node(dir.path());
    let prompt = at_the_prompt(&mut node, "os");
    let token = person_session(&mut node);
    let drafted = ok(draft(&mut node, &token, "home"));
    let said = answered(&mut node, &prompt, &token, "pool_key", drafted["card"].as_str().unwrap(), true);
    ok(req(&mut node, "ticket_spend", "", &token, json!({"ticket": said["secret"], "payload": payload(&drafted)})));
    ok(req(&mut node, "pool_found", "", &token, json!({"charter": accepted(&drafted, said["seed"].as_str().unwrap())})));
    let status = ok(req(&mut node, "pool_status", "", &token, json!({})));
    let (pool, charter) = (status["pool"].as_str().unwrap().to_string(), status["governance"]["charter_hash"].as_str().unwrap().to_string());
    let other = kit::gov::entry_on(kit::gov::computer_admin(9, "second"), 2, &pool, &charter);
    let words = poolhouse_node::phone::words(&other.fingerprint());
    for (kind, body) in [("admin_add", json!({"entry": other})), ("admin_remove", json!({"key": other.key}))] {
        let card = ok(req(&mut node, "pool_draft", "", &token, json!({"kind": kind, "body": body})));
        let shown_before = prompt.shown.lock().unwrap().len();
        answered(&mut node, &prompt, &token, "card", card["card"].as_str().unwrap(), true);
        let shown = prompt.shown.lock().unwrap()[shown_before].clone();
        assert!(shown.contains(&words), "the yes of {kind} is for that key, so the prompt names it: {shown}");
    }
}
