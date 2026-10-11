//! The members of a pool, folded from the acts: who was admitted, removed or left, and which devices are delegates.
//!
//! The roster is a view. It is built from scratch from the acts held and the chain (`chain`), so no row of it is ever believed on
//! the word of the member that sent it. A removal is a union and is never undone; an admit of a removed certificate is refused;
//! a `leave` is undone by a later `admit`. A delegate's admits and an open pool's member admits are counted by the order this
//! device first saw them, and those past the rate are refused.

use std::collections::{BTreeMap, BTreeSet};

use crate::fsutil::hex;
use crate::membership::{Device, Policy, Status};

use super::act::Act;
use super::body::{self, Admit, Body};
use super::chain::{Chain, Seen};
use super::charter::Class;
use super::{DAY_MS, DELEGATE_DAYS, DELEGATE_PER_DAY, DELEGATE_PER_HOUR, HOUR_MS, OPEN_PER_HOUR, SETTLED};

/// A device an admin act lets sign `admit`.
#[derive(Clone, Debug)]
pub struct Delegate {
    pub device: String,
    pub ends_ms: i64,
}

#[derive(Default, Debug)]
pub struct Roster {
    pub devices: BTreeMap<String, Device>,
    /// Live delegates by device fingerprint.
    pub delegates: BTreeMap<String, Delegate>,
    /// For each device, the spans of awake time (first sight of the act that made it a delegate until it ended or ran out) in which
    /// an admit it signed counts.
    windows: BTreeMap<String, Vec<(i64, i64)>>,
    /// For each device, who signed its admit: `admin KEY`, `delegate FINGERPRINT` or `member FINGERPRINT`.
    pub signed_by: BTreeMap<String, String>,
    /// For each device, the id of the act that admitted it.
    pub admitted_by: BTreeMap<String, String>,
    pub void: BTreeMap<String, String>,
    pub short: BTreeMap<String, (usize, usize, String)>,
    /// The ids of the admits that stand.
    pub standing: BTreeSet<String>,
    /// For each log (board and origin) of a removed device that a removal names, how many of its entries stand: the least any
    /// removal says.
    pub log_cuts: BTreeMap<(String, String), u64>,
}

pub enum Verdict {
    Ok,
    Short(usize, usize, String),
    Void(String),
}

fn refs(v: &[String]) -> Vec<&str> {
    v.iter().map(String::as_str).collect()
}

/// Whether ``act`` carries the admin signatures its class needs (one strong admin when ``class`` is None) against the charter it
/// names, and against the charter now.
pub fn verdict(id: &str, act: &Act, class: Option<Class>, chain: &Chain, weak_ok: bool) -> Verdict {
    let Some(e) = chain.state_for(act) else { return Verdict::Void("it names a charter this pool never had".into()) };
    let held: Vec<String> = chain.signers(id, act).into_iter().filter(|k| if weak_ok { e.charter.admin(k).is_some_and(|x| x.role == super::keys::Role::Admin) } else { e.charter.is_strong(k) }).collect();
    let count = |c: &super::charter::Charter, keys: &[String]| if weak_ok { c.count_any(&refs(keys)) } else { c.count(&refs(keys)) };
    let need = class.map_or(1, |c| e.charter.needed(c));
    if count(&e.charter, &held) < need {
        return Verdict::Short(count(&e.charter, &held), need, format!("it needs {need} signers and has {}", count(&e.charter, &held)));
    }
    let latest = chain.latest();
    if e.n < latest.n {
        let still: Vec<String> = held.iter().filter(|k| latest.charter.is_strong(k) || chain.cuts.contains_key(*k) || (weak_ok && latest.charter.admin(k).is_some())).cloned().collect();
        let need_now = class.map_or(1, |c| latest.charter.needed(c));
        if count(&e.charter, &still) < need_now {
            return Verdict::Short(count(&e.charter, &still), need_now, "the pool's charter has moved on and its signers no longer meet it".into());
        }
    }
    Verdict::Ok
}

fn clean(name: &str) -> String {
    name.chars().filter(|c| !c.is_control()).take(40).collect()
}

struct Candidate {
    id: String,
    admit: Admit,
    fingerprint: String,
    der: Vec<u8>,
    at_ms: u64,
    /// The devices that signed it, by fingerprint.
    signers: Vec<String>,
}

fn removals(chain: &Chain, acts: &BTreeMap<String, Act>, out: &mut Roster) -> BTreeMap<String, String> {
    let mut removed = BTreeMap::new();
    for (id, a) in acts.iter().filter(|(_, a)| a.kind == "remove") {
        let Ok(Body::Remove(d)) = body::parse(a) else { continue };
        match verdict(id, a, Some(Class::Routine), chain, false) {
            Verdict::Ok => {
                for c in d.cut {
                    out.log_cuts.entry((c.board, c.origin)).and_modify(|s| *s = (*s).min(c.seq)).or_insert(c.seq);
                }
                removed.entry(d.fingerprint).or_insert_with(|| id.clone());
            }
            Verdict::Short(h, n, why) => drop(out.short.insert(id.clone(), (h, n, why))),
            Verdict::Void(why) => drop(out.void.insert(id.clone(), why)),
        }
    }
    removed
}

fn delegates(chain: &Chain, acts: &BTreeMap<String, Act>, seen: &Seen, now: i64, out: &mut Roster) {
    let mut ops: Vec<(i64, u64, &String, &Act)> = acts.iter().filter(|(_, a)| matches!(a.kind.as_str(), "delegate" | "delegate_end")).map(|(id, a)| (seen.get(id).copied().unwrap_or(now), a.at_ms, id, a)).collect();
    ops.sort_by_key(|(first, at, id, _)| (*first, *at, (*id).clone()));
    for (at, _, id, a) in ops {
        match (body::parse(a), a.kind.as_str()) {
            (Ok(Body::Delegate(d)), _) => match verdict(id, a, Some(Class::Sensitive), chain, false) {
                Verdict::Ok => out.windows.entry(d.fingerprint).or_default().push((at, at.saturating_add(DELEGATE_DAYS * DAY_MS))),
                Verdict::Short(h, n, why) => drop(out.short.insert(id.clone(), (h, n, why))),
                Verdict::Void(why) => drop(out.void.insert(id.clone(), why)),
            },
            (Ok(Body::DelegateEnd(d)), _) => match verdict(id, a, Some(Class::Routine), chain, true) {
                Verdict::Ok => out.windows.entry(d.fingerprint).or_default().iter_mut().for_each(|w| w.1 = w.1.min(at)),
                Verdict::Short(h, n, why) => drop(out.short.insert(id.clone(), (h, n, why))),
                Verdict::Void(why) => drop(out.void.insert(id.clone(), why)),
            },
            _ => {}
        }
    }
    for (device, spans) in &out.windows {
        if let Some((_, ends)) = spans.iter().filter(|(from, until)| *from <= now && now < *until).max_by_key(|(_, until)| *until) {
            out.delegates.insert(device.clone(), Delegate { device: device.clone(), ends_ms: *ends });
        }
    }
}

fn candidates(acts: &BTreeMap<String, Act>) -> Vec<Candidate> {
    acts.iter().filter(|(_, a)| a.kind == "admit").filter_map(|(id, a)| {
        let Ok(Body::Admit(admit)) = body::parse(a) else { return None };
        let (der, fingerprint) = body::cert_of(&admit).ok()?;
        Some(Candidate { id: id.clone(), admit, fingerprint, der, at_ms: a.at_ms, signers: a.device_keys().iter().map(|s| s.to_string()).collect() })
    }).collect()
}

/// The admits that stand, given the device that has already been let in: an admin's, then those of a live delegate or (in an open
/// pool) of a member, each of which needs its signer to be let in first.
fn standing<'a>(cands: &'a [Candidate], cx: &Facts, gone: &BTreeSet<String>, limited: &BTreeSet<String>) -> (Vec<&'a Candidate>, BTreeMap<String, String>) {
    let mut ok: Vec<&Candidate> = Vec::new();
    let mut by: BTreeMap<String, String> = BTreeMap::new();
    let mut active: BTreeSet<String> = BTreeSet::new();
    let mut waiting: Vec<&Candidate> = Vec::new();
    for c in cands.iter().filter(|c| !gone.contains(&c.fingerprint) && !limited.contains(&c.id)) {
        if cx.admin_ok.contains(&c.id) {
            by.insert(c.id.clone(), format!("admin {}", cx.admin_of.get(&c.id).cloned().unwrap_or_default()));
            active.insert(c.fingerprint.clone());
            ok.push(c);
        } else {
            waiting.push(c);
        }
    }
    let mut progress = true;
    while progress {
        progress = false;
        waiting.retain(|c| {
            let Some((signer, kind)) = c.signers.iter().find_map(|s| cx.admits(s, c, &active).map(|kind| (s, kind))) else { return true };
            by.insert(c.id.clone(), format!("{kind} {signer}"));
            active.insert(c.fingerprint.clone());
            ok.push(c);
            progress = true;
            false
        });
    }
    (ok, by)
}

/// The per-signer facts the admits are judged by.
struct Facts {
    open: bool,
    /// For each device, its admits and leaves by the order this device first saw them (then by the time signed): whether it was a
    /// member at a given point of that order.
    life: BTreeMap<String, Vec<(i64, u64, String, bool)>>,
    windows: BTreeMap<String, Vec<(i64, i64)>>,
    seen: BTreeMap<String, i64>,
    admin_ok: BTreeSet<String>,
    admin_of: BTreeMap<String, String>,
}

impl Facts {
    /// Whether ``device`` was a member when this device first saw the admit ``c``, by the last of its own admits and leaves seen by
    /// then; a device with neither is.
    fn member_at(&self, device: &str, c: &Candidate) -> bool {
        let key = (self.seen.get(&c.id).copied().unwrap_or(0), c.at_ms, c.id.clone());
        self.life.get(device).and_then(|events| events.iter().rev().find(|(s, t, id, _)| (*s, *t, id.clone()) <= key)).is_none_or(|(_, _, _, joined)| *joined)
    }

    /// How the device ``signer``, taken in as ``active`` has it, may sign the admit ``c``: `member` in an open pool, `delegate`.
    fn admits(&self, signer: &str, c: &Candidate, active: &BTreeSet<String>) -> Option<&'static str> {
        if !active.contains(signer) || !self.member_at(signer, c) {
            return None;
        }
        if self.open && c.admit.via == "segment" {
            Some("member")
        } else if c.admit.via.is_empty() && self.delegate_at(signer, &c.id) {
            Some("delegate")
        } else {
            None
        }
    }

    /// Whether ``device`` was a delegate when this device first saw the admit ``id``.
    fn delegate_at(&self, device: &str, id: &str) -> bool {
        let at = self.seen.get(id).copied().unwrap_or(0);
        at == SETTLED || self.windows.get(device).is_some_and(|w| w.iter().any(|(from, until)| *from <= at && at < *until))
    }
}

/// The admits past the rate of their signer, by the order this device first saw them.
fn over_rate(ok: &[&Candidate], by: &BTreeMap<String, String>, seen: &Seen, now: i64) -> BTreeSet<String> {
    let mut per: BTreeMap<String, Vec<(i64, String)>> = BTreeMap::new();
    for c in ok {
        let who = by.get(&c.id).cloned().unwrap_or_default();
        if !who.starts_with("admin") {
            per.entry(who).or_default().push((seen.get(&c.id).copied().unwrap_or(now), c.id.clone()));
        }
    }
    let mut dropped = BTreeSet::new();
    for (who, mut list) in per {
        list.sort();
        let (per_hour, per_day) = if who.starts_with("delegate") { (DELEGATE_PER_HOUR, DELEGATE_PER_DAY) } else { (OPEN_PER_HOUR, usize::MAX) };
        let mut kept: Vec<i64> = Vec::new();
        for (at, id) in list.into_iter().filter(|(at, _)| *at != SETTLED) {
            let within = |span: i64| kept.iter().filter(|k| at.saturating_sub(**k) < span).count();
            if within(HOUR_MS) >= per_hour || within(DAY_MS) >= per_day {
                dropped.insert(id);
            } else {
                kept.push(at);
            }
        }
    }
    dropped
}

fn leaves(chain_ok: &BTreeSet<String>, acts: &BTreeMap<String, Act>) -> Vec<(String, u64, String)> {
    acts.iter().filter(|(_, a)| a.kind == "leave").filter_map(|(id, a)| {
        let Ok(Body::Leave(d)) = body::parse(a) else { return None };
        (chain_ok.contains(&d.fingerprint) && a.device_keys().contains(&d.fingerprint.as_str())).then(|| (d.fingerprint, a.at_ms, id.clone()))
    }).collect()
}

/// Build the roster from the chain and the acts held.
pub fn build(chain: &Chain, acts: &BTreeMap<String, Act>, seen: &Seen, now: i64) -> Roster {
    let mut out = Roster::default();
    let removed = removals(chain, acts, &mut out);
    delegates(chain, acts, seen, now, &mut out);
    let cands = candidates(acts);
    let seen_of: BTreeMap<String, i64> = cands.iter().map(|c| (c.id.clone(), seen.get(&c.id).copied().unwrap_or(now))).collect();
    let mut life: BTreeMap<String, Vec<(i64, u64, String, bool)>> = BTreeMap::new();
    let first = |id: &String| seen.get(id).copied().unwrap_or(now);
    for c in &cands {
        life.entry(c.fingerprint.clone()).or_default().push((first(&c.id), c.at_ms, c.id.clone(), true));
    }
    for (fp, at, id) in leaves(&cands.iter().map(|c| c.fingerprint.clone()).collect(), acts) {
        life.entry(fp).or_default().push((first(&id), at, id, false));
    }
    life.values_mut().for_each(|events| events.sort());
    let mut facts = Facts { open: chain.latest().charter.policy == Policy::Open, life, windows: out.windows.clone(), seen: seen_of, admin_ok: BTreeSet::new(), admin_of: BTreeMap::new() };
    for c in &cands {
        if let Some(a) = acts.get(&c.id).filter(|a| !chain.signers(&c.id, a).is_empty()) {
            match verdict(&c.id, a, None, chain, false) {
                Verdict::Ok => {
                    facts.admin_ok.insert(c.id.clone());
                    facts.admin_of.insert(c.id.clone(), chain.signers(&c.id, a).first().map(|k| crate::gov::keys::fingerprint_of(k)).unwrap_or_default());
                }
                Verdict::Short(h, n, why) => drop(out.short.insert(c.id.clone(), (h, n, why))),
                Verdict::Void(why) => drop(out.void.insert(c.id.clone(), why)),
            }
        }
    }
    let gone: BTreeSet<String> = removed.keys().cloned().collect();
    let mut limited = BTreeSet::new();
    let (mut ok, mut by) = standing(&cands, &facts, &gone, &limited);
    for _ in 0..8 {
        let more = over_rate(&ok, &by, seen, now);
        if more.is_empty() {
            break;
        }
        limited.extend(more);
        (ok, by) = standing(&cands, &facts, &gone, &limited);
    }
    for c in &cands {
        if limited.contains(&c.id) {
            out.void.insert(c.id.clone(), "it is past the rate a delegate or a member may admit devices".into());
        } else if gone.contains(&c.fingerprint) {
            out.void.insert(c.id.clone(), "that certificate was removed and does not come back".into());
        } else if !ok.iter().any(|k| k.id == c.id) && !out.short.contains_key(&c.id) && !out.void.contains_key(&c.id) {
            out.void.insert(c.id.clone(), "nobody who may admit a device signed it".into());
        }
    }
    out.standing = ok.iter().map(|c| c.id.clone()).collect();
    let mut events: BTreeMap<String, Vec<(u64, String, bool)>> = BTreeMap::new();
    for c in &ok {
        events.entry(c.fingerprint.clone()).or_default().push((c.at_ms, c.id.clone(), true));
    }
    let admitted: BTreeSet<String> = ok.iter().map(|c| c.fingerprint.clone()).collect();
    for (fp, at, id) in leaves(&admitted, acts) {
        events.entry(fp).or_default().push((at, id, false));
    }
    for (fp, mut list) in events {
        list.sort();
        let Some((at, id, joined)) = list.last().cloned() else { continue };
        let Some(c) = ok.iter().filter(|c| c.fingerprint == fp).max_by_key(|c| (c.at_ms, c.id.clone())) else { continue };
        let status = if gone.contains(&fp) { Status::Revoked } else if joined { Status::Active } else { Status::Left };
        out.signed_by.insert(fp.clone(), by.get(&c.id).cloned().unwrap_or_default());
        out.admitted_by.insert(fp.clone(), c.id.clone());
        let _ = id;
        out.devices.insert(fp.clone(), Device { fingerprint: fp, cert: hex(&c.der), name: clean(&c.admit.name), status, at, by: c.admit.how.clone() });
    }
    for (fp, id) in &removed {
        let known = cands.iter().find(|c| &c.fingerprint == fp);
        let at = acts.get(id).map_or(0, |a| a.at_ms);
        out.devices.insert(fp.clone(), Device { fingerprint: fp.clone(), cert: known.map(|c| hex(&c.der)).unwrap_or_default(), name: known.map(|c| clean(&c.admit.name)).unwrap_or_default(), status: Status::Revoked, at, by: "removed".into() });
    }
    out
}
