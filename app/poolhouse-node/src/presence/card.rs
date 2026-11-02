//! The cards a person answers at the computer's own prompt, and the tickets that answer makes.
//!
//! A card is a change the node itself drafted (`pool_draft_found`, `pool_draft`, `op_draft`) for the person's own session: the node holds the
//! bytes each key would sign and writes the sentence the prompt shows from those bytes, the pool's name and a short fingerprint, never from
//! the words of the session that asked. When the person says yes at the operating system's prompt the node issues a ticket for that card: a
//! secret handed once to the session that asked, good for two minutes from the yes, that the signer shows the node once for each payload it is
//! about to sign. The ticket names the SHA-256 of each payload, so it signs those bytes and no others, and each only once. A card that
//! founds a pool may carry a key the node made for it: the yes enrols that key and no other, and hands its seed to the asker once.

use std::collections::BTreeMap;

use crate::fsutil::sha256_hex;

/// How long a card waits to be answered, and how long a ticket lasts from the yes.
pub const CARD_FOR_MS: u64 = 10 * 60_000;
pub const TICKET_FOR_MS: u64 = 2 * 60_000;
/// How many cards the node keeps at once for each asker: one asker filling its own cards evicts no other's.
pub(crate) const MOST_CARDS: usize = 16;
/// The most a prompt's sentence may hold: the operating system's dialog shows no more, and the node never cuts a sentence.
pub const MOST_SENTENCE: usize = 240;
/// The most payloads one card names: a bundle (the charter's acceptances, for example) costs one prompt.
pub const MOST_PAYLOADS: usize = 10;

/// What the node says when a name cannot be shown in the operating system's prompt.
pub const UNSHOWABLE: &str = "This name has characters the computer's prompt cannot show, or is too long to confirm there: use letters, digits, spaces and dashes, and keep it short.";

/// A key the node made for a card: its public half (hex) and its seed, which the node gives once to the person's session that answers the
/// card and keeps nowhere else.
#[derive(Clone)]
pub struct Generated {
    pub public: String,
    pub seed: [u8; 32],
}

impl std::fmt::Debug for Generated {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Generated {{ public: {}, seed: .. }}", self.public)
    }
}

/// One change waiting for a person's yes.
#[derive(Clone, Debug)]
pub struct Card {
    pub id: String,
    /// What the prompt says, written by the node.
    pub sentence: String,
    /// The SHA-256 of each payload a ticket for this card lets a key sign.
    pub payloads: Vec<String>,
    /// The anchor of the charter this card founds, when it founds a pool.
    pub founding: Option<String>,
    /// The key the node made for this card, when the card's yes enrols one.
    pub generated: Option<Generated>,
    made_ms: u64,
}

/// The cards the node has drafted and not forgotten, by the session they were drafted for.
#[derive(Default)]
pub struct Cards {
    held: BTreeMap<(String, String), Card>,
}

/// Whether ``text`` is what the operating system's prompt shows as it stands: plain, and short enough not to be cut.
pub fn shows_whole(text: &str) -> bool {
    text.chars().count() <= MOST_SENTENCE && super::plain(text) == text
}

/// The change a card is made of, before it is kept.
pub struct Draft<'a> {
    pub sentence: &'a str,
    pub payloads: &'a [Vec<u8>],
    pub founding: Option<String>,
    pub generated: Option<Generated>,
}

impl Cards {
    /// Keep a card for ``by``; its id is a hash of the payloads, so drafting the same change again is the same card. Refuses a sentence the
    /// prompt cannot show whole. The oldest of ``by``'s own cards makes room, never another session's.
    pub fn add(&mut self, by: &str, draft: Draft<'_>, now: u64) -> Result<String, &'static str> {
        if !shows_whole(draft.sentence) || draft.payloads.is_empty() || draft.payloads.len() > MOST_PAYLOADS {
            return Err(UNSHOWABLE);
        }
        let hashes: Vec<String> = draft.payloads.iter().map(|p| sha256_hex(p)).collect();
        let id = sha256_hex(hashes.join("\n").as_bytes());
        self.held.retain(|_, c| now.saturating_sub(c.made_ms) <= CARD_FOR_MS);
        let key = (by.to_string(), id.clone());
        while !self.held.contains_key(&key) && self.held.keys().filter(|(who, _)| who == by).count() >= MOST_CARDS {
            let Some(oldest) = self.held.iter().filter(|((who, _), _)| who == by).min_by_key(|(_, c)| c.made_ms).map(|(k, _)| k.clone()) else { break };
            self.held.remove(&oldest);
        }
        self.held.entry(key).or_insert(Card { id: id.clone(), sentence: draft.sentence.into(), payloads: hashes, founding: draft.founding, generated: draft.generated, made_ms: now });
        Ok(id)
    }

    /// The card ``id`` drafted for ``by``, while it is still waiting.
    pub fn get(&self, by: &str, id: &str, now: u64) -> Option<&Card> {
        self.held.get(&(by.to_string(), id.to_string())).filter(|c| now.saturating_sub(c.made_ms) <= CARD_FOR_MS)
    }

    /// The key the card ``id`` of ``by`` made, once: the yes that takes it hands it on, and the card keeps no copy.
    pub fn take_generated(&mut self, by: &str, id: &str) -> Option<Generated> {
        self.held.get_mut(&(by.to_string(), id.to_string())).and_then(|c| c.generated.take())
    }

    /// How many cards ``by`` has waiting.
    pub fn count(&self, by: &str) -> usize {
        self.held.keys().filter(|(who, _)| who == by).count()
    }
}
