//! Names and tokens. A session gets one unique readable name per board, made from its model
//! family and a suffix of the hash of its native identity; the node stamps every write with
//! the name its token resolves to, so no request ever says who it is.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::error::{Error, Result};
use crate::fsutil::{hex, random_hex, sha256_hex, write_atomic};
use crate::lineage::{shares, Anchor, ENFORCED};

pub const SHORT: usize = 6;
pub const MAX_IDENTITIES: usize = 10_000;
const MAX_TOKENS_PER_NAME: usize = 8;
/// How many tokens a node keeps; when it is full, the tokens of sessions idle for `IDLE_MS` make room.
pub const MOST_TOKENS: usize = 2048;
pub const IDLE_MS: u64 = 30 * 24 * 3600 * 1000;

/// The lower-case model family of an exact model id (`agent` when it names none).
pub fn family_word(model: &str) -> &'static str {
    let model = model.to_lowercase();
    let model = model.rsplit('/').next().unwrap_or("");
    if model.starts_with("claude-") {
        "claude"
    } else if ["gpt-", "chatgpt-", "o1", "o3", "o4"].iter().any(|p| model.starts_with(p)) {
        "chatgpt"
    } else if model.starts_with("qwen") || model.starts_with("thinkingcap-qwen") {
        "qwen"
    } else {
        "agent"
    }
}

/// The full identity of a native session: SHA-256 of harness, a NUL, and the session id.
pub fn digest(harness: &str, session: &str) -> String {
    hex(&Sha256::digest(format!("{harness}\0{session}").as_bytes()))
}

/// How far a session's model id is believed: what it said, or what the node checked.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ModelState {
    Unknown,
    Claimed,
    /// A subagent that named no model runs the one its parent runs.
    Inherited,
    Verified,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Ident {
    pub digest: String,
    pub family: String,
    pub parent: String,
    pub model: String,
    pub model_state: ModelState,
    pub harness: String,
    pub retired: bool,
    /// Why a retired session ended: empty or ``done`` for an ordinary end, ``revoked`` or ``forged`` for a cause.
    #[serde(default)]
    pub retired_reason: String,
    /// Wall milliseconds of the last request this session made (written at most every 30 s).
    pub seen_ms: u64,
    /// The processes the session was registered from (`lineage`); empty when they were not known.
    #[serde(default)]
    pub lineage: Vec<Anchor>,
    /// How far the session has read its inbox: the furthest cursor it presented when it read it, by origin.
    #[serde(default)]
    pub inbox_read: BTreeMap<String, u64>,
}

/// A model id or harness name as one short clean line.
pub fn clean(text: &str, most: usize) -> Result<String> {
    let text = text.trim();
    if text.len() > most || text.chars().any(char::is_control) {
        return Err(Error::Invalid(format!("a model or harness is one clean line of at most {most} bytes")));
    }
    Ok(text.into())
}

/// The names of one board.
pub struct Registry {
    path: PathBuf,
    names: BTreeMap<String, Ident>,
}

impl Registry {
    pub fn open(path: &Path) -> Result<Registry> {
        let names = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("names unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        Ok(Registry { path: path.into(), names })
    }

    pub fn contains(&self, name: &str) -> bool {
        self.names.contains_key(name)
    }

    pub fn get(&self, name: &str) -> Option<&Ident> {
        self.names.get(name)
    }

    /// The main session ``name`` belongs to: the top of the chain of sessions that started it, which the node knows from who registered
    /// whom and not from anything a session says about itself.
    pub fn root(&self, name: &str) -> String {
        let mut at = name;
        for _ in 0..16 {
            match self.names.get(at) {
                Some(i) if !i.parent.is_empty() => at = i.parent.as_str(),
                _ => break,
            }
        }
        at.to_string()
    }

    pub fn all(&self) -> &BTreeMap<String, Ident> {
        &self.names
    }

    fn save(&self) -> Result<()> {
        write_atomic(&self.path, &serde_json::to_vec(&self.names)?)
    }

    /// The name already given to this native session, if any.
    pub fn find(&self, harness: &str, session: &str) -> Option<&str> {
        let full = digest(harness, session);
        self.names.iter().find(|(_, i)| i.digest == full).map(|(n, _)| n.as_str())
    }

    /// Record what a session says it runs on; a verified model is never lowered to a claim.
    pub fn claim_model(&mut self, name: &str, model: &str, harness: &str) -> Result<bool> {
        let (model, harness) = (clean(model, 256)?, clean(harness, 64)?);
        let ident = self.names.get_mut(name).ok_or_else(|| Error::Denied("no such session".into()))?;
        let new_harness = if harness.is_empty() { ident.harness.clone() } else { harness };
        let (new, state) = match (model.is_empty(), ident.model_state) {
            (true, state) => (ident.model.clone(), state),
            (false, ModelState::Verified) if model == ident.model => (model, ModelState::Verified),
            (false, ModelState::Verified) => return Err(Error::Denied("the model of this session was verified; a claim cannot change it".into())),
            (false, _) => (model, ModelState::Claimed),
        };
        let changed = (new.as_str(), new_harness.as_str(), state) != (ident.model.as_str(), ident.harness.as_str(), ident.model_state);
        (ident.model, ident.harness, ident.model_state) = (new, new_harness, state);
        if changed {
            self.save()?;
        }
        Ok(changed)
    }

    /// Record that the node checked ``name`` runs ``model``; only the node's own checks call this.
    pub fn verify_model(&mut self, name: &str, model: &str) -> Result<()> {
        let model = clean(model, 256)?;
        let ident = self.names.get_mut(name).ok_or_else(|| Error::Denied("no such session".into()))?;
        (ident.model, ident.model_state) = (model, ModelState::Verified);
        self.save()
    }

    /// Mark ``name`` retired for ``reason`` (``done``, ``revoked`` or ``forged``); true when it was not already.
    pub fn retire(&mut self, name: &str, reason: &str) -> Result<bool> {
        let ident = self.names.get_mut(name).ok_or_else(|| Error::Denied("no such session".into()))?;
        let fresh = !ident.retired;
        ident.retired = true;
        ident.retired_reason = reason.into();
        self.save()?;
        Ok(fresh)
    }

    /// Note that ``name`` was just active, writing at most every 30 seconds.
    pub fn touch(&mut self, name: &str, now_ms: u64) -> Result<()> {
        match self.names.get_mut(name) {
            Some(i) if now_ms >= i.seen_ms + 30_000 => {
                i.seen_ms = now_ms;
                self.save()
            }
            _ => Ok(()),
        }
    }

    /// Note that ``name`` read its inbox from ``cursor`` on: what is before it was read. Written only when it moved.
    pub fn note_inbox_read(&mut self, name: &str, cursor: &BTreeMap<String, u64>) -> Result<()> {
        let Some(ident) = self.names.get_mut(name) else { return Ok(()) };
        let mut moved = false;
        for (origin, seq) in cursor {
            let seen = ident.inbox_read.entry(origin.clone()).or_insert(0);
            if *seq > *seen {
                *seen = *seq;
                moved = true;
            }
        }
        if moved { self.save() } else { Ok(()) }
    }

    /// ``name`` and everything below it by parent links.
    pub fn descendants(&self, name: &str) -> Vec<String> {
        let mut out = Vec::new();
        let mut frontier = vec![name.to_string()];
        while let Some(top) = frontier.pop() {
            for (n, i) in &self.names {
                if i.parent == top && !out.contains(n) {
                    out.push(n.clone());
                    frontier.push(n.clone());
                }
            }
        }
        out
    }

    pub fn len(&self) -> usize {
        self.names.len()
    }

    pub fn is_empty(&self) -> bool {
        self.names.is_empty()
    }

    /// The unique name of this session, recorded so no later session takes the same one. The
    /// same session always gets the same name back (``true`` when it is new), to a process of the lineage it was
    /// registered from only (``proven``: the request carried a valid token of that name); a session whose short suffix
    /// collides with another's gets a longer one.
    pub fn assign(&mut self, who: Who, model: &str, state: ModelState, now_ms: u64, lineage: &[Anchor], proven: bool) -> Result<(String, bool)> {
        let Who { harness, session, parent } = who;
        if session.is_empty() || harness.is_empty() {
            return Err(Error::Denied("a session name needs the native harness and session id".into()));
        }
        let (full, word) = (digest(harness, session), family_word(model));
        let model = clean(model, 256)?;
        let known = self.names.iter().find(|(_, i)| i.digest == full).map(|(n, _)| n.clone());
        if let Some(name) = known {
            self.admit(&name, parent, lineage, proven)?;
            return Ok((name, false));
        }
        if self.names.len() >= MAX_IDENTITIES {
            return Err(Error::Quota("this board holds as many sessions as it will".into()));
        }
        let name = (SHORT..=full.len()).step_by(2).map(|w| format!("{word}-{}", &full[..w])).find(|n| !self.names.contains_key(n))
            .ok_or_else(|| Error::Quota("no free name".into()))?;
        let state = if model.is_empty() { ModelState::Unknown } else { state };
        self.names.insert(name.clone(), Ident {
            digest: full, family: word.into(), parent: parent.into(), model, model_state: state, harness: clean(harness, 64)?,
            retired: false, retired_reason: String::new(), seen_ms: now_ms, lineage: lineage.to_vec(), inbox_read: BTreeMap::new(),
        });
        self.save()?;
        Ok((name, true))
    }

    /// Whether the session ``name`` may be registered again by a process of ``lineage`` under ``parent``: a retired session
    /// never, a subagent only under its own parent, a main session only from the lineage it was registered from. A main
    /// session with no lineage recorded (registered before lineages were kept, or by a process that could not be read) is taken
    /// by a process that presents a token of that name (``proven``), and by nobody else until a person moves it.
    fn admit(&mut self, name: &str, parent: &str, lineage: &[Anchor], proven: bool) -> Result<()> {
        let known = self.names.get_mut(name).expect("found above");
        if known.retired {
            return Err(Error::Denied(format!("{name} was retired; its session cannot register again")));
        }
        if known.parent != parent {
            return Err(Error::Denied(format!("{name} is already registered under another parent")));
        }
        if !parent.is_empty() || !ENFORCED {
            return Ok(());
        }
        if known.lineage.is_empty() {
            if !proven || lineage.is_empty() {
                return Err(Error::Denied("this session has no process lineage recorded, so a process cannot be told from the one that registered it; \
                                          a process that holds one of its tokens registers it again, or a person moves it to the terminal where it \
                                          runs now with `poolhouse-workspace rebind HARNESS SESSION`, which ends the tokens it holds".into()));
            }
            known.lineage = lineage.to_vec();
            return self.save();
        }
        if shares(&known.lineage, lineage) {
            return Ok(());
        }
        Err(Error::Denied("this session is registered from another process lineage (another terminal or program); a person moves it to the terminal \
                           where it runs now with `poolhouse-workspace rebind HARNESS SESSION`, which ends the tokens it holds".into()))
    }

    /// Move the main session ``name`` to ``lineage``: it is registered from there from now on.
    pub fn rebind(&mut self, name: &str, lineage: &[Anchor]) -> Result<()> {
        let ident = self.names.get_mut(name).ok_or_else(|| Error::Denied("no such session".into()))?;
        if ident.retired || !ident.parent.is_empty() {
            return Err(Error::Denied("only a main session that has not retired is moved".into()));
        }
        ident.lineage = lineage.to_vec();
        self.save()
    }
}

/// The native session a registration names: its harness, its id and the parent it registers under (empty for a main session).
#[derive(Clone, Copy)]
pub struct Who<'a> {
    pub harness: &'a str,
    pub session: &'a str,
    pub parent: &'a str,
}

/// Where a token leads: a board and a name on it.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Holder {
    pub board: String,
    pub name: String,
}

/// A token as the file keeps it: whose it is, when in order of issue it was made, and the lineage it was registered from
/// (empty when none was recorded: such a token spends no grant until a person moves its session).
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
struct Held {
    #[serde(flatten)]
    holder: Holder,
    #[serde(default)]
    seq: u64,
    #[serde(default)]
    lineage: Vec<Anchor>,
}

/// The tokens of this node, kept as hashes in a file only its owner reads.
pub struct Tokens {
    path: PathBuf,
    held: BTreeMap<String, Held>,
    next: u64,
    most: usize,
}

impl Tokens {
    pub fn open(path: &Path) -> Result<Tokens> {
        let held: BTreeMap<String, Held> = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("tokens unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => BTreeMap::new(),
            Err(e) => return Err(e.into()),
        };
        let next = held.values().map(|h| h.seq).max().map_or(1, |n| n + 1);
        Ok(Tokens { path: path.into(), held, next, most: MOST_TOKENS })
    }

    /// A fresh random token for ``board``/``name`` bound to ``lineage``, returned once and never stored. A name holds at most a
    /// few tokens; past them the oldest goes, except the first the name was given, which only the end of the session (or a
    /// person's `rebind`) takes away. A node holds `MOST_TOKENS` at most.
    pub fn issue(&mut self, board: &str, name: &str, lineage: &[Anchor]) -> Result<String> {
        let holder = Holder { board: board.into(), name: name.into() };
        let mut same: Vec<(u64, String)> = self.held.iter().filter(|(_, h)| h.holder == holder).map(|(k, h)| (h.seq, k.clone())).collect();
        same.sort();
        let dropped = (same.len() + 1).saturating_sub(MAX_TOKENS_PER_NAME).min(same.len().saturating_sub(1));
        if self.held.len() + 1 > self.most + dropped {
            return Err(Error::Quota("this node holds as many tokens as it will".into()));
        }
        for (_, old) in same.iter().skip(1).take(dropped) {
            self.held.remove(old);
        }
        let token = random_hex(32)?;
        self.held.insert(Tokens::key(&token), Held { holder, seq: self.next, lineage: lineage.to_vec() });
        self.next += 1;
        write_atomic(&self.path, &serde_json::to_vec(&self.held)?)?;
        Ok(token)
    }

    /// Drop every token of ``board``/``name``; how many there were.
    pub fn revoke_name(&mut self, board: &str, name: &str) -> Result<usize> {
        let before = self.held.len();
        self.held.retain(|_, h| !(h.holder.board == board && h.holder.name == name));
        let gone = before - self.held.len();
        if gone > 0 {
            write_atomic(&self.path, &serde_json::to_vec(&self.held)?)?;
        }
        Ok(gone)
    }

    /// Who ``token`` belongs to.
    pub fn resolve(&self, token: &str) -> Option<&Holder> {
        self.held.get(&Tokens::key(token)).map(|h| &h.holder)
    }

    /// What a token is kept under: its hash.
    pub fn key(token: &str) -> String {
        sha256_hex(token.as_bytes())
    }

    /// Who the token kept under ``key`` belongs to, and the lineage it was registered from.
    pub fn bound(&self, key: &str) -> Option<(&Holder, &[Anchor])> {
        self.held.get(key).map(|h| (&h.holder, h.lineage.as_slice()))
    }

    /// The lineage ``token`` was registered from; empty when none was recorded.
    pub fn lineage_of(&self, token: &str) -> Option<&[Anchor]> {
        self.bound(&Tokens::key(token)).map(|(_, lineage)| lineage)
    }

    pub fn len(&self) -> usize {
        self.held.len()
    }

    pub fn is_empty(&self) -> bool {
        self.held.is_empty()
    }

    /// Drop every token whose holder ``gone`` names; how many there were.
    pub fn revoke_where(&mut self, gone: &dyn Fn(&Holder) -> bool) -> Result<usize> {
        let before = self.held.len();
        self.held.retain(|_, h| !gone(&h.holder));
        let ended = before - self.held.len();
        if ended > 0 {
            write_atomic(&self.path, &serde_json::to_vec(&self.held)?)?;
        }
        Ok(ended)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_token_keeps_the_lineage_it_was_made_for_across_a_reopen_and_a_node_holds_a_bounded_number() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("tokens.json");
        let from = [Anchor { pid: 7, start: 9 }, Anchor { pid: 3, start: 1 }];
        let mut tokens = Tokens::open(&path).unwrap();
        tokens.most = 12;
        let first = tokens.issue("demo", "claude-aaaaaa", &from).unwrap();
        assert_eq!(tokens.lineage_of(&first), Some(&from[..]));
        let again = Tokens::open(&path).unwrap();
        assert_eq!(again.lineage_of(&first), Some(&from[..]), "the lineage outlasts a restart");
        assert_eq!(again.bound(&Tokens::key(&first)).map(|(h, _)| h.name.as_str()), Some("claude-aaaaaa"));
        for i in 1..12 {
            tokens.issue("demo", &format!("claude-{i:06}"), &[]).unwrap();
        }
        assert!(matches!(tokens.issue("demo", "claude-one-more", &[]), Err(Error::Quota(_))), "a full node issues no more tokens");
        assert_eq!(tokens.len(), 12);
    }

    #[test]
    fn the_tokens_of_idle_sessions_are_dropped_by_a_predicate_and_the_rest_stay() {
        let dir = tempfile::tempdir().unwrap();
        let mut tokens = Tokens::open(&dir.path().join("tokens.json")).unwrap();
        let (keep, drop) = (tokens.issue("demo", "claude-aaaaaa", &[]).unwrap(), tokens.issue("demo", "claude-bbbbbb", &[]).unwrap());
        assert_eq!(tokens.revoke_where(&|h| h.name == "claude-bbbbbb").unwrap(), 1);
        assert!(tokens.resolve(&keep).is_some() && tokens.resolve(&drop).is_none());
    }
}
