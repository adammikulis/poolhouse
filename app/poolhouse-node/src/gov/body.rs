//! The body of each kind of act, read strictly: a field that is not named here refuses the act.

use serde::{Deserialize, Serialize};
use serde::de::DeserializeOwned;

use crate::cert::{cert_fingerprint, parses, valid_fingerprint};
use crate::error::{Error, Result};
use crate::fsutil::unhex;
use crate::row::{valid_name, valid_origin};

use super::act::{valid_id, Act};
use super::charter::{Charter, Mode, Unit};
use super::keys::{valid_public, AdminKey};

/// How a device came in: `found` (the first device of a pool), `join-code`, `open`, `tap`, `delegate`, `handoff`.
pub const HOW: [&str; 6] = ["found", "join-code", "open", "tap", "delegate", "handoff"];
const MOST_CUT: usize = 256;
/// The most logs one removal names.
pub const MOST_LOG_CUTS: usize = 256;

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Admit {
    /// The device's certificate, hex DER.
    pub cert: String,
    #[serde(default)]
    pub name: String,
    pub how: String,
    /// `segment` when an open pool's member checked that the device is on its network segment.
    #[serde(default)]
    pub via: String,
}

/// A device named by the fingerprint of its certificate.
#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Device {
    pub fingerprint: String,
}

/// How much of one log of a board stands: the board, the origin that names the log, its first ``seq`` entries and the hash of the last
/// of them (empty when the log holds none).
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct LogCut {
    pub board: String,
    pub origin: String,
    pub seq: u64,
    #[serde(default)]
    pub hash: String,
}

/// The removal of a device: its fingerprint, and for each log of it that the removing admin holds, how much of it stands.
#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Removal {
    pub fingerprint: String,
    #[serde(default)]
    pub cut: Vec<LogCut>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Add {
    pub entry: AdminKey,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Dismiss {
    pub key: String,
    /// The ids of every act by that key the removing admins have seen.
    #[serde(default)]
    pub cut: Vec<String>,
}

/// Another act, named by its id.
#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Target {
    pub act: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Thresholds {
    pub routine: Option<u32>,
    pub sensitive: Option<u32>,
    pub unit: Option<Unit>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Holds {
    pub admin_add_h: Option<u64>,
    pub admin_remove_h: Option<u64>,
    pub handoff_min: Option<u64>,
    pub dispute_d: Option<u64>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct SetMode {
    pub mode: Mode,
}

/// The body of an act, by kind.
#[derive(Clone, Debug)]
pub enum Body {
    Found(Box<Charter>),
    Admit(Admit),
    Remove(Removal),
    Leave(Device),
    AdminAdd(Add),
    AdminRemove(Dismiss),
    Veto(Target),
    Cancel(Target),
    SetThreshold(Thresholds),
    SetHolds(Holds),
    SetMode(SetMode),
    Delegate(Device),
    DelegateEnd(Device),
}

fn bad<T>(why: &str) -> Result<T> {
    Err(Error::Invalid(why.into()))
}

fn read<T: DeserializeOwned>(act: &Act) -> Result<T> {
    serde_json::from_value(act.body.clone()).map_err(|e| Error::Invalid(format!("the body of a {} act is not well formed: {}", act.kind, e.to_string().chars().take(80).collect::<String>())))
}

/// The certificate of an admit, as DER: it parses, and its fingerprint is what the device is known by.
pub fn cert_of(admit: &Admit) -> Result<(Vec<u8>, String)> {
    let der = unhex(&admit.cert).filter(|d| parses(d)).ok_or_else(|| Error::Invalid("an admit carries the device's certificate".into()))?;
    let fingerprint = cert_fingerprint(&der);
    Ok((der, fingerprint))
}

fn check_admit(a: &Admit) -> Result<()> {
    cert_of(a)?;
    if !HOW.contains(&a.how.as_str()) || !(a.via.is_empty() || a.via == "segment") || a.name.chars().count() > 80 {
        return bad("an admit says how the device came in, and by `segment` only when it did");
    }
    Ok(())
}

fn check_device(d: &Device) -> Result<()> {
    if valid_fingerprint(&d.fingerprint) { Ok(()) } else { bad("a device is named by its fingerprint, 64 hex digits") }
}

fn check_removal(r: &Removal) -> Result<()> {
    if !valid_fingerprint(&r.fingerprint) || r.cut.len() > MOST_LOG_CUTS || !r.cut.iter().all(|c| valid_name(&c.board) && valid_origin(&c.origin) && (valid_id(&c.hash) || (c.seq == 0 && c.hash.is_empty()))) {
        return bad("a device is removed by its fingerprint, with the logs of it that were seen named by board and origin");
    }
    Ok(())
}

fn check_target(t: &Target) -> Result<()> {
    if valid_id(&t.act) { Ok(()) } else { bad("an act is named by its id, 64 hex digits") }
}

fn check_drop(d: &Dismiss) -> Result<()> {
    if !valid_public(&d.key) || d.cut.len() > MOST_CUT || !d.cut.iter().all(|c| valid_id(c)) {
        return bad("an admin is removed by its key, with the ids of the acts it signed that were seen");
    }
    Ok(())
}

/// The body of ``act``, read strictly for its kind.
pub fn parse(act: &Act) -> Result<Body> {
    Ok(match act.kind.as_str() {
        "found" => Body::Found(Box::new(read(act)?)),
        "admit" => {
            let a: Admit = read(act)?;
            check_admit(&a)?;
            Body::Admit(a)
        }
        "remove" => {
            let r: Removal = read(act)?;
            check_removal(&r)?;
            Body::Remove(r)
        }
        "leave" | "delegate" | "delegate_end" => {
            let d: Device = read(act)?;
            check_device(&d)?;
            match act.kind.as_str() {
                "leave" => Body::Leave(d),
                "delegate" => Body::Delegate(d),
                _ => Body::DelegateEnd(d),
            }
        }
        "admin_add" => {
            let a: Add = read(act)?;
            a.entry.check()?;
            Body::AdminAdd(a)
        }
        "admin_remove" => {
            let d: Dismiss = read(act)?;
            check_drop(&d)?;
            Body::AdminRemove(d)
        }
        "veto" | "cancel" => {
            let t: Target = read(act)?;
            check_target(&t)?;
            if act.kind == "veto" { Body::Veto(t) } else { Body::Cancel(t) }
        }
        "set_threshold" => {
            let t: Thresholds = read(act)?;
            if t.routine.is_none() && t.sensitive.is_none() && t.unit.is_none() {
                return bad("a threshold change names a threshold");
            }
            Body::SetThreshold(t)
        }
        "set_holds" => {
            let h: Holds = read(act)?;
            if h.admin_add_h.is_none() && h.admin_remove_h.is_none() && h.handoff_min.is_none() && h.dispute_d.is_none() {
                return bad("a hold change names a hold");
            }
            Body::SetHolds(h)
        }
        "set_mode" => Body::SetMode(read(act)?),
        _ => return bad("this build does not know that kind of act"),
    })
}
