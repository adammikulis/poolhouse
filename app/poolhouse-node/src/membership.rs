//! Which devices are in the pool, by certificate: the record every link off this machine is
//! checked against.
//!
//! A device is its certificate; its fingerprint is the SHA-256 of the DER. A peer is served only
//! when the certificate it showed in the handshake is listed here as active; a revoked one is
//! refused at its next handshake and at its next request on a connection already open. A
//! revocation is never undone, and a revoked certificate cannot be enrolled again (a device
//! returns with a new one).
//!
//! A device is in one of two states. Unfounded (a fresh device): a pool of one that has no charter, no id and no admins, whose only
//! member is the device itself, kept in memory and never written. Governed: the members, the policy and the project come from the
//! acts of the pool's charter and from nothing else. There is no third state: a `pool.json` of the old format is refused at start.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::cert::cert_fingerprint;
use crate::error::{Error, Result};
use crate::fsutil::{hex, unhex};
use crate::gov::pool::GovPool;

/// The directories under `pools/` that hold a pool. A directory that holds some of a pool's files but not its charter is damage, never
/// the absence of a pool.
fn held_pools(root: &Path) -> Result<Vec<PathBuf>> {
    let entries = match std::fs::read_dir(root.join("pools")) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e.into()),
    };
    let mut held = Vec::new();
    for path in entries.filter_map(|e| e.ok()).map(|e| e.path()) {
        if path.join("charter.json").is_file() {
            held.push(path);
        } else if crate::gov::pool::POOL_FILES.iter().any(|f| path.join(f).exists()) {
            return Err(Error::Damaged(format!("the pool in {} lost its charter", path.display())));
        }
    }
    Ok(held)
}

/// How a new device gets in.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Policy {
    /// A device on the same network segment that asks to join is enrolled without a code.
    Open,
    /// A device is enrolled only through a join code both people know.
    Secure,
}

impl Policy {
    pub fn parse(text: &str) -> Result<Policy> {
        match text {
            "open" => Ok(Policy::Open),
            "secure" => Ok(Policy::Secure),
            _ => Err(Error::Invalid("the join policy is open or secure".into())),
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Policy::Open => "open",
            Policy::Secure => "secure",
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Active,
    Revoked,
    /// The device left the pool by its own act; an admit may bring it back.
    Left,
}

/// What the record says of a fingerprint.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Standing {
    Active,
    Revoked,
    Unknown,
}

/// Whether ``c`` is left out of a name wherever it is shown: a control, a quote (with which a name could close the quotes a sentence puts
/// around it), or a mark that reorders the text after it (bidirectional controls, line separators).
fn never_shown(c: char) -> bool {
    c.is_control() || matches!(c, '\'' | '"' | '`' | '\u{b4}' | '\u{ab}' | '\u{bb}' | '\u{2018}'..='\u{201f}' | '\u{2032}'..='\u{2037}' | '\u{2039}' | '\u{203a}'
        | '\u{300c}'..='\u{300f}' | '\u{301d}'..='\u{301f}' | '\u{ff02}' | '\u{ff07}' | '\u{ff40}' | '\u{61c}' | '\u{200b}' | '\u{200e}' | '\u{200f}'
        | '\u{2028}'..='\u{202e}' | '\u{2066}'..='\u{206f}' | '\u{feff}')
}

/// A name a device or a person chose, as a list or a refusal shows it: at most ``most`` characters, none of them `never_shown`.
pub fn shown_name(name: &str, most: usize) -> String {
    name.chars().filter(|c| !never_shown(*c)).take(most).collect::<String>().trim().to_string()
}

/// The plain letters of an accented Latin one; None for any other character.
fn plain_letters(c: char) -> Option<&'static str> {
    const LETTERS: [(&str, &str); 38] = [
        ("àáâãäåāăą", "a"), ("ÀÁÂÃÄÅĀĂĄ", "A"), ("çćĉċč", "c"), ("ÇĆĈĊČ", "C"), ("ďđð", "d"), ("ĎĐÐ", "D"), ("èéêëēĕėęě", "e"), ("ÈÉÊËĒĔĖĘĚ", "E"),
        ("ĝğġģ", "g"), ("ĜĞĠĢ", "G"), ("ĥħ", "h"), ("ĤĦ", "H"), ("ìíîïĩīĭįı", "i"), ("ÌÍÎÏĨĪĬĮİ", "I"), ("ñńņň", "n"), ("ÑŃŅŇ", "N"), ("òóôõöøōŏő", "o"),
        ("ÒÓÔÕÖØŌŎŐ", "O"), ("ŕŗř", "r"), ("ŔŖŘ", "R"), ("śŝşšș", "s"), ("ŚŜŞŠȘ", "S"), ("ţťŧț", "t"), ("ŢŤŦȚ", "T"), ("ùúûüũūŭůűų", "u"),
        ("ÙÚÛÜŨŪŬŮŰŲ", "U"), ("ýÿŷ", "y"), ("ÝŸŶ", "Y"), ("źżž", "z"), ("ŹŻŽ", "Z"), ("ł", "l"), ("Ł", "L"), ("ß", "ss"), ("æ", "ae"), ("Æ", "AE"),
        ("œ", "oe"), ("Œ", "OE"), ("þ", "th"),
    ];
    LETTERS.iter().find(|(many, _)| many.contains(c)).map(|(_, plain)| *plain)
}

/// A device's name inside a text the node writes for a person to sign, which is printable ASCII: accented letters lose their accents, and
/// anything but letters, digits, spaces, dots, dashes and underscores is left out, so a name adds no words of its own to the sentence. A
/// name with no letter or digit left is the start of the device's fingerprint.
pub fn sentence_name(name: &str, fingerprint: &str) -> String {
    let mut out = String::new();
    for c in name.chars() {
        match plain_letters(c) {
            Some(plain) => out.push_str(plain),
            None if c.is_ascii_alphanumeric() || matches!(c, ' ' | '.' | '-' | '_') => out.push(c),
            None => {}
        }
    }
    if out.chars().any(|c| c.is_ascii_alphanumeric()) {
        out.trim().to_string()
    } else {
        fingerprint.chars().take(12).collect()
    }
}

/// One device's standing in the pool.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
pub struct Device {
    pub fingerprint: String,
    /// The certificate as hex DER; empty on a revocation recorded from a fingerprint alone.
    #[serde(default)]
    pub cert: String,
    #[serde(default)]
    pub name: String,
    pub status: Status,
    /// Milliseconds since the epoch.
    #[serde(default)]
    pub at: u64,
    /// The fingerprint of the device that recorded this, or why (`join-code`, `open`, `self`).
    #[serde(default)]
    pub by: String,
}

impl Device {
    pub fn der(&self) -> Option<Vec<u8>> {
        unhex(&self.cert)
    }
}

/// The pool this device belongs to and its record of the devices in it: unfounded (`gov` is None, the device alone) or governed
/// by the acts of `gov`, which are the only source of its members, its policy and its project.
pub struct Pool {
    pub id: String,
    pub policy: Policy,
    /// The project this pool belongs to (`project::project_key`), or empty for none.
    pub project: String,
    devices: BTreeMap<String, Device>,
    gov: Option<GovPool>,
}

impl Pool {
    /// Open the pool of the state directory ``root``: the governed pool kept under `pools/`, or the unfounded pool of one when there
    /// is none. A pool this build cannot read stops the node.
    pub fn load(root: &Path) -> Result<Pool> {
        let mut held = held_pools(root)?;
        match held.len() {
            0 => Ok(Pool { id: String::new(), policy: Policy::Secure, project: String::new(), devices: BTreeMap::new(), gov: None }),
            1 => Ok(Pool::governed(GovPool::load(&held.remove(0))?)),
            _ => Err(Error::Damaged("this build holds one pool, and this directory holds several".into())),
        }
    }

    /// A pool governed by ``gov``, with the record this device keeps of its members read from it.
    pub fn governed(gov: GovPool) -> Pool {
        let mut pool = Pool { id: String::new(), policy: Policy::Secure, project: String::new(), devices: BTreeMap::new(), gov: Some(gov) };
        pool.refresh();
        pool
    }

    pub fn gov(&self) -> Option<&GovPool> {
        self.gov.as_ref()
    }

    pub fn gov_mut(&mut self) -> Option<&mut GovPool> {
        self.gov.as_mut()
    }

    /// Whether no pool has been founded by this device or joined by it.
    pub fn is_unfounded(&self) -> bool {
        self.gov.is_none()
    }

    /// Whether this device may join another pool: it is in none yet.
    pub fn can_join_other(&self) -> bool {
        self.gov.is_none()
    }

    /// Read the id, the policy, the project and the devices from the governed pool again.
    pub fn refresh(&mut self) {
        let Some(gov) = &self.gov else { return };
        let charter = &gov.state().chain.latest().charter;
        (self.id, self.policy, self.project, self.devices) = (gov.id.clone(), charter.policy, charter.project.clone(), gov.devices());
    }

    /// List this device itself as the only member of an unfounded pool (in memory; nothing is written).
    pub fn list_self(&mut self, der: &[u8], name: &str, now_ms: u64) {
        if self.gov.is_some() {
            return;
        }
        let fingerprint = cert_fingerprint(der);
        let name = shown_name(name, 80);
        self.devices.insert(fingerprint.clone(), Device { fingerprint, cert: hex(der), name, status: Status::Active, at: now_ms, by: "self".into() });
    }

    /// Name the project of an unfounded device, for the pools it may join.
    pub fn claim_project(&mut self, project: &str) {
        if self.gov.is_none() {
            self.project = project.into();
        }
    }

    pub fn devices(&self) -> Vec<Device> {
        let mut all: Vec<Device> = self.devices.values().cloned().collect();
        all.sort_by(|a, b| (&a.name, &a.fingerprint).cmp(&(&b.name, &b.fingerprint)));
        all
    }

    pub fn get(&self, fingerprint: &str) -> Option<&Device> {
        self.devices.get(fingerprint)
    }

    pub fn standing(&self, fingerprint: &str) -> Standing {
        match self.devices.get(fingerprint).map(|d| d.status) {
            Some(Status::Active) => Standing::Active,
            Some(Status::Revoked) => Standing::Revoked,
            Some(Status::Left) | None => Standing::Unknown,
        }
    }

    pub fn is_active(&self, fingerprint: &str) -> bool {
        self.standing(fingerprint) == Standing::Active
    }

    pub fn active(&self) -> Vec<Device> {
        self.devices().into_iter().filter(|d| d.status == Status::Active).collect()
    }

    /// Whether no device but one is active: the pool is one device (or none yet).
    pub fn alone(&self) -> bool {
        self.devices.values().filter(|d| d.status == Status::Active).count() <= 1
    }
}
