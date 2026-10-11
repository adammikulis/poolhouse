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

/// What a device that finds a `pool.json` of the old format says once: there is no migration, the device makes a new pool.
pub const OLD_FORMAT: &str = "this device was in an old-format pool: it now starts unfounded; create a new pool";

/// The name an old-format `pool.json` is renamed to: `pool.json.old-format`, then `.old-format.1`, `.2` and so on.
const SET_ASIDE: &str = "pool.json.old-format";

/// Rename the `pool.json` of ``root`` (whatever it holds: it is never read) out of the way, never deleting it, and say so once; the
/// name it now has, or empty when there was none.
fn set_aside_old_format(root: &Path) -> Result<String> {
    let old = root.join("pool.json");
    if !old.exists() {
        return Ok(String::new());
    }
    let name = std::iter::once(SET_ASIDE.to_string()).chain((1..).map(|n| format!("{SET_ASIDE}.{n}"))).find(|n| !root.join(n).exists()).expect("a free name");
    std::fs::rename(&old, root.join(&name))?;
    eprintln!("{OLD_FORMAT}");
    Ok(name)
}

/// How a new device gets in.
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Policy {
    /// A device on the same network segment is enrolled automatically.
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
    /// The name an old-format `pool.json` was renamed to at this start, or empty.
    pub retired: String,
}

impl Pool {
    /// Open the pool of the state directory ``root``: the governed pool kept under `pools/`, or the unfounded pool of one when there
    /// is none. A `pool.json` of the old format is renamed aside and the device starts as it would fresh (`OLD_FORMAT`).
    pub fn load(root: &Path) -> Result<Pool> {
        let mut held: Vec<PathBuf> = match std::fs::read_dir(root.join("pools")) {
            Ok(entries) => entries.filter_map(|e| e.ok()).map(|e| e.path()).filter(|p| p.join("charter.json").is_file()).collect(),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(e.into()),
        };
        let retired = set_aside_old_format(root)?;
        match held.len() {
            0 => Ok(Pool { id: String::new(), policy: Policy::Secure, project: String::new(), devices: BTreeMap::new(), gov: None, retired }),
            1 => Ok(Pool { retired, ..Pool::governed(GovPool::load(&held.remove(0))?) }),
            _ => Err(Error::Damaged("this build holds one pool, and this directory holds several".into())),
        }
    }

    /// A pool governed by ``gov``, with the record this device keeps of its members read from it.
    pub fn governed(gov: GovPool) -> Pool {
        let mut pool = Pool { id: String::new(), policy: Policy::Secure, project: String::new(), devices: BTreeMap::new(), gov: Some(gov), retired: String::new() };
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
        let name: String = name.chars().filter(|c| !c.is_control()).take(80).collect();
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
