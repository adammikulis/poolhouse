//! The prompt, what became of it, and what it made, for each asker.
//!
//! Every session that asks has a state of its own: the prompt it has open, its answer, its ticket and its founding yes. One session's
//! questions, refusals and cards never take the place of another's.
//!
//! Nothing here ever holds a request back for how often, or how recently, the person was asked or answered. The only refusal is that an asker
//! has one prompt open and asks for another: it is told what is open. A request the person has not seen (a dialog hidden behind windows, one
//! that never opened, one that ran out) is a request that must be possible to raise again at once, so a no, a failure and a timeout leave
//! nothing behind but their own outcome, and nothing is kept in a file: a node that starts again has forgotten none of it because there is none.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};

use super::card::{self, Generated};
use super::os::ASK_FOR_MS;
use super::{same, Asking, Presence, Purpose};
use crate::error::{Error, Result};
use crate::fsutil::sha256_hex;
use crate::gov::keys::Claim;

/// How long what a yes makes (the one-time secret, a founding yes) is good for.
pub(crate) const SECRET_FOR_MS: u64 = 10 * 60_000;

/// What the asker is told when the person's yes found nothing to answer for, or the computer could not make the secret a yes makes: failures of the
/// node, which are not the person's no.
const CARD_GONE: &str = "The person said yes, but the change it was for is no longer waiting (a change waits ten minutes): draft it again and ask again.";
const NO_RANDOM: &str = "The person said yes, but this computer could not make the secret a yes needs: ask again.";

/// A prompt that is open.
struct Pending {
    purpose: Purpose,
    /// The public key (hex) the first key will be, or the id of the card asked.
    bound: String,
    /// The sentence the person is shown, which every report of the open request says as it is.
    sentence: String,
    started_ms: u64,
    prompt: Box<dyn Asking>,
}

/// The ticket a yes for a card made: the card, the hash of its secret, the payloads not yet signed and the key it enrols.
struct Ticket {
    sha256: String,
    left: BTreeSet<String>,
    made_ms: u64,
    /// The key the node made for the card, until it is handed to the asker.
    seed: Option<Generated>,
    /// The key (public, claim) the founding of the card's charter enrols, until the pool is founded.
    enrol: Option<(String, Claim)>,
}

/// A founding card that was answered yes, waiting for the charter it founds.
struct Founded {
    anchor: String,
    made_ms: u64,
}

enum Outcome {
    /// Confirmed: the secret, until the session that asked has read it.
    Yes(Option<String>, u64, Purpose),
    /// The person declined at the dialog.
    No,
    /// Nobody answered in time. It is not a no.
    Late,
    /// The dialog could not be shown or failed, in the system's words. It is not a no, and the request is raised again by asking again.
    Failed(String),
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
    /// The changes the node drafted that a person may answer.
    pub cards: card::Cards,
}

impl Firsts {
    pub fn new(presence: Box<dyn Presence>) -> Firsts {
        Firsts { presence, askers: BTreeMap::new(), secret: None, cards: card::Cards::default() }
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

    /// Whether a prompt can be raised for ``by``: it has none open. An asker with one open is told what it is, in the person's words, so it can
    /// tell the person; it is never told to wait for a time to pass, because nothing is waiting for a time to pass.
    fn admit(&self, by: &str, now: u64) -> Result<()> {
        match self.askers.get(by).and_then(|a| a.asking.as_ref()) {
            Some(open) => Err(crate::limits::floor(&format!(
                "The person is already being asked: {} Answer that one, or let it end ({} seconds left), before asking for another.",
                open.sentence, ASK_FOR_MS.saturating_sub(now.saturating_sub(open.started_ms)) / 1000
            ))),
            None => Ok(()),
        }
    }

    /// Take in what the dialogs that ended said, so that a report of what waits (`waiting`) is of what still does.
    pub fn settle(&mut self, now: u64) {
        self.poll(now);
    }

    /// The requests that wait for the person right now, each as the sentence they are shown: what `pool_status` reports, so a dialog that is
    /// hidden, or a notification that was missed, is not the only place the person can learn there is something to answer.
    pub fn waiting(&self, now: u64) -> Vec<Value> {
        self.askers.iter().filter_map(|(by, asker)| asker.asking.as_ref().map(|p| (by, p))).filter(|(_, p)| now.saturating_sub(p.started_ms) <= ASK_FOR_MS).map(|(by, p)| {
            json!({"by": by, "say": format!("waiting for you: {}", p.sentence), "sentence": p.sentence, "via": self.presence.kind(), "left_ms": ASK_FOR_MS.saturating_sub(now.saturating_sub(p.started_ms))})
        }).collect()
    }

    /// Whether the presence of this node asks only on the controlling terminal of the process that asks.
    pub fn asks_on_own_terminal(&self) -> bool {
        self.presence.own_terminal()
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
        asker.asking = Some(Pending { purpose, bound: if purpose == Purpose::Key { bound.into() } else { String::new() }, sentence: super::plain(&what), started_ms: now, prompt });
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
        let asker = self.askers.entry(by.into()).or_default();
        asker.asking = Some(Pending { purpose, bound: held.id, sentence: held.sentence, started_ms: now, prompt });
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

    /// The key ``by``'s yes was for, with its secret, which the asker is given once. Nothing is enrolled by it: the key is entered in the person keys
    /// only when the pool it founds is (`founding_yes`).
    pub fn take_key(&mut self, by: &str) -> Option<Generated> {
        self.askers.get_mut(by).and_then(|a| a.ticket.as_mut()).and_then(|t| t.seed.take())
    }

    /// What ``by``'s yes less than ten minutes ago for the card that founds the charter of ``anchor`` lets the founding do: None when there is no such yes,
    /// else the key (public half and claim) the founding enrols, if the card made one. The yes is kept until `founded`, so a founding that fails leaves it.
    pub fn founding_yes(&self, by: &str, anchor: &str, now: u64) -> Option<Option<(String, Claim)>> {
        let asker = self.askers.get(by)?;
        asker.founded.as_ref().filter(|f| f.anchor == anchor && now.saturating_sub(f.made_ms) <= SECRET_FOR_MS)?;
        Some(asker.ticket.as_ref().and_then(|t| t.enrol.clone()))
    }

    /// The pool the yes was for is founded: the yes is spent.
    pub fn founded(&mut self, by: &str) {
        if let Some(asker) = self.askers.get_mut(by) {
            asker.founded = None;
            if let Some(t) = asker.ticket.as_mut() {
                t.enrol = None;
            }
        }
    }

    fn poll(&mut self, now: u64) {
        let open: Vec<String> = self.askers.iter().filter(|(_, a)| a.asking.is_some()).map(|(by, _)| by.clone()).collect();
        for by in open {
            let Some(asker) = self.askers.get_mut(&by) else { continue };
            let Some(p) = asker.asking.as_mut() else { continue };
            let said = p.prompt.poll();
            // A dialog that could not be shown has no answer; it says why instead. It is never read as the person's no.
            let failed = if said.is_none() { p.prompt.failed() } else { None };
            // What a yes makes lives from the yes, which the prompt noted, and not from the poll that found it.
            let yes_at = now.saturating_sub(p.prompt.age_ms());
            let late = match said {
                Some(_) => yes_at.saturating_sub(p.started_ms) > ASK_FOR_MS,
                None => now.saturating_sub(p.started_ms) > ASK_FOR_MS,
            };
            if said.is_none() && failed.is_none() && !late {
                continue;
            }
            let Some(done) = asker.asking.take() else { continue };
            let outcome = match (said, failed) {
                (Some(true), _) if !late && matches!(done.purpose, Purpose::PoolKey | Purpose::Card) => self.make_ticket(&by, &done, yes_at),
                (Some(true), _) if !late => self.make(done.purpose, &done.bound, yes_at),
                (Some(false), _) => Outcome::No,
                (None, Some(why)) => Outcome::Failed(why),
                _ => Outcome::Late,
            };
            self.askers.entry(by.clone()).or_default().outcome = Some(outcome);
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
            return json!({"state": "asking", "purpose": p.purpose.words(), "sentence": p.sentence, "left_ms": ASK_FOR_MS.saturating_sub(now.saturating_sub(p.started_ms))});
        }
        match &mut asker.outcome {
            Some(Outcome::Yes(_, expires, _)) if now > *expires => json!({"state": "expired"}),
            Some(Outcome::Yes(secret, expires, purpose)) => json!({"state": "confirmed", "purpose": purpose.name(), "secret": secret.take(), "expires_ms": *expires}),
            Some(Outcome::No) => json!({"state": "refused"}),
            Some(Outcome::Late) => json!({"state": "expired"}),
            Some(Outcome::Failed(why)) => json!({"state": "failed", "error": why}),
            None => json!({"state": "none"}),
        }
    }

    /// The ticket for the card ``done`` was asked for, made at the yes ``yes_at``: the secret is handed once, to the asker; the node keeps only its hash.
    fn make_ticket(&mut self, by: &str, done: &Pending, yes_at: u64) -> Outcome {
        let Some(held) = self.cards.get(by, &done.bound, yes_at).cloned() else { return Outcome::Failed(CARD_GONE.into()) };
        let mut raw = [0u8; 32];
        if getrandom::fill(&mut raw).is_err() {
            return Outcome::Failed(NO_RANDOM.into());
        }
        let secret = crate::phone::enrol::b64url(&raw);
        let seed = if done.purpose == Purpose::PoolKey { self.cards.take_generated(by, &done.bound) } else { None };
        let enrol = seed.as_ref().map(|made| (made.public.clone(), made.claim));
        let asker = self.askers.entry(by.into()).or_default();
        asker.ticket = Some(Ticket { sha256: sha256_hex(secret.as_bytes()), left: held.payloads.iter().cloned().collect(), made_ms: yes_at, seed, enrol });
        if let Some(anchor) = held.founding {
            asker.founded = Some(Founded { anchor, made_ms: yes_at });
        }
        Outcome::Yes(Some(secret), yes_at + card::TICKET_FOR_MS, done.purpose)
    }

    fn make(&mut self, purpose: Purpose, bound: &str, yes_at: u64) -> Outcome {
        let mut raw = [0u8; 32];
        if getrandom::fill(&mut raw).is_err() {
            return Outcome::Failed(NO_RANDOM.into());
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
