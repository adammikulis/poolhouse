//! Which devices are in the pool, by certificate: the record every link off this machine is
//! checked against.
//!
//! A device is its certificate; its fingerprint is the SHA-256 of the DER. A peer is served only
//! when the certificate it showed in the handshake is listed here as active; a revoked one is
//! refused at its next handshake and at its next request on a connection already open. A
//! revocation is never undone, and a revoked certificate cannot be enrolled again (a device
//! returns with a new one). A row whose fingerprint is not the hash of the certificate beside it
//! is dropped.
//!
//! This record is the legacy pool (`pool.json`). A member's word changes nothing in it: rows,
//! removals and the join policy that other members send are ignored, and only this device's own
//! local API (a grant), a join this device made and the join policy at the moment of joining
//! write to it. The join policy (`open` or `secure`) is one attribute of the pool.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::cert::{cert_fingerprint, parses, valid_fingerprint};
use crate::error::{Error, Result};
use crate::fsutil::{hex, random_hex, unhex, write_atomic};
use crate::gov::pool::GovPool;

pub const MOST_ROWS: usize = 4096;
const VERSION: u64 = 1;

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
    /// A row from the file or a peer, or None when it is not what it claims.
    pub fn read(row: &Value) -> Option<Device> {
        let mut d: Device = serde_json::from_value(row.clone()).ok()?;
        d.name = d.name.chars().take(80).collect();
        d.by = d.by.chars().take(80).collect();
        if !valid_fingerprint(&d.fingerprint) {
            return None;
        }
        if !d.cert.is_empty() {
            let der = unhex(&d.cert)?;
            if !parses(&der) || cert_fingerprint(&der) != d.fingerprint {
                return None;
            }
        }
        (d.status == Status::Revoked || !d.cert.is_empty()).then_some(d)
    }

    pub fn der(&self) -> Option<Vec<u8>> {
        unhex(&self.cert)
    }
}

/// The standing two records of one device settle on: revoked wins, and stays.
fn join(held: Option<&Device>, new: Device) -> Device {
    match held {
        None => new,
        Some(h) if new.status == Status::Revoked && h.status != Status::Revoked => new,
        Some(h) if h.status == Status::Revoked && new.status == Status::Revoked && h.cert.is_empty() && !new.cert.is_empty() => new,
        Some(h) => h.clone(),
    }
}

#[derive(Serialize, Deserialize)]
struct File {
    v: u64,
    pool: String,
    policy: Policy,
    policy_at: u64,
    policy_by: String,
    project: String,
    devices: Vec<Device>,
}

/// The pool this device belongs to and its record of the devices in it. A governed pool (`gov`) takes its members and its policy
/// from the acts it holds and nothing else; a legacy pool (no `gov`) is the `pool.json` of this device.
pub struct Pool {
    path: PathBuf,
    pub id: String,
    pub policy: Policy,
    pub policy_at: u64,
    pub policy_by: String,
    /// The project this pool belongs to (`project::project_key`), or empty for none.
    pub project: String,
    devices: BTreeMap<String, Device>,
    gov: Option<GovPool>,
}

fn governed_refusal() -> Error {
    Error::Denied("this pool is governed: its members and its policy change only by signed acts".into())
}

impl Pool {
    /// Open the pool of the state directory ``root``: the governed pool kept under `pools/` when there is one, else the legacy record.
    pub fn load(root: &Path) -> Result<Pool> {
        let mut held: Vec<PathBuf> = match std::fs::read_dir(root.join("pools")) {
            Ok(entries) => entries.filter_map(|e| e.ok()).map(|e| e.path()).filter(|p| p.join("charter.json").is_file()).collect(),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(e.into()),
        };
        match held.len() {
            0 => Pool::open(&root.join("pool.json")),
            1 => Ok(Pool::governed(root, GovPool::load(&held.remove(0))?)),
            _ => Err(Error::Damaged("this build holds one pool, and this directory holds several".into())),
        }
    }

    /// A pool governed by ``gov``, with the record this device keeps of its members read from it.
    pub fn governed(root: &Path, gov: GovPool) -> Pool {
        let mut pool = Pool {
            path: root.join("pool.json"), id: String::new(), policy: Policy::Secure, policy_at: 0, policy_by: String::new(), project: String::new(),
            devices: BTreeMap::new(), gov: Some(gov),
        };
        pool.refresh();
        pool
    }

    pub fn gov(&self) -> Option<&GovPool> {
        self.gov.as_ref()
    }

    pub fn gov_mut(&mut self) -> Option<&mut GovPool> {
        self.gov.as_mut()
    }

    /// Whether this pool is the `pool.json` of this device, which no member's word changes.
    pub fn is_legacy(&self) -> bool {
        self.gov.is_none()
    }

    /// Whether this device may join another pool: it is alone in a pool that no admin governs.
    pub fn can_join_other(&self) -> bool {
        self.gov.is_none() && self.alone()
    }

    /// Read the id, the policy, the project and the devices from the governed pool again.
    pub fn refresh(&mut self) {
        let Some(gov) = &self.gov else { return };
        let charter = &gov.state().chain.latest().charter;
        (self.id, self.policy, self.project, self.devices) = (gov.id.clone(), charter.policy, charter.project.clone(), gov.devices());
    }

    /// Open the record at ``path``, making a pool of one (no members yet) on first use.
    pub fn open(path: &Path) -> Result<Pool> {
        match std::fs::read(path) {
            Ok(bytes) => {
                let f: File = serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("pool record unreadable: {e}")))?;
                let mut devices = BTreeMap::new();
                for d in f.devices.into_iter().take(MOST_ROWS) {
                    if let Some(d) = Device::read(&serde_json::to_value(&d)?) {
                        let kept = join(devices.get(&d.fingerprint), d);
                        devices.insert(kept.fingerprint.clone(), kept);
                    }
                }
                let policy_at = f.policy_at.min(crate::poolops::wall_ms());
                Ok(Pool { path: path.into(), id: f.pool, policy: f.policy, policy_at, policy_by: f.policy_by, project: f.project, devices, gov: None })
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let pool = Pool { path: path.into(), id: random_hex(8)?, policy: Policy::Secure, policy_at: 0, policy_by: String::new(), project: String::new(), devices: BTreeMap::new(), gov: None };
                pool.save()?;
                Ok(pool)
            }
            Err(e) => Err(e.into()),
        }
    }

    fn save(&self) -> Result<()> {
        if self.gov.is_some() {
            return Err(governed_refusal());
        }
        let file = File {
            v: VERSION, pool: self.id.clone(), policy: self.policy, policy_at: self.policy_at, policy_by: self.policy_by.clone(), project: self.project.clone(),
            devices: self.devices.values().cloned().collect(),
        };
        write_atomic(&self.path, &serde_json::to_vec(&file)?)
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

    /// Let a device in. A certificate that was revoked here stays out (`Denied`).
    pub fn enrol(&mut self, der: &[u8], name: &str, by: &str, now_ms: u64) -> Result<Device> {
        if self.gov.is_some() {
            return Err(governed_refusal());
        }
        if !parses(der) {
            return Err(Error::Invalid("a device is enrolled with its certificate".into()));
        }
        let fingerprint = cert_fingerprint(der);
        match self.devices.get(&fingerprint) {
            Some(d) if d.status == Status::Revoked => {
                return Err(Error::Denied(format!("{} was put out of the pool; it needs a new certificate", &fingerprint[..12])));
            }
            Some(d) => return Ok(d.clone()),
            None => {}
        }
        let name: String = name.chars().filter(|c| !c.is_control()).take(80).collect();
        let device = Device { fingerprint: fingerprint.clone(), cert: hex(der), name, status: Status::Active, at: now_ms, by: by.chars().take(80).collect() };
        self.devices.insert(fingerprint, device.clone());
        self.save()?;
        Ok(device)
    }

    /// Put a device out: it is refused from its next handshake and its next request.
    pub fn revoke(&mut self, fingerprint: &str, by: &str, now_ms: u64) -> Result<Device> {
        if self.gov.is_some() {
            return Err(governed_refusal());
        }
        if !valid_fingerprint(fingerprint) {
            return Err(Error::Invalid("a device is removed by its fingerprint, 64 hex digits".into()));
        }
        let held = self.devices.get(fingerprint).cloned();
        if let Some(d) = held.as_ref().filter(|d| d.status == Status::Revoked) {
            return Ok(d.clone());
        }
        let device = Device {
            fingerprint: fingerprint.into(), cert: held.as_ref().map(|d| d.cert.clone()).unwrap_or_default(),
            name: held.map(|d| d.name).unwrap_or_default(), status: Status::Revoked, at: now_ms, by: by.chars().take(80).collect(),
        };
        self.devices.insert(fingerprint.into(), device.clone());
        self.save()?;
        Ok(device)
    }

    pub fn export(&self) -> Vec<Value> {
        self.devices().iter().filter_map(|d| serde_json::to_value(d).ok()).collect()
    }

    /// Take the rows of the device this one has just joined, which the join itself authenticated (the join code's exchange, or the
    /// pinned certificate of an `open` pool). Rows a member sends in an ordinary exchange never come here.
    fn absorb(&mut self, rows: &[Value], by: &str) -> Result<usize> {
        let mut altered = 0;
        for row in rows.iter().take(MOST_ROWS) {
            let Some(mut heard) = Device::read(row) else { continue };
            let kept = self.devices.get(&heard.fingerprint);
            if heard.cert.is_empty() {
                heard.cert = kept.map(|d| d.cert.clone()).unwrap_or_default();
            }
            if heard.name.is_empty() {
                heard.name = kept.map(|d| d.name.clone()).unwrap_or_default();
            }
            if heard.by.is_empty() {
                heard.by = by.chars().take(80).collect();
            }
            let new = join(kept, heard);
            if kept != Some(&new) {
                self.devices.insert(new.fingerprint.clone(), new);
                altered += 1;
            }
        }
        if altered > 0 {
            self.save()?;
        }
        Ok(altered)
    }

    /// Set the join policy; false (and no change) when ``at`` is not newer than the standing one.
    pub fn set_policy(&mut self, policy: Policy, at: u64, by: &str) -> Result<bool> {
        if self.gov.is_some() {
            return Err(Error::Invalid("a pool's join policy never changes in place; reissue the pool to change it".into()));
        }
        if (at, by) <= (self.policy_at, self.policy_by.as_str()) {
            return Ok(false);
        }
        let changed = policy != self.policy;
        (self.policy, self.policy_at, self.policy_by) = (policy, at, by.chars().take(80).collect());
        self.save()?;
        Ok(changed)
    }

    /// Make this pool one for ``project`` (the node does this at start, before it has heard of
    /// another pool of that project). Only a device alone in its pool may.
    pub fn claim_project(&mut self, project: &str) -> Result<()> {
        if self.gov.is_some() {
            return Err(governed_refusal());
        }
        if self.project == project {
            return Ok(());
        }
        if !self.alone() {
            return Err(Error::Denied("a pool that has members does not change project".into()));
        }
        self.project = project.into();
        self.save()
    }

    /// Take the pool of the device ``host`` this device has joined, the join having been ``by`` (`join-code` or `open`). A device alone
    /// in its pool takes the pool's id, its rows and its policy; a device that has members takes only the row of ``host``, inside the
    /// pool it is already in. The row of ``host`` says how it came in as this device met it.
    pub fn adopt(&mut self, id: &str, rows: &[Value], policy: Option<(Policy, u64, &str)>, joined: (&str, &str)) -> Result<()> {
        let (host, by) = joined;
        if self.gov.is_some() {
            return Err(governed_refusal());
        }
        if id != self.id && (!self.alone() || id.len() != 16 || !id.bytes().all(|b| b.is_ascii_hexdigit())) {
            return Err(Error::Denied("a device that already has members does not change pool".into()));
        }
        let alone = self.alone();
        self.id = id.into();
        let is_host = |r: &Value| r.get("fingerprint").and_then(Value::as_str) == Some(host);
        let mut taken: Vec<Value> = rows.iter().filter(|r| alone || is_host(r)).cloned().collect();
        taken.iter_mut().filter(|r| is_host(r)).filter_map(Value::as_object_mut).for_each(|r| drop(r.insert("by".into(), by.into())));
        self.absorb(&taken, host)?;
        if let (true, Some((policy, at, by))) = (alone, policy) {
            self.set_policy(policy, at.min(crate::poolops::wall_ms()), by)?;
        }
        self.save()
    }
}
