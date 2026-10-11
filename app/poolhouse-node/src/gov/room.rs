//! What a pool makes room for: how many acts of each kind it holds, and which go first when a member sends more.
//!
//! The acts a member can send in bulk are the ones a device signs: an admit that stands nowhere (past a rate, or by a device that
//! may not admit) and a leave. A device may have 32 of the first and 8 of the second held at once and replaces its own oldest; the
//! pool holds at most 1,024 admits of the first kind and, when it holds as many acts as it will, drops the oldest of them for an act
//! that arrives. An act an admin signed never goes for a device's. Admits count toward the 4,096 only while they stand.

use std::collections::{BTreeMap, BTreeSet};

use super::act::Act;
use super::pool::GovPool;
use super::{ADMIN_ACTS_PER_KEYS, MAX_ACTS, MAX_ADMIN_ACTS};

/// Admits signed by a device that stand nowhere, held at once, and how many of one device's and of its leaves.
pub const MOST_JUNK: usize = 1024;
pub const MOST_JUNK_PER_DEVICE: usize = 32;
pub const MOST_LEAVES_PER_DEVICE: usize = 8;

type Key = (i64, String);

/// An index of the device-signed acts that hold no one in, oldest first by the time this device first saw them.
#[derive(Default)]
pub struct Junk {
    built: bool,
    all: BTreeSet<Key>,
    by_device: BTreeMap<String, BTreeSet<Key>>,
    leaves: BTreeMap<String, BTreeSet<Key>>,
}

impl Junk {
    pub fn forget(&mut self) {
        *self = Junk::default();
    }

    fn add(&mut self, key: &Key, act: &Act) {
        let devices = act.device_keys();
        if devices.is_empty() {
            return;
        }
        if act.kind != "leave" {
            self.all.insert(key.clone());
        }
        let table = if act.kind == "leave" { &mut self.leaves } else { &mut self.by_device };
        for d in devices {
            table.entry(d.to_string()).or_default().insert(key.clone());
        }
    }

    fn remove(&mut self, key: &Key, act: &Act) {
        self.all.remove(key);
        for table in [&mut self.by_device, &mut self.leaves] {
            for d in act.device_keys() {
                if let Some(set) = table.get_mut(d) {
                    set.remove(key);
                }
            }
        }
    }

    fn oldest_of(&self, device: &str, leaves: bool, most: usize) -> Option<Key> {
        let set = if leaves { self.leaves.get(device) } else { self.by_device.get(device) };
        set.filter(|s| s.len() >= most).and_then(|s| s.iter().next().cloned())
    }
}

impl GovPool {
    fn index_junk(&mut self) {
        if self.junk.built {
            return;
        }
        let mut junk = Junk { built: true, ..Junk::default() };
        for (id, act) in self.acts.iter().filter(|(id, _)| !self.state.roster.standing.contains(*id)) {
            junk.add(&(self.seen.get(id).copied().unwrap_or(0), id.clone()), act);
        }
        self.junk = junk;
    }

    /// Note that ``act``, just accepted under ``id``, is held.
    pub(super) fn note_held(&mut self, id: &str, act: &Act) {
        if act.kind == "admit" {
            self.admits_live += 1;
        }
        if self.junk.built {
            self.junk.add(&(self.seen.get(id).copied().unwrap_or(0), id.to_string()), act);
        }
    }

    /// Drop the act ``id`` and what is noted of it.
    pub(super) fn forget(&mut self, id: &str) {
        let key = (self.seen.get(id).copied().unwrap_or(0), id.to_string());
        if let Some(act) = self.acts.remove(id) {
            let void = self.state.roster.void.contains_key(id) || self.state.roster.short.contains_key(id);
            if act.kind == "admit" && !void {
                self.admits_live = self.admits_live.saturating_sub(1);
            }
            self.junk.remove(&key, &act);
        }
        self.seen.remove(id);
        self.ready.remove(id);
        self.dirty.acts = true;
    }

    fn drop_oldest_junk(&mut self) -> bool {
        let Some((_, id)) = self.junk.all.iter().next().cloned() else { return false };
        self.forget(&id);
        true
    }

    /// Make room for ``act``, which has passed the checks of what it is, or say why there is none.
    pub(super) fn room_for(&mut self, act: &Act) -> std::result::Result<(), String> {
        self.index_junk();
        let leave = act.kind == "leave";
        for device in act.device_keys().into_iter().map(str::to_string).collect::<Vec<_>>() {
            let most = if leave { MOST_LEAVES_PER_DEVICE } else { MOST_JUNK_PER_DEVICE };
            while let Some((_, id)) = self.junk.oldest_of(&device, leave, most) {
                self.forget(&id);
            }
        }
        while !leave && !act.device_keys().is_empty() && self.junk.all.len() >= MOST_JUNK && self.drop_oldest_junk() {}
        if self.acts.len() >= MAX_ACTS && !self.drop_oldest_junk() {
            return Err("this pool holds as many acts as it will".into());
        }
        if act.kind == "admit" && !super::admit_fits(!act.admin_keys().is_empty(), self.admits_live) {
            return Err("this pool holds as many admits as it will".into());
        }
        if act.is_chain() {
            self.room_for_admin_act(act)?;
        }
        Ok(())
    }

    /// The admin-set acts not yet in effect are at most 256, and the same keys may have signed at most 16 of them: a key that
    /// floods the pool with acts replaces its own oldest, and never the acts of other keys.
    fn room_for_admin_act(&mut self, act: &Act) -> std::result::Result<(), String> {
        let keys = act.admin_keys();
        let live = |pool: &GovPool| -> Vec<(i64, String, bool)> {
            pool.acts.iter().filter(|(id, a)| a.is_chain() && !pool.state.chain.applied.contains(*id))
                .map(|(id, a)| (pool.seen.get(id).copied().unwrap_or(0), id.clone(), a.admin_keys().iter().any(|k| keys.contains(k)))).collect()
        };
        let held = live(self);
        if held.len() >= MAX_ADMIN_ACTS {
            return Err("this pool holds as many acts that change its admins as it will".into());
        }
        let mine: Vec<&(i64, String, bool)> = held.iter().filter(|(_, _, shared)| *shared).collect();
        if mine.len() >= ADMIN_ACTS_PER_KEYS {
            let oldest = mine.iter().min().map(|(_, id, _)| id.clone());
            oldest.into_iter().for_each(|id| self.forget(&id));
        }
        Ok(())
    }
}
