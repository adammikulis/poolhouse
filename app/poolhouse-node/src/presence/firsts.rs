//! The prompt, what became of it, and what it made, for each asker.
//!
//! Every session that asks has a state of its own: the prompt it has open, its answer, its ticket, its founding yes and its limits
//! (`ledger`). One session's questions, refusals and cards never take the place of another's, so a program that registers a session and asks
//! over and over spends only its own allowance.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};

use super::card::{self, Generated};
use super::ledger::Ledger;
use super::os::ASK_FOR_MS;
use super::{same, Asking, Presence, Purpose};
use crate::error::{Error, Result};
use crate::fsutil::sha256_hex;
use crate::gov::keys::Claim;

/// How long what a yes makes (the one-time secret, a founding yes) is good for.
pub(crate) const SECRET_FOR_MS: u64 = 10 * 60_000;
/// How many prompts are open at once, over all askers: a dialog for every session would bury the person.
const MOST_OPEN: usize = 4;

/// A prompt that is open.
struct Pending {
    purpose: Purpose,
    /// The public key (hex) the first key will be, or the id of the card asked.
    bound: String,
    started_ms: u64,
    prompt: Box<dyn Asking>,
}

/// The ticket a yes for a card made: the card, the hash of its secret, the payloads not yet signed and the key it enrols.
struct Ticket {
    sha256: String,
    left: BTreeSet<String>,
    made_ms: u64,
    enrol: Option<Generated>,
}

/// A founding card that was answered yes, waiting for the charter it founds.
struct Founded {
    anchor: String,
    made_ms: u64,
}

enum Outcome {
    /// Confirmed: the secret, until the session that asked has read it.
    Yes(Option<String>, u64, Purpose),
    No,
    Late,
}

struct Secret {
    purpose: Purpose,
    bound: String,
    sha256: String,
    at_ms: u64,
}

/// What one asker has open and has been given.
#[derive(Default)]
struct Asker {
    asking: Option<Pending>,
    outcome: Option<Outcome>,
    ticket: Option<Ticket>,
    founded: Option<Founded>,
}

/// The prompts, what became of them, and the one secret the first key or phone needs.
pub struct Firsts {
    presence: Box<dyn Presence>,
    askers: BTreeMap<String, Asker>,
    secret: Option<Secret>,
    ledger: Ledger,
    /// The changes the node drafted that a person may answer.
    pub cards: card::Cards,
}

impl Firsts {
    /// A prompt whose limits are lost with the node.
    pub fn new(presence: Box<dyn Presence>) -> Firsts {
        Firsts::with(presence, Ledger::memory())
    }

    /// A prompt whose limits are kept in ``dir``, so a restart of the node forgets none of them.
    pub fn kept(presence: Box<dyn Presence>, dir: &std::path::Path) -> Firsts {
        Firsts::with(presence, Ledger::kept(dir))
    }

    fn with(presence: Box<dyn Presence>, ledger: Ledger) -> Firsts {
        Firsts { presence, askers: BTreeMap::new(), secret: None, ledger, cards: card::Cards::default() }
    }

    pub fn kind(&self) -> &'static str {
        self.presence.kind()
    }

    /// Whether a key made here can be above a typed yes: the person is asked at the operating system's own prompt. Where this is false the
    /// platform can make no such key, and a pool founded here is approved by a typed yes and says so.
    pub fn can_hold_counting_key(&self) -> bool {
        self.presence.kind() == "os"
    }

    /// The claim a key is recorded with when the person says yes for it at this node's prompt: `keystore-prompt` for the operating
    /// system's own prompt, `keystore-typed` for every other way of asking (a typed yes counts for nothing above a typed yes).
    pub fn pool_key_claim(&self) -> Claim {
        if self.can_hold_counting_key() { Claim::keystore_prompt() } else { Claim::keystore_typed() }
    }

    /// Whether a prompt can be raised for ``by`` now: it has none open, the node is not asking too many askers at once, and ``by`` is neither
    /// in the quiet after a no nor over its allowance.
    fn admit(&self, by: &str, now: u64) -> Result<()> {
        if self.askers.get(by).is_some_and(|a| a.asking.is_some()) {
            return Err(crate::limits::floor("The person is being asked something already: answer that first."));
        }
        if self.askers.values().filter(|a| a.asking.is_some()).count() >= MOST_OPEN {
            return Err(crate::limits::floor("The person is being asked a few things already: answer those first."));
        }
        self.ledger.allows(by, now)
    }

    /// Open a prompt for ``by`` (`board/name`): nothing is made until the person says yes.
    /// ``bound`` is the public key (hex) the first key will be: the yes is for that key only, and the dialog shows its words (empty for a phone,
    /// whose key is made by the phone after the scan and shown on both screens then).
    pub fn ask(&mut self, purpose: Purpose, by: &str, bound: &str, terminal: &str, now: u64) -> Result<()> {
        if self.askers.get(by).and_then(|a| a.asking.as_ref()).is_some_and(|p| p.purpose == purpose) {
            return Ok(());
        }
        self.admit(by, now)?;
        let key = match (purpose, bound) {
            (Purpose::Key, "") => return Err(Error::Invalid("The first key is asked for with its public key, so the yes is for that key.".into())),
            (Purpose::Key, public) => format!(" with the key {}", crate::phone::words(&crate::grants::person::fingerprint_of("ed25519", public)?)),
            (Purpose::Phone, _) => String::new(),
            (Purpose::PoolKey | Purpose::Card, _) => return Err(Error::Invalid("A change is asked for by its card.".into())),
        };
        let what = format!("poolhouse: allow the session {by} to add {}{key} to this computer?", purpose.words());
        let prompt = self.presence.begin(&what, terminal)?;
        let asker = self.askers.entry(by.into()).or_default();
        asker.asking = Some(Pending { purpose, bound: if purpose == Purpose::Key { bound.into() } else { String::new() }, started_ms: now, prompt });
        asker.outcome = None;
        Ok(())
    }

    /// Open a prompt for ``by`` (`board/name`) that says the sentence the node wrote for the card ``card``: a yes makes the ticket for that
    /// card, and for `PoolKey` also enrols the key the node made for the card and hands its seed to ``by`` once. A key is enrolled only
    /// through a card that made it, and a card that made one is asked for only as `PoolKey`. Asking again for the card being asked is the same ask.
    pub fn ask_card(&mut self, purpose: Purpose, by: &str, card: &str, terminal: &str, now: u64) -> Result<()> {
        if self.askers.get(by).and_then(|a| a.asking.as_ref()).is_some_and(|p| p.purpose == purpose && p.bound == card) {
            return Ok(());
        }
        self.admit(by, now)?;
        let held = self.cards.get(by, card, now).cloned().ok_or_else(|| Error::Invalid("There is no such change waiting: draft it again.".into()))?;
        match (purpose, held.generated.is_some()) {
            (Purpose::PoolKey, true) | (Purpose::Card, false) => {}
            (Purpose::PoolKey, false) => return Err(Error::Invalid("That change makes no key for this computer, so its yes enrols none.".into())),
            (Purpose::Card, true) => return Err(Error::Invalid("That change makes a key for this computer: it is asked for as a key.".into())),
            _ => return Err(Error::Invalid("Only a card is asked for by its id.".into())),
        }
        let prompt = self.presence.begin(&held.sentence, terminal)?;
        self.ledger.raised(by, now)?;
        let asker = self.askers.entry(by.into()).or_default();
        asker.asking = Some(Pending { purpose, bound: held.id, started_ms: now, prompt });
        asker.outcome = None;
        Ok(())
    }

    /// Show the node a payload a signer is about to sign: whether the ticket ``secret`` that ``by`` was given names it, and it is the first
    /// time. Each payload is shown once, a ticket lasts two minutes from the person's yes, and it is good only for the session it was given to.
    pub fn spend_payload(&mut self, by: &str, secret: &str, payload: &str, now: u64) -> std::result::Result<(), &'static str> {
        let Some(asker) = self.askers.get_mut(by) else { return Err("No yes was given for that, or it was used up: ask the person again.") };
        let Some(t) = asker.ticket.as_mut() else { return Err("No yes was given for that, or it was used up: ask the person again.") };
        if secret.is_empty() || !same(&t.sha256, &sha256_hex(secret.as_bytes())) {
            return Err("That ticket is not the one the person's yes made for this session.");
        }
        if now.saturating_sub(t.made_ms) > card::TICKET_FOR_MS {
            asker.ticket = None;
            return Err("The person's yes is more than two minutes old: ask again.");
        }
        if !t.left.remove(payload) {
            return Err("The person's yes was not for those bytes, or they were signed already.");
        }
        Ok(())
    }

    /// The key ``by``'s yes enrolled and the node has not yet entered in the person keys, once.
    pub fn take_enrolment(&mut self, by: &str) -> Option<Generated> {
        self.askers.get_mut(by).and_then(|a| a.ticket.as_mut()).and_then(|t| t.enrol.take())
    }

    /// Whether ``by`` answered yes for the card that founds the charter of ``anchor`` less than ten minutes ago; true once.
    pub fn founding_confirmed(&mut self, by: &str, anchor: &str, now: u64) -> bool {
        let Some(asker) = self.askers.get_mut(by) else { return false };
        let ok = asker.founded.as_ref().is_some_and(|f| f.anchor == anchor && now.saturating_sub(f.made_ms) <= SECRET_FOR_MS);
        if ok {
            asker.founded = None;
        }
        ok
    }

    fn poll(&mut self, now: u64) {
        let open: Vec<String> = self.askers.iter().filter(|(_, a)| a.asking.is_some()).map(|(by, _)| by.clone()).collect();
        for by in open {
            let Some(asker) = self.askers.get_mut(&by) else { continue };
            let Some(p) = asker.asking.as_mut() else { continue };
            let said = p.prompt.poll();
            // What a yes makes lives from the yes, which the prompt noted, and not from the poll that found it.
            let yes_at = now.saturating_sub(p.prompt.age_ms());
            let late = match said {
                Some(_) => yes_at.saturating_sub(p.started_ms) > ASK_FOR_MS,
                None => now.saturating_sub(p.started_ms) > ASK_FOR_MS,
            };
            if said.is_none() && !late {
                continue;
            }
            let Some(done) = asker.asking.take() else { continue };
            let outcome = match said {
                Some(true) if !late && matches!(done.purpose, Purpose::PoolKey | Purpose::Card) => self.make_ticket(&by, &done, yes_at),
                Some(true) if !late => self.make(done.purpose, &done.bound, yes_at),
                Some(false) => Outcome::No,
                _ => Outcome::Late,
            };
            let granted = matches!(outcome, Outcome::Yes(..));
            self.askers.entry(by.clone()).or_default().outcome = Some(outcome);
            if !granted {
                // A refusal is the person's word: the node records it where a restart does not forget it, and a full disk is said, not hidden.
                if let Err(e) = self.ledger.refused(&by, now) {
                    eprintln!("node: {e}");
                }
            }
        }
    }

    /// A live phone yes belonging to this requester, before its one-time secret is read.
    pub(crate) fn phone_ready(&mut self, by: &str, now: u64) -> bool {
        self.poll(now);
        self.offered(Purpose::Phone, now)
            && matches!(self.askers.get(by).and_then(|a| a.outcome.as_ref()), Some(Outcome::Yes(Some(_), expires, Purpose::Phone)) if now <= *expires)
    }

    /// What became of the prompt ``by`` opened, as a reply: the secret comes once, in the reply that first says yes.
    pub fn status(&mut self, by: &str, now: u64) -> Value {
        self.poll(now);
        let Some(asker) = self.askers.get_mut(by) else { return json!({"state": "none"}) };
        if let Some(p) = asker.asking.as_ref() {
            return json!({"state": "asking", "purpose": p.purpose.words(), "left_ms": ASK_FOR_MS.saturating_sub(now.saturating_sub(p.started_ms))});
        }
        match &mut asker.outcome {
            Some(Outcome::Yes(_, expires, _)) if now > *expires => json!({"state": "expired"}),
            Some(Outcome::Yes(secret, expires, purpose)) => json!({"state": "confirmed", "purpose": purpose.name(), "secret": secret.take(), "expires_ms": *expires}),
            Some(Outcome::No) => json!({"state": "refused"}),
            Some(Outcome::Late) => json!({"state": "expired"}),
            None => json!({"state": "none"}),
        }
    }

    /// The ticket for the card ``done`` was asked for, made at the yes ``yes_at``: the secret is handed once, to the asker; the node keeps only its hash.
    fn make_ticket(&mut self, by: &str, done: &Pending, yes_at: u64) -> Outcome {
        let Some(held) = self.cards.get(by, &done.bound, yes_at).cloned() else { return Outcome::No };
        let mut raw = [0u8; 32];
        if getrandom::fill(&mut raw).is_err() {
            return Outcome::No;
        }
        let secret = crate::phone::enrol::b64url(&raw);
        let enrol = if done.purpose == Purpose::PoolKey { self.cards.take_generated(by, &done.bound) } else { None };
        let asker = self.askers.entry(by.into()).or_default();
        asker.ticket = Some(Ticket { sha256: sha256_hex(secret.as_bytes()), left: held.payloads.iter().cloned().collect(), made_ms: yes_at, enrol });
        if let Some(anchor) = held.founding {
            asker.founded = Some(Founded { anchor, made_ms: yes_at });
        }
        Outcome::Yes(Some(secret), yes_at + card::TICKET_FOR_MS, done.purpose)
    }

    fn make(&mut self, purpose: Purpose, bound: &str, yes_at: u64) -> Outcome {
        let mut raw = [0u8; 32];
        if getrandom::fill(&mut raw).is_err() {
            return Outcome::No;
        }
        let secret = crate::phone::enrol::b64url(&raw);
        self.secret = Some(Secret { purpose, bound: bound.into(), sha256: sha256_hex(secret.as_bytes()), at_ms: yes_at });
        Outcome::Yes(Some(secret), yes_at + SECRET_FOR_MS, purpose)
    }

    /// Whether a secret of ``purpose`` is waiting to be used.
    pub fn offered(&self, purpose: Purpose, now: u64) -> bool {
        self.secret.as_ref().is_some_and(|s| s.purpose == purpose && now >= s.at_ms && now - s.at_ms <= SECRET_FOR_MS)
    }

    /// Whether ``secret`` is the one waiting for ``purpose``, without spending it: a guess from the network never ends the offer.
    pub fn matches(&self, purpose: Purpose, secret: &str, now: u64) -> bool {
        self.offered(purpose, now) && !secret.is_empty() && self.secret.as_ref().is_some_and(|s| same(&s.sha256, &sha256_hex(secret.as_bytes())))
    }

    /// The secret waiting is used up.
    pub fn spend(&mut self) {
        self.secret = None;
    }

    /// Spend the secret of ``purpose`` for the key ``bound`` (the one the yes was for; empty for a phone): it is good once, for ten minutes, and one wrong guess spends it too.
    pub fn take(&mut self, purpose: Purpose, secret: &str, bound: &str, now: u64) -> bool {
        let found = self.secret.take();
        found.is_some_and(|s| s.purpose == purpose && same(&s.bound, bound) && now >= s.at_ms && now - s.at_ms <= SECRET_FOR_MS && !secret.is_empty() && same(&s.sha256, &sha256_hex(secret.as_bytes())))
    }
}
