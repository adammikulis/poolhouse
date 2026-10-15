use std::collections::BTreeMap;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, write_atomic};
use super::{bad, Binding, GovPool};

const MARKER: &str = "phone-checkpoint.json";
const FILES: [&str; 4] = ["charter.json", "acts.json", "seen.json", "refused.json"];
const MAX_FILE: u64 = (super::MAX_ACTS * super::MAX_ACT_BYTES) as u64;

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(super) struct Checkpoint {
    version: u32,
    pub binding: Binding,
    generation: u64,
    epoch: u64,
    head: String,
    files: BTreeMap<String, String>,
    clock: Option<super::clock::Stamp>,
}

fn bytes(path: &Path) -> Result<Vec<u8>> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > MAX_FILE {
        return bad("The phone's pool checkpoint is not a bounded private file.");
    }
    Ok(std::fs::read(path)?)
}

impl Checkpoint {
    pub fn pin(root: &Path, binding: Binding) -> Result<()> {
        if root.join(MARKER).exists() {
            Self::read(root, &binding)?;
            return Ok(());
        }
        if std::fs::read_dir(root)?.next().is_some() {
            return bad("The phone's pool checkpoint is missing. This key cannot be rebound.");
        }
        let marker = Self { version: 2, binding, generation: 0, epoch: 0, head: String::new(), files: BTreeMap::new(), clock: None };
        write_atomic(&root.join(MARKER), &serde_json::to_vec(&marker)?)
    }

    pub fn read(root: &Path, binding: &Binding) -> Result<Self> {
        let marker: Self = serde_json::from_slice(&bytes(&root.join(MARKER))?)?;
        if marker.version != 2 || &marker.binding != binding || (marker.epoch == 0 && (!marker.files.is_empty() || marker.generation != 0 || !marker.head.is_empty() || marker.clock.is_some())) {
            return bad("The phone's pool checkpoint does not match this key and computer.");
        }
        if marker.epoch > 0 && (marker.generation == 0 || !super::super::act::valid_id(&marker.head) || marker.files.len() != FILES.len()) {
            return bad("The phone's pool checkpoint is incomplete.");
        }
        Ok(marker)
    }

    pub fn check_current(&self, root: &Path) -> Result<()> {
        if Self::read(root, &self.binding)? != *self {
            return bad("The phone's pool checkpoint changed while signing.");
        }
        self.verify_files(root)
    }

    fn pool_dir(&self, root: &Path) -> std::path::PathBuf {
        root.join("pools").join(&self.binding.anchor[..32])
    }

    fn verify_files(&self, root: &Path) -> Result<()> {
        if self.epoch == 0 {
            return Ok(());
        }
        let dir = self.pool_dir(root);
        for name in FILES {
            let data = bytes(&dir.join(name))?;
            if self.files.get(name) != Some(&sha256_hex(&data)) {
                return bad("The phone's pool checkpoint is missing or damaged.");
            }
            let value: Value = serde_json::from_slice(&data)?;
            if name == "seen.json" {
                let time = value["awake_ms"].as_i64().filter(|n| *n >= 0).ok_or_else(|| Error::Damaged("The phone's awake checkpoint is damaged.".into()))?;
                if !value["joined_ms"].is_null() {
                    return bad("A phone checkpoint cannot contain supplied settled times.");
                }
                for field in ["seen", "ready"] {
                    let Some(map) = value[field].as_object() else { return bad("The phone's observation checkpoint is damaged.") };
                    if map.len() > super::MAX_ACTS || map.iter().any(|(id, v)| !super::super::act::valid_id(id) || v.as_i64().is_none_or(|n| n < 0 || n > time)) {
                        return bad("The phone's observation checkpoint is damaged.");
                    }
                }
            }
            if name == "acts.json" {
                let acts: Vec<super::Act> = serde_json::from_value(value["acts"].clone())?;
                if acts.len() > super::MAX_ACTS || acts.iter().any(|a| a.check_shape().is_err()) {
                    return bad("The phone's retained governance evidence is damaged.");
                }
            }
        }
        Ok(())
    }

    pub fn load(&self, root: &Path, clock: Option<&super::clock::LocalClock>) -> Result<Option<GovPool>> {
        self.verify_files(root)?;
        if self.epoch == 0 {
            if root.join("pools").exists() {
                return bad("The phone's initial governance checkpoint was interrupted.");
            }
            return Ok(None);
        }
        let dir = self.pool_dir(root);
        let base = match (clock, &self.clock) {
            (None, None) => None,
            (Some(clock), Some(previous)) => {
                let earned: Value = serde_json::from_slice(&bytes(&dir.join("seen.json"))?)?;
                let elapsed = clock.sample()?.earned_since(previous)?;
                let base = earned["awake_ms"].as_i64().and_then(|n| n.checked_add(elapsed));
                Some(base.ok_or_else(|| Error::Damaged("The phone's awake checkpoint cannot be restored.".into()))?)
            }
            _ => return bad("The phone's awake checkpoint does not match this installation."),
        };
        let pool = GovPool::load_with_awake_base(&dir, base)?;
        let (epoch, head) = pool.context();
        let retained = pool.state().chain.epochs.iter().any(|state| state.n == self.epoch && state.hash == self.head);
        if epoch < self.epoch || (epoch == self.epoch && head != self.head) || !retained || pool.genesis.anchor() != self.binding.anchor {
            return bad("The phone's retained governance head does not match its checkpoint.");
        }
        Ok(Some(pool))
    }

    pub fn commit(&mut self, root: &Path, pool: &mut GovPool, clock: Option<&super::clock::LocalClock>) -> Result<()> {
        let (epoch, head) = pool.context();
        if epoch < self.epoch || (epoch == self.epoch && !self.head.is_empty() && head != self.head) {
            return bad("The pool evidence conflicts with a head this phone has already verified.");
        }
        pool.save_observation_checkpoint()?;
        let mut next = self.clone();
        next.generation = next.generation.checked_add(1).ok_or_else(|| Error::Damaged("The phone checkpoint is exhausted.".into()))?;
        next.epoch = epoch;
        next.head = head;
        next.clock = clock.map(super::clock::LocalClock::sample).transpose()?;
        if let (Some(now), Some(previous)) = (&next.clock, &self.clock) { now.earned_since(previous)?; }
        next.files = FILES.into_iter().map(|name| Ok((name.to_string(), sha256_hex(&bytes(&pool.dir().join(name))?)))).collect::<Result<_>>()?;
        write_atomic(&root.join(MARKER), &serde_json::to_vec(&next)?)?;
        *self = next;
        Ok(())
    }
}
