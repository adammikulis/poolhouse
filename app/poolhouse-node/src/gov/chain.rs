//! The admin-set acts, one step after the other: who the admins are, the thresholds and the holds, epoch by epoch.
//!
//! An act that adds or removes an admin, or moves a threshold, a hold or the mode, names the charter it follows (`prev`) and takes
//! the pool to the next epoch. Acts that follow the same charter form one step:
//!
//! * Removals commute: every removal of the step applies, unless together they would leave fewer strong admins than the
//!   sensitive threshold needs, in which case none applies and a dispute opens. One removal that alone would leave too few is void.
//! * An add or a threshold change counts only if its signers survive the removals of the step.
//! * Two such acts that cannot both hold are settled by the number of distinct strong signers, then by the lower signer
//!   fingerprints; a tie that remains applies neither and opens a dispute that the signers settle by signing one of them again.
//!
//! An act complete on this device only after the first act of its step has run its hold arrives too late to join or contest the step.
//!
//! A step takes effect when the longest hold among its acts has run on this device, counted in awake time from the first moment
//! this device saw each act with all the signatures it needs, with no valid `cancel` seen before then; a `veto` by the key a removal names ends the removal and
//! opens a dispute. While a dispute is open no step is taken. A key that is removed loses every signature of its that is not in the
//! cut its removal carried, wherever it was counted.

use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet};

use super::act::Act;
use super::body::{self, Body};
use super::charter::{Charter, Class, Mode, Unit};
use super::keys::{Role, Strength};
use super::{DAY_MS, HOUR_MS, MAX_ADMINS, MAX_ADMIN_ACTS, SETTLED};

/// The first moment (awake milliseconds) this device saw each act.
pub type Seen = BTreeMap<String, i64>;

/// The charter of one epoch and its hash.
#[derive(Clone, Debug)]
pub struct EpochState {
    pub n: u64,
    pub charter: Charter,
    pub hash: String,
}

impl EpochState {
    fn of(charter: Charter) -> EpochState {
        EpochState { n: charter.epoch, hash: charter.hash(), charter }
    }
}

/// A step formed but not yet in effect.
#[derive(Clone, Debug)]
pub struct Pending {
    pub acts: Vec<String>,
    pub effective_ms: i64,
}

/// A pause of the admin-set acts.
#[derive(Clone, Debug)]
pub struct Dispute {
    pub contested: Vec<String>,
    pub since_ms: i64,
    pub ends_ms: i64,
    pub why: String,
}

/// The result of building the chain.
#[derive(Clone, Debug)]
pub struct Chain {
    pub epochs: Vec<EpochState>,
    pub pending: Option<Pending>,
    pub dispute: Option<Dispute>,
    pub applied: BTreeSet<String>,
    /// Acts that cannot take effect, with why.
    pub void: BTreeMap<String, String>,
    /// Acts that lack signers: how many they have and how many they need.
    pub short: BTreeMap<String, (usize, usize, String)>,
    /// Removed admin keys, with the ids of the acts they signed that the removal kept.
    pub cuts: BTreeMap<String, BTreeSet<String>>,
    /// The acts that had the signatures their kind needs at some point of the fold.
    pub validated: BTreeSet<String>,
    /// The signatures that no longer count, each named by the act id and the key: the key was removed and the act is not in its cut.
    pub struck: BTreeSet<(String, String)>,
}

struct Ctx<'a> {
    pool: &'a str,
    acts: &'a BTreeMap<String, Act>,
    seen: &'a Seen,
    ready: &'a Seen,
    /// When this device took the history of the pool from the member that let it in; a settled act may be cancelled until its hold
    /// has run from then.
    joined: i64,
    now: i64,
}

impl Ctx<'_> {
    fn seen_at(&self, id: &str) -> i64 {
        self.seen.get(id).copied().unwrap_or(self.now)
    }

    /// When the act ``id`` first had the signatures its kind needs on this device: its hold runs from then.
    fn ready_at(&self, id: &str) -> i64 {
        self.ready.get(id).copied().unwrap_or(self.now)
    }
}

/// One act that can take part in a step.
struct Cand {
    id: String,
    seen: i64,
    body: Body,
    signers: Vec<String>,
    effective: i64,
}

enum Check {
    Ok(Vec<String>),
    Short(usize, usize, String),
    Void(String),
}

impl Chain {
    /// The chain of a pool that has taken no step: its charter of epoch 1.
    pub fn new(genesis: &Charter) -> Chain {
        Chain::start(genesis, BTreeSet::new())
    }

    fn start(genesis: &Charter, struck: BTreeSet<(String, String)>) -> Chain {
        Chain {
            epochs: vec![EpochState::of(genesis.clone())], pending: None, dispute: None, applied: BTreeSet::new(), void: BTreeMap::new(), short: BTreeMap::new(),
            cuts: BTreeMap::new(), validated: BTreeSet::new(), struck,
        }
    }

    pub fn latest(&self) -> &EpochState {
        self.epochs.last().expect("a chain has its first epoch")
    }

    /// The epoch the act ``act`` was signed at, when its charter is one this pool has had.
    pub fn state_for(&self, act: &Act) -> Option<&EpochState> {
        usize::try_from(act.epoch).ok().and_then(|n| self.epochs.get(n.checked_sub(1)?)).filter(|e| e.hash == act.prev)
    }

    /// The admin keys whose signature on ``act`` (held under ``id``) still counts.
    pub fn signers(&self, id: &str, act: &Act) -> Vec<String> {
        act.admin_keys().into_iter().filter(|k| !self.struck.contains(&(id.to_string(), (*k).to_string()))).map(str::to_string).collect()
    }
}

fn hours(h: u64) -> i64 {
    i64::try_from(h).unwrap_or(0).saturating_mul(HOUR_MS)
}

fn hold_ms(body: &Body, c: &Charter) -> i64 {
    match body {
        Body::AdminAdd(_) => hours(c.holds.admin_add_h),
        Body::AdminRemove(_) => hours(c.holds.admin_remove_h),
        b if weakens(b, c) => hours(c.holds.admin_add_h.max(c.holds.admin_remove_h)),
        _ => 0,
    }
}

/// Whether the change gives a signer less to do or a thief more room: a smaller threshold, a shorter hold or `mode: members`.
fn weakens(body: &Body, c: &Charter) -> bool {
    let sooner = |new: Option<u64>, now: u64| new.is_some_and(|n| n < now);
    match body {
        Body::SetThreshold(t) => {
            let sensitive = t.sensitive.map(|x| if x == 0 { c.people().clamp(1, 2) } else { x as usize });
            t.routine.is_some_and(|r| r < c.thresholds.routine) || sensitive.is_some_and(|x| x < c.needed(Class::Sensitive)) || (t.unit == Some(Unit::Key) && c.thresholds.unit == Unit::Person)
        }
        Body::SetHolds(h) => sooner(h.admin_add_h, c.holds.admin_add_h) || sooner(h.admin_remove_h, c.holds.admin_remove_h) || sooner(h.handoff_min, c.holds.handoff_min) || sooner(h.dispute_d, c.holds.dispute_d),
        Body::SetMode(m) => m.mode == Mode::Members,
        _ => false,
    }
}

fn apply_set(c: &mut Charter, body: &Body) -> Result<(), String> {
    match body {
        Body::SetThreshold(t) => {
            c.thresholds.routine = t.routine.unwrap_or(c.thresholds.routine);
            c.thresholds.sensitive = t.sensitive.unwrap_or(c.thresholds.sensitive);
            c.thresholds.unit = t.unit.unwrap_or(c.thresholds.unit);
        }
        Body::SetHolds(h) => {
            c.holds.admin_add_h = h.admin_add_h.unwrap_or(c.holds.admin_add_h);
            c.holds.admin_remove_h = h.admin_remove_h.unwrap_or(c.holds.admin_remove_h);
            c.holds.handoff_min = h.handoff_min.unwrap_or(c.holds.handoff_min);
            c.holds.dispute_d = h.dispute_d.unwrap_or(c.holds.dispute_d);
        }
        Body::SetMode(m) if m.mode == Mode::Members => return Err("members-governed mode is not in this build".into()),
        _ => {}
    }
    c.check_numbers().map_err(|_| "the new thresholds need more people than the strong keys listed".to_string())
}

fn check_add(add: &body::Add, s: &EpochState, pool: &str) -> Option<Check> {
    let e = &add.entry;
    let why = if e.since != s.n + 1 {
        "a key is listed at the epoch it joins"
    } else if s.charter.admin(&e.key).is_some() {
        "that key is already listed"
    } else if s.charter.admins.len() >= MAX_ADMINS {
        "a pool names at most sixteen keys"
    } else if e.role == Role::Recovery && s.charter.admins.iter().any(|k| k.role == Role::Recovery) {
        "the recovery key is replaced only by its own signature"
    } else if !e.accepted(pool) {
        "the added key did not accept being listed"
    } else {
        return None;
    };
    Some(Check::Void(why.into()))
}

/// Whether ``cand`` has the signatures its kind needs against the charter of ``s``.
fn check(act: &Act, signed: &[String], body: &Body, s: &EpochState, pool: &str) -> Check {
    let c = &s.charter;
    let strong = |k: &&String| c.is_strong(k);
    let counted: Vec<String> = match body {
        Body::AdminRemove(d) => signed.iter().filter(strong).filter(|k| **k != d.key).cloned().collect(),
        _ => signed.iter().filter(strong).cloned().collect(),
    };
    match body {
        Body::AdminAdd(add) => {
            if let Some(why) = check_add(add, s, pool) {
                return why;
            }
        }
        Body::AdminRemove(d) => match c.admin(&d.key) {
            None => return Check::Void("that key is not an admin of this pool".into()),
            Some(_) if left_after(c, &BTreeSet::from([d.key.clone()])) < c.needed(Class::Sensitive).max(1) => {
                return Check::Void("it would leave the pool with too few admins".into());
            }
            Some(t) if t.role == Role::Recovery && !signed.contains(&t.key) => {
                return Check::Short(c.count(&counted.iter().map(String::as_str).collect::<Vec<_>>()), c.needed(Class::Sensitive), "the recovery key is removed only by its own signature".into());
            }
            Some(_) => {}
        },
        Body::SetThreshold(_) | Body::SetHolds(_) | Body::SetMode(_) => {
            if let Err(why) = apply_set(&mut c.clone(), body) {
                return Check::Void(why);
            }
        }
        _ => return Check::Void(format!("a {} act is not a step of the admin chain", act.kind)),
    }
    let (have, need) = (c.count(&counted.iter().map(String::as_str).collect::<Vec<_>>()), c.needed(Class::Sensitive));
    if have < need {
        return Check::Short(have, need, format!("it needs {need} signers and has {have}"));
    }
    Check::Ok(counted)
}

/// The earliest first-sight of a valid `cancel` or `veto` for each act it names, against the charter of ``s``.
fn stops(cx: &Ctx, s: &EpochState) -> (BTreeMap<String, i64>, BTreeMap<String, i64>) {
    let (mut cancels, mut vetoes) = (BTreeMap::new(), BTreeMap::new());
    let c = &s.charter;
    for (id, a) in cx.acts.iter().filter(|(_, a)| matches!(a.kind.as_str(), "cancel" | "veto")) {
        let (Ok(Body::Cancel(t) | Body::Veto(t)), Some(_)) = (body::parse(a), (a.prev == s.hash).then_some(())) else { continue };
        let signers: Vec<String> = a.admin_keys().into_iter().filter(|k| c.is_strong(k)).map(str::to_string).collect();
        let at = cx.seen_at(id);
        let held = signers.iter().map(String::as_str).collect::<Vec<_>>();
        if a.kind == "cancel" && c.count(&held) >= c.needed(Class::Routine) {
            cancels.entry(t.act.clone()).and_modify(|e: &mut i64| *e = (*e).min(at)).or_insert(at);
        }
        let named = cx.acts.get(&t.act).and_then(|r| match body::parse(r) { Ok(Body::AdminRemove(d)) => Some(d.key), _ => None });
        let named_signed = named.as_deref().and_then(|k| c.admin(k)).is_some_and(|x| x.strength == Strength::Strong && a.admin_keys().contains(&x.key.as_str()));
        if a.kind == "veto" && named_signed {
            vetoes.entry(t.act.clone()).and_modify(|e: &mut i64| *e = (*e).min(at)).or_insert(at);
        }
    }
    (cancels, vetoes)
}

fn dispute(contested: Vec<String>, since: i64, c: &Charter, why: &str) -> Dispute {
    Dispute { contested, since_ms: since, ends_ms: since.saturating_add(i64::try_from(c.holds.dispute_d).unwrap_or(0).saturating_mul(DAY_MS)), why: why.into() }
}

/// What one round of the chain came to.
enum Round {
    Done,
    Blocked(Dispute),
    Waiting(Pending),
    Retry,
    Drop(String, String),
    Applied(Charter, Vec<String>),
}

fn candidates(cx: &Ctx, chain: &mut Chain, dropped: &BTreeSet<String>) -> Vec<Cand> {
    let s = chain.latest().clone();
    let (cancels, vetoes) = stops(cx, &s);
    let mut out = Vec::new();
    for (id, act) in cx.acts.iter().filter(|(id, a)| a.is_chain() && a.prev == s.hash && !dropped.contains(*id)) {
        let Ok(parsed) = body::parse(act) else { continue };
        if act.epoch != s.n {
            chain.void.insert(id.clone(), "it names an epoch that does not match the charter it follows".into());
            continue;
        }
        match check(act, &chain.signers(id, act), &parsed, &s, cx.pool) {
            Check::Void(why) => drop(chain.void.insert(id.clone(), why)),
            Check::Short(have, need, why) => drop(chain.short.insert(id.clone(), (have, need, why))),
            Check::Ok(signers) => {
                chain.validated.insert(id.clone());
                let effective = cx.ready_at(id).saturating_add(hold_ms(&parsed, &s.charter));
                let stoppable_until = if cx.ready_at(id) == SETTLED { cx.joined.saturating_add(hold_ms(&parsed, &s.charter)) } else { effective };
                if cancels.get(id).is_some_and(|c| *c <= stoppable_until) {
                    chain.void.insert(id.clone(), "an admin cancelled it".into());
                } else if vetoes.get(id).is_some_and(|v| *v <= stoppable_until) {
                    chain.void.insert(id.clone(), "the key it removes vetoed it".into());
                } else {
                    out.push(Cand { id: id.clone(), seen: cx.ready_at(id), body: parsed, signers, effective });
                }
            }
        }
    }
    out
}

fn veto_dispute(cx: &Ctx, chain: &Chain, s: &EpochState) -> Option<Dispute> {
    let (_, vetoes) = stops(cx, s);
    vetoes.iter().filter(|(id, _)| chain.void.get(*id).is_some_and(|w| w.contains("vetoed"))).map(|(id, at)| dispute(vec![id.clone()], *at, &s.charter, "the key a removal names vetoed it")).max_by_key(|d| d.ends_ms)
}

/// The survivors of removing ``targets`` from ``c``, and how many strong admins they are by the unit of the thresholds.
fn left_after(c: &Charter, targets: &BTreeSet<String>) -> usize {
    let mut rest = c.clone();
    rest.admins.retain(|k| !targets.contains(&k.key));
    match rest.thresholds.unit {
        Unit::Person => rest.people(),
        Unit::Key => rest.strong().count(),
    }
}

/// How an act ranks against one that cannot hold beside it: more distinct strong signers first, then the lower signer fingerprints.
fn rank(cand: &Cand, c: &Charter) -> (Reverse<usize>, Vec<String>) {
    let mut fingerprints: Vec<String> = cand.signers.iter().filter_map(|k| c.admin(k)).map(|k| k.fingerprint()).collect();
    fingerprints.sort();
    (Reverse(cand.signers.len()), fingerprints)
}

fn round(cx: &Ctx, chain: &mut Chain, dropped: &mut BTreeSet<String>) -> Round {
    let s = chain.latest().clone();
    let mut cands = candidates(cx, chain, dropped);
    if let Some(d) = veto_dispute(cx, chain, &s).filter(|d| cx.now < d.ends_ms) {
        return Round::Blocked(d);
    }
    let first = cands.iter().map(|c| c.seen).min();
    let window = cands.iter().filter(|c| Some(c.seen) == first).map(|c| c.effective).min();
    cands.retain(|c| {
        let timely = window.is_none_or(|w| c.seen <= w);
        if !timely {
            chain.void.insert(c.id.clone(), "it arrived after the step it follows took effect".into());
        }
        timely
    });
    if cands.is_empty() {
        return Round::Done;
    }
    let need = s.charter.needed(Class::Sensitive).max(1);
    let (mut removals, mut others): (Vec<&Cand>, Vec<&Cand>) = (Vec::new(), Vec::new());
    for cand in &cands {
        match &cand.body {
            Body::AdminRemove(d) if left_after(&s.charter, &BTreeSet::from([d.key.clone()])) < need => drop(chain.void.insert(cand.id.clone(), "it would leave the pool with too few admins".into())),
            Body::AdminRemove(_) => removals.push(cand),
            _ => others.push(cand),
        }
    }
    let targets: BTreeSet<String> = removals.iter().filter_map(|r| if let Body::AdminRemove(d) = &r.body { Some(d.key.clone()) } else { None }).collect();
    if removals.len() > 1 && left_after(&s.charter, &targets) < need {
        let ids: Vec<String> = removals.iter().map(|r| r.id.clone()).collect();
        let since = removals.iter().map(|r| cx.ready_at(&r.id)).max().unwrap_or(cx.now);
        return settle(cx, dispute(ids, since, &s.charter, "removals together would leave too few admins"), dropped);
    }
    let survive = |k: &&String| !targets.contains(*k);
    others.retain(|o| {
        let held: Vec<&str> = o.signers.iter().filter(survive).map(String::as_str).collect();
        let kept = s.charter.count(&held) >= need;
        if !kept {
            chain.void.insert(o.id.clone(), "its signers were removed in the same step".into());
        }
        kept
    });
    others.sort_by_key(|o| rank(o, &s.charter));
    if others.len() > 1 && rank(others[0], &s.charter) == rank(others[1], &s.charter) {
        let ids: Vec<String> = others.iter().filter(|o| rank(o, &s.charter) == rank(others[0], &s.charter)).map(|o| o.id.clone()).collect();
        let since = others.iter().map(|o| cx.ready_at(&o.id)).max().unwrap_or(cx.now);
        return settle(cx, dispute(ids, since, &s.charter, "two acts that cannot both hold have the same signers"), dropped);
    }
    for loser in others.iter().skip(1) {
        chain.void.insert(loser.id.clone(), "another act that follows the same charter has more signers".into());
    }
    let step: Vec<&Cand> = removals.into_iter().chain(others.into_iter().take(1)).collect();
    if step.is_empty() {
        return Round::Done;
    }
    let effective = step.iter().map(|c| c.effective).max().unwrap_or(cx.now);
    let ids: Vec<String> = step.iter().map(|c| c.id.clone()).collect();
    if cx.now < effective {
        return Round::Waiting(Pending { acts: ids, effective_ms: effective });
    }
    next_charter(&s, &step, &ids)
}

/// A dispute over acts of this step: it blocks the chain until it ends, and then drops the acts it was about.
fn settle(cx: &Ctx, d: Dispute, dropped: &mut BTreeSet<String>) -> Round {
    if cx.now < d.ends_ms {
        return Round::Blocked(d);
    }
    dropped.extend(d.contested);
    Round::Retry
}

fn next_charter(s: &EpochState, step: &[&Cand], ids: &[String]) -> Round {
    let mut next = s.charter.clone();
    next.epoch += 1;
    for cand in step {
        match &cand.body {
            Body::AdminRemove(d) => next.admins.retain(|k| k.key != d.key),
            Body::AdminAdd(a) => next.admins.push(a.entry.clone()),
            other => {
                if let Err(why) = apply_set(&mut next, other) {
                    return Round::Drop(cand.id.clone(), why);
                }
            }
        }
    }
    next.admins.sort_by(|a, b| a.key.cmp(&b.key));
    Round::Applied(next, ids.to_vec())
}

fn run(genesis: &Charter, cx: &Ctx, struck: BTreeSet<(String, String)>) -> Chain {
    let mut chain = Chain::start(genesis, struck);
    let mut dropped = BTreeSet::new();
    for _ in 0..(2 * MAX_ADMIN_ACTS + 2) {
        match round(cx, &mut chain, &mut dropped) {
            Round::Done => break,
            Round::Blocked(d) => {
                chain.dispute = Some(d);
                break;
            }
            Round::Waiting(p) => {
                chain.pending = Some(p);
                break;
            }
            Round::Retry => {}
            Round::Drop(id, why) => {
                chain.void.insert(id.clone(), why);
                dropped.insert(id);
            }
            Round::Applied(next, ids) => {
                for id in &ids {
                    if let Some(Body::AdminRemove(d)) = cx.acts.get(id).and_then(|a| body::parse(a).ok()) {
                        chain.cuts.insert(d.key.clone(), d.cut.iter().cloned().collect());
                    }
                }
                chain.applied.extend(ids);
                chain.epochs.push(EpochState::of(next));
            }
        }
    }
    chain
}

/// Build the chain from the charter of epoch 1 and every act held, as it stands ``now`` on this device.
pub fn build(genesis: &Charter, pool: &str, acts: &BTreeMap<String, Act>, times: (&Seen, &Seen), joined: i64, now: i64) -> Chain {
    let cx = Ctx { pool, acts, seen: times.0, ready: times.1, joined, now };
    let mut struck: BTreeSet<(String, String)> = BTreeSet::new();
    loop {
        let chain = run(genesis, &cx, struck.clone());
        let own_removal = |id: &String, k: &String| matches!(acts.get(id).map(body::parse), Some(Ok(Body::AdminRemove(d))) if d.key == *k);
        let fresh: Vec<(String, String)> = acts.iter()
            .flat_map(|(id, a)| a.admin_keys().into_iter().map(move |k| (id.clone(), k.to_string())))
            .filter(|(id, k)| chain.cuts.get(k).is_some_and(|cut| !cut.contains(id)) && !own_removal(id, k) && !struck.contains(&(id.clone(), k.clone())))
            .collect();
        if fresh.is_empty() {
            return chain;
        }
        struck.extend(fresh);
    }
}
