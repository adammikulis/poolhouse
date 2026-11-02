//! The cards a person answers at the computer's own prompt, and the tickets that answer makes.
//!
//! A card is a change the node itself drafted (`pool_draft_found`, `pool_draft`): the node holds the bytes each key would sign and writes
//! the sentence the prompt shows from those bytes, the pool's name and a short fingerprint, never from the words of the session that asked.
//! When the person says yes at the operating system's prompt the node issues a ticket for that card: a secret handed once to the session
//! that asked, good for two minutes, that the signer shows the node once for each payload it is about to sign. The ticket names the SHA-256
//! of each payload, so it signs those bytes and no others, and each only once. The node holds no key that signs: this is the only thing the
//! signer in the person-facing process needs from the node before it unseals a key.

use std::collections::BTreeMap;

use crate::fsutil::sha256_hex;

/// How long a card waits to be answered, and how long a ticket lasts.
pub const CARD_FOR_MS: u64 = 10 * 60_000;
pub const TICKET_FOR_MS: u64 = 2 * 60_000;
/// How many cards the node keeps at once.
const MOST_CARDS: usize = 16;
/// The most a prompt's sentence may hold: the operating system's dialog shows no more, and the node never cuts a sentence.
pub const MOST_SENTENCE: usize = 160;
/// The most payloads one card names: a bundle (the charter's acceptances, for example) costs one prompt.
pub const MOST_PAYLOADS: usize = 10;

/// What the node says when a name cannot be shown in the operating system's prompt.
pub const UNSHOWABLE: &str = "This name has characters the computer's prompt cannot show, or is too long to confirm there: use letters, digits, spaces and dashes, and keep it short.";

/// One change waiting for a person's yes.
#[derive(Clone, Debug)]
pub struct Card {
    pub id: String,
    /// What the prompt says, written by the node.
    pub sentence: String,
    /// The SHA-256 of each payload a ticket for this card lets a key sign.
    pub payloads: Vec<String>,
    /// The public keys (hex) that sign them.
    pub keys: Vec<String>,
    /// The anchor of the charter this card founds, when it founds a pool.
    pub founding: Option<String>,
    made_ms: u64,
}

/// The cards the node has drafted and not forgotten.
#[derive(Default)]
pub struct Cards {
    held: BTreeMap<String, Card>,
}

/// Whether ``text`` is what the operating system's prompt shows as it stands: plain, and short enough not to be cut.
pub fn shows_whole(text: &str) -> bool {
    text.chars().count() <= MOST_SENTENCE && super::plain(text) == text
}

impl Cards {
    /// Keep a card of ``sentence`` and the ``payloads`` the ``keys`` would sign; its id is a hash of the payloads, so drafting the same change
    /// again is the same card. Refuses a sentence the prompt cannot show whole.
    pub fn add(&mut self, sentence: &str, payloads: &[Vec<u8>], keys: Vec<String>, founding: Option<String>, now: u64) -> Result<String, &'static str> {
        if !shows_whole(sentence) || payloads.is_empty() || payloads.len() > MOST_PAYLOADS {
            return Err(UNSHOWABLE);
        }
        let hashes: Vec<String> = payloads.iter().map(|p| sha256_hex(p)).collect();
        let id = sha256_hex(hashes.join("\n").as_bytes());
        self.held.retain(|_, c| now.saturating_sub(c.made_ms) <= CARD_FOR_MS);
        while self.held.len() >= MOST_CARDS {
            let Some(oldest) = self.held.iter().min_by_key(|(_, c)| c.made_ms).map(|(k, _)| k.clone()) else { break };
            self.held.remove(&oldest);
        }
        self.held.entry(id.clone()).or_insert(Card { id: id.clone(), sentence: sentence.into(), payloads: hashes, keys, founding, made_ms: now });
        Ok(id)
    }

    /// The card ``id``, while it is still waiting.
    pub fn get(&self, id: &str, now: u64) -> Option<&Card> {
        self.held.get(id).filter(|c| now.saturating_sub(c.made_ms) <= CARD_FOR_MS)
    }
}
