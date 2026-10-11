//! What keeps a request that is made again and again from wearing a person down until they accept to be left alone.
//!
//! The same request made again is the same item (`same`). A requester whose requests the person declined, or let run out, twice is
//! held: what it asks next is kept, shown to nobody and pushed to nobody, and the person is told once, quietly, with a choice. Each
//! hold lasts longer than the last, a third in a day suspends the requester and its grants, a person can deny and block, and a scope
//! that was declined shows how often before and takes one more deliberate step to approve. A request from a source that is not in a
//! pool the person governs, one nobody told the agent to make, and everything past a cap on what reaches the phone in an hour reaches
//! it as a list to open, never as a push that interrupts. Declines, timeouts and holds are kept on disk, so a restart forgets none.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, write_atomic};
use crate::row::canonical;

pub const URGENT: &str = "urgent";
pub const QUIET: &str = "quiet";
pub const SILENT: &str = "silent";

const HOUR_MS: u64 = 3_600_000;
const DAY_MS: u64 = 24 * HOUR_MS;
const MOST_DECLINED: usize = 400;
const MOST_ENTRIES: usize = 400;

/// The numbers the brakes run on. A node starts with `Rules::default()`; the fields are public so a check can shorten a wait.
#[derive(Clone, Debug)]
pub struct Rules {
    /// How long a decline or a timeout counts, and how long a scope that was declined is remembered.
    pub window_ms: u64,
    pub declined_ms: u64,
    /// After this long a requester may ask for the same kind again after its first decline.
    pub backoff_ms: u64,
    /// The same-kind declines or timeouts of a session, of an agent (any kind) and of a pool (any kind) that hold it.
    pub hold_after: [usize; 3],
    /// How long the first and the second hold of the last day last; a third suspends instead.
    pub hold_steps_ms: [u64; 2],
    pub suspend_ms: u64,
    /// What a deny and block lasts.
    pub block_ms: u64,
    /// Requests that interrupt: at once for one requester, for the whole person, and pushes in an hour. Quiet pushes in an hour.
    pub urgent_per_requester: usize,
    pub urgent_pending: usize,
    pub pushes_per_hour: usize,
    pub quiet_per_hour: usize,
    /// Requests one held requester may have kept back.
    pub most_held: usize,
}

impl Default for Rules {
    fn default() -> Rules {
        Rules {
            window_ms: DAY_MS, declined_ms: 30 * DAY_MS, backoff_ms: 30_000, hold_after: [2, 5, 20], hold_steps_ms: [HOUR_MS, 4 * HOUR_MS],
            suspend_ms: DAY_MS, block_ms: DAY_MS, urgent_per_requester: 3, urgent_pending: 8, pushes_per_hour: 10, quiet_per_hour: 4, most_held: 8,
        }
    }
}

/// Who asks, as the three levels the brakes count at: the session, the agent (its harness and model) and the pool (the board).
#[derive(Clone, Debug)]
pub struct Requester {
    pub keys: [String; 3],
    pub session: String,
    pub board: String,
}

/// The requester of the session ``name`` of ``board`` whose harness and model are as given.
pub fn requester(board: &str, name: &str, harness: &str, model: &str) -> Requester {
    let agent = if harness.is_empty() && model.is_empty() { name.to_string() } else { format!("{harness}/{model}") };
    Requester { keys: [format!("session:{board}/{name}"), format!("agent:{board}/{agent}"), format!("pool:{board}")], session: name.into(), board: board.into() }
}

/// What the brakes say about a requester that asks for a kind of thing now.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Standing {
    Free,
    /// Asked for the same kind too soon after a decline: ask again at ``until_ms``.
    Backoff { until_ms: u64 },
    /// Held at the level ``key`` names until ``until_ms``: what it asks is kept back.
    Held { until_ms: u64, key: String },
    /// Refused outright until ``until_ms``, because a person blocked it or it was suspended.
    Refused { until_ms: u64, cause: String },
}

/// What a decline or a timeout did to the requester.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Outcome {
    /// The level that started to hold the requester now, and how long.
    pub held: Option<(String, u64)>,
    /// The level that was suspended now.
    pub suspended: Option<String>,
}

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
struct Entry {
    /// When each decline or timeout happened and of what kind of request.
    declines: Vec<(u64, String)>,
    /// When each hold of the last day began.
    holds: Vec<u64>,
    hold_until_ms: u64,
    refuse_until_ms: u64,
    refuse_cause: String,
    not_before_ms: u64,
    not_before_kind: String,
    penalty: u32,
    /// The summary of the current hold, how many were kept back when it was made, and whether a person answered one in this hold.
    summary: String,
    summary_queued: u64,
    answered: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
struct Declined {
    board: String,
    action: String,
    resource: String,
    at_ms: u64,
}

#[derive(Serialize, Deserialize, Default)]
struct File {
    entries: BTreeMap<String, Entry>,
    declined: Vec<Declined>,
    pushes: Vec<u64>,
    quiet: Vec<u64>,
}

/// The ledger of declines, holds and pushes of a node, kept in one atomically replaced file.
pub struct Fatigue {
    path: PathBuf,
    file: File,
    pub rules: Rules,
}

/// The kind of a request: the sorted actions it asks for. Declines of one kind count together.
pub fn kind_of(actions: &[&str]) -> String {
    let mut all: Vec<&str> = actions.to_vec();
    all.sort_unstable();
    all.dedup();
    all.join("+")
}

/// What makes two requests the same: the board, the session and the sorted actions and resources, and nothing else. The note, the
/// time asked for and the number of uses are left out, so asking again a little differently is still the same request.
pub fn same(board: &str, session: &str, asks: &[(&str, &str)]) -> String {
    let mut all: Vec<(&str, &str)> = asks.to_vec();
    all.sort_unstable();
    all.dedup();
    sha256_hex(canonical(&serde_json::json!({"board": board, "session": session, "asks": all})).as_bytes())
}

impl Fatigue {
    pub fn open(path: &Path) -> Result<Fatigue> {
        let file = match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("fatigue unreadable: {e}")))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => File::default(),
            Err(e) => return Err(e.into()),
        };
        Ok(Fatigue { path: path.into(), file, rules: Rules::default() })
    }

    fn save(&mut self) -> Result<()> {
        if self.file.entries.len() > MOST_ENTRIES {
            self.file.entries.retain(|_, e| !e.declines.is_empty() || e.hold_until_ms > 0 || e.refuse_until_ms > 0);
        }
        write_atomic(&self.path, &serde_json::to_vec(&self.file)?)
    }

    /// Forget what is old enough to have stopped counting.
    fn prune(&mut self, now: u64) {
        let (window, declined) = (self.rules.window_ms, self.rules.declined_ms);
        for e in self.file.entries.values_mut() {
            e.declines.retain(|(at, _)| now.saturating_sub(*at) < window);
            e.holds.retain(|at| now.saturating_sub(*at) < window);
        }
        self.file.declined.retain(|d| now.saturating_sub(d.at_ms) < declined);
        self.file.pushes.retain(|at| now.saturating_sub(*at) < HOUR_MS);
        self.file.quiet.retain(|at| now.saturating_sub(*at) < HOUR_MS);
    }

    /// What the brakes say about ``who`` asking for ``kind`` at ``now``: refused beats held beats too soon.
    pub fn standing(&mut self, who: &Requester, kind: &str, now: u64) -> Standing {
        self.prune(now);
        let entries: Vec<(&String, Option<&Entry>)> = who.keys.iter().map(|k| (k, self.file.entries.get(k))).collect();
        if let Some(e) = entries.iter().filter_map(|(_, e)| *e).find(|e| e.refuse_until_ms > now) {
            return Standing::Refused { until_ms: e.refuse_until_ms, cause: e.refuse_cause.clone() };
        }
        if let Some((key, e)) = entries.iter().find_map(|(k, e)| e.filter(|e| e.hold_until_ms > now).map(|e| ((*k).clone(), e))) {
            return Standing::Held { until_ms: e.hold_until_ms, key };
        }
        match entries[0].1 {
            Some(e) if e.not_before_ms > now && e.not_before_kind == kind => Standing::Backoff { until_ms: e.not_before_ms },
            _ => Standing::Free,
        }
    }

    /// A decline, or a request that ran out unanswered, of a request of ``kind`` by ``who``. Counted at the three levels; a level that
    /// reaches its threshold starts a hold, and a third hold in a day suspends.
    pub fn decline(&mut self, who: &Requester, kind: &str, now: u64) -> Result<Outcome> {
        self.prune(now);
        let mut outcome = Outcome::default();
        for (level, key) in who.keys.iter().enumerate() {
            let rules = self.rules.clone();
            let e = self.file.entries.entry(key.clone()).or_default();
            e.declines.push((now, kind.to_string()));
            let count = if level == 0 { e.declines.iter().filter(|(_, k)| k == kind).count() } else { e.declines.len() };
            if count < rules.hold_after[level] || e.hold_until_ms > now || e.refuse_until_ms > now {
                continue;
            }
            e.holds.push(now);
            e.answered = false;
            e.summary.clear();
            e.summary_queued = 0;
            if e.holds.len() > rules.hold_steps_ms.len() {
                (e.refuse_until_ms, e.refuse_cause, e.penalty) = (now + rules.suspend_ms, "suspended".into(), e.penalty + 20);
                e.hold_until_ms = 0;
                outcome.suspended = Some(key.clone());
            } else {
                let step = rules.hold_steps_ms[e.holds.len() - 1];
                (e.hold_until_ms, e.penalty) = (now + step, e.penalty + 5);
                outcome.held = Some((key.clone(), step));
            }
        }
        if outcome.held.is_none() && outcome.suspended.is_none() {
            let e = self.file.entries.entry(who.keys[0].clone()).or_default();
            (e.not_before_ms, e.not_before_kind) = (now + self.rules.backoff_ms, kind.to_string());
        }
        self.save()?;
        Ok(outcome)
    }

    /// A request of ``kind`` by ``who`` was approved: what it was declined for stops counting against it.
    pub fn approved(&mut self, who: &Requester, kind: &str) -> Result<()> {
        for key in &who.keys {
            if let Some(e) = self.file.entries.get_mut(key) {
                e.declines.retain(|(_, k)| k != kind);
                e.not_before_ms = 0;
            }
        }
        self.save()
    }

    /// A person blocked ``key``: nothing it asks is taken for ``ms``.
    pub fn block(&mut self, key: &str, now: u64, ms: u64) -> Result<()> {
        let e = self.file.entries.entry(key.to_string()).or_default();
        (e.refuse_until_ms, e.refuse_cause) = (now + ms, "blocked".into());
        e.hold_until_ms = 0;
        self.save()
    }

    /// Start an episode at ``key`` that lasts until ``until_ms``: what a notice about it is made once for.
    pub fn begin(&mut self, key: &str, until_ms: u64) -> Result<()> {
        self.file.entries.entry(key.to_string()).or_default().hold_until_ms = until_ms;
        self.save()
    }

    /// When the hold or the refusal at ``key`` ends, 0 when there is none.
    pub fn until(&self, key: &str) -> u64 {
        self.file.entries.get(key).map_or(0, |e| e.hold_until_ms.max(e.refuse_until_ms))
    }

    /// Let ``key`` ask again: its hold and its refusal end, and what it was declined for stops counting.
    pub fn lift(&mut self, key: &str) -> Result<()> {
        if let Some(e) = self.file.entries.get_mut(key) {
            (e.hold_until_ms, e.refuse_until_ms, e.not_before_ms) = (0, 0, 0);
            e.refuse_cause.clear();
            e.declines.clear();
            e.summary.clear();
        }
        self.save()
    }

    /// A person said keep holding ``key``: the hold lasts at least ``ms`` more, and no further summary is made until it ends.
    pub fn keep(&mut self, key: &str, now: u64, ms: u64) -> Result<()> {
        if let Some(e) = self.file.entries.get_mut(key) {
            e.hold_until_ms = e.hold_until_ms.max(now + ms);
            e.answered = true;
            e.summary.clear();
        }
        self.save()
    }

    /// The levels whose hold ran out by ``now``, so what they kept back can be let go.
    pub fn ended(&mut self, now: u64) -> Result<Vec<String>> {
        let done: Vec<String> = self.file.entries.iter().filter(|(_, e)| e.hold_until_ms > 0 && e.hold_until_ms <= now).map(|(k, _)| k.clone()).collect();
        for key in &done {
            if let Some(e) = self.file.entries.get_mut(key) {
                (e.hold_until_ms, e.summary, e.summary_queued, e.answered) = (0, String::new(), 0, false);
            }
        }
        if !done.is_empty() {
            self.save()?;
        }
        Ok(done)
    }

    /// The summary of the hold at ``key`` and how many requests it counted, empty when there is none; and whether a person answered one.
    pub fn summary_of(&self, key: &str) -> (String, u64, bool) {
        self.file.entries.get(key).map_or((String::new(), 0, false), |e| (e.summary.clone(), e.summary_queued, e.answered))
    }

    pub fn set_summary(&mut self, key: &str, id: &str, queued: u64) -> Result<()> {
        let e = self.file.entries.entry(key.to_string()).or_default();
        (e.summary, e.summary_queued) = (id.into(), queued);
        self.save()
    }

    /// Whether ``who`` is suspended now: a suspended session holds no grant.
    pub fn suspended(&self, who: &Requester, now: u64) -> bool {
        who.keys.iter().any(|k| self.file.entries.get(k).is_some_and(|e| e.refuse_until_ms > now && e.refuse_cause == "suspended"))
    }

    /// The penalty ``key`` has earned for the trust ledger to read.
    pub fn penalty(&self, key: &str) -> u32 {
        self.file.entries.get(key).map_or(0, |e| e.penalty)
    }

    /// Remember that a person declined these scopes of ``board``.
    pub fn note_declined(&mut self, board: &str, asks: &[(&str, &str)], now: u64) -> Result<()> {
        for (action, resource) in asks {
            self.file.declined.push(Declined { board: board.into(), action: (*action).into(), resource: (*resource).into(), at_ms: now });
        }
        let extra = self.file.declined.len().saturating_sub(MOST_DECLINED);
        self.file.declined.drain(..extra);
        self.save()
    }

    /// How often the most declined of these scopes was declined before on ``board``.
    pub fn declined_before(&mut self, board: &str, asks: &[(&str, &str)], now: u64) -> u64 {
        self.prune(now);
        asks.iter().map(|(a, r)| self.file.declined.iter().filter(|d| d.board == board && d.action == *a && d.resource == *r).count() as u64).max().unwrap_or(0)
    }

    pub fn pushes_this_hour(&mut self, now: u64) -> usize {
        self.prune(now);
        self.file.pushes.len()
    }

    pub fn quiet_this_hour(&mut self, now: u64) -> usize {
        self.prune(now);
        self.file.quiet.len()
    }

    pub fn record_push(&mut self, quiet: bool, now: u64) -> Result<()> {
        if quiet { self.file.quiet.push(now) } else { self.file.pushes.push(now) }
        self.save()
    }
}

/// What decides how loudly a request is delivered.
pub struct Delivery<'a> {
    pub trusted: bool,
    pub prompted: &'a str,
    /// How many requests of this requester interrupt now, how many of anyone's, and how many pushes went out in the last hour.
    pub requester_urgent: usize,
    pub all_urgent: usize,
    pub pushes: usize,
}

/// How loudly a request is delivered, and whether it was made quiet only because a cap was reached (so the overflow is summarised).
pub fn urgency(rules: &Rules, d: &Delivery) -> (&'static str, bool) {
    if !d.trusted || d.prompted == "no" {
        return (SILENT, false);
    }
    if d.requester_urgent >= rules.urgent_per_requester || d.all_urgent >= rules.urgent_pending || d.pushes >= rules.pushes_per_hour {
        return (SILENT, true);
    }
    (URGENT, false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ledger() -> Fatigue {
        let dir = tempfile::tempdir().unwrap();
        let mut f = Fatigue::open(&dir.keep().join("fatigue.json")).unwrap();
        f.rules.backoff_ms = 1000;
        f
    }

    fn who() -> Requester {
        requester("demo", "claude-a", "claude-code", "sonnet")
    }

    #[test]
    fn the_same_request_with_a_different_note_or_time_is_the_same() {
        let a = same("demo", "claude-a", &[("add_device", "*"), ("link_read", "a>b")]);
        assert_eq!(a, same("demo", "claude-a", &[("link_read", "a>b"), ("add_device", "*")]));
        assert_ne!(a, same("demo", "claude-b", &[("add_device", "*"), ("link_read", "a>b")]));
        assert_ne!(a, same("demo", "claude-a", &[("add_device", "*")]));
        assert_eq!(kind_of(&["link_read", "add_device", "add_device"]), "add_device+link_read");
    }

    #[test]
    fn a_decline_backs_off_the_same_kind_and_the_second_holds_with_longer_holds_after_and_a_suspension() {
        let mut f = ledger();
        let (me, now) = (who(), 1_000_000);
        assert_eq!(f.standing(&me, "add_device", now), Standing::Free);
        assert_eq!(f.decline(&me, "add_device", now).unwrap(), Outcome::default());
        assert_eq!(f.standing(&me, "add_device", now + 10), Standing::Backoff { until_ms: now + 1000 });
        assert_eq!(f.standing(&me, "sync_now", now + 10), Standing::Free, "another kind is not held back");
        assert_eq!(f.standing(&me, "add_device", now + 2000), Standing::Free, "after the wait it may ask");
        let second = f.decline(&me, "add_device", now + 2000).unwrap();
        assert_eq!(second.held, Some(("session:demo/claude-a".to_string(), HOUR_MS)));
        assert!(matches!(f.standing(&me, "sync_now", now + 3000), Standing::Held { .. }), "a hold covers every kind");
        let later = now + HOUR_MS + 5000;
        assert_eq!(f.ended(later).unwrap(), vec!["session:demo/claude-a".to_string()]);
        assert_eq!(f.standing(&me, "add_device", later), Standing::Free);
        let again = f.decline(&me, "add_device", later).unwrap();
        assert_eq!(again.held.map(|h| h.1), Some(4 * HOUR_MS), "the first decline after a hold holds again, for longer");
        let t3 = later + 4 * HOUR_MS + 5000;
        f.ended(t3).unwrap();
        let third = f.decline(&me, "add_device", t3).unwrap();
        assert!(third.suspended.is_some() && third.held.is_none(), "{third:?}");
        assert!(matches!(f.standing(&me, "sync_now", t3 + 20), Standing::Refused { cause, .. } if cause == "suspended"));
        assert!(f.suspended(&me, t3 + 20) && f.penalty("session:demo/claude-a") >= 30);
    }

    #[test]
    fn an_approval_clears_what_that_kind_was_declined_for_and_a_block_refuses_only_that_session() {
        let mut f = ledger();
        let (me, other, now) = (who(), requester("demo", "claude-b", "claude-code", "sonnet"), 5_000_000);
        f.decline(&me, "add_device", now).unwrap();
        f.approved(&me, "add_device").unwrap();
        assert_eq!(f.decline(&me, "add_device", now + 10).unwrap(), Outcome::default(), "the earlier decline no longer counts");
        f.block(&me.keys[0], now + 20, DAY_MS).unwrap();
        assert!(matches!(f.standing(&me, "sync_now", now + 30), Standing::Refused { cause, .. } if cause == "blocked"));
        assert!(!f.suspended(&me, now + 30), "a block does not take the grants");
        assert_eq!(f.standing(&other, "sync_now", now + 30), Standing::Free);
        f.lift("session:demo/claude-a").unwrap();
        assert_eq!(f.standing(&me, "sync_now", now + 40), Standing::Free);
    }

    #[test]
    fn five_declines_of_one_agent_hold_every_session_of_it_and_a_scope_declined_is_remembered() {
        let mut f = ledger();
        let now = 9_000_000;
        for (i, name) in ["claude-a", "claude-b", "claude-c", "claude-d", "claude-g"].into_iter().enumerate() {
            f.decline(&requester("demo", name, "claude-code", "sonnet"), "add_device", now + i as u64).unwrap();
        }
        let sixth = requester("demo", "claude-e", "claude-code", "sonnet");
        assert!(matches!(f.standing(&sixth, "sync_now", now + 10), Standing::Held { key, .. } if key == "agent:demo/claude-code/sonnet"));
        assert_eq!(f.standing(&requester("demo", "claude-f", "other", "model"), "sync_now", now + 10), Standing::Free);
        assert_eq!(f.declined_before("demo", &[("add_device", "*")], now), 0);
        f.note_declined("demo", &[("add_device", "*")], now).unwrap();
        f.note_declined("demo", &[("add_device", "*"), ("sync_now", "*")], now + 1).unwrap();
        assert_eq!(f.declined_before("demo", &[("add_device", "*"), ("link_read", "a>b")], now + 2), 2);
        assert_eq!(f.declined_before("lab", &[("add_device", "*")], now + 2), 0, "another board");
        assert_eq!(f.declined_before("demo", &[("add_device", "*")], now + 31 * DAY_MS), 0, "forgotten after thirty days");
    }

    #[test]
    fn urgency_never_interrupts_for_a_stranger_an_unprompted_ask_or_past_a_cap() {
        let rules = Rules::default();
        let ok = Delivery { trusted: true, prompted: "unknown", requester_urgent: 0, all_urgent: 0, pushes: 0 };
        assert_eq!(urgency(&rules, &ok), (URGENT, false));
        assert_eq!(urgency(&rules, &Delivery { prompted: "yes", ..ok }), (URGENT, false));
        assert_eq!(urgency(&rules, &Delivery { trusted: false, ..ok }), (SILENT, false));
        assert_eq!(urgency(&rules, &Delivery { prompted: "no", ..ok }), (SILENT, false));
        assert_eq!(urgency(&rules, &Delivery { requester_urgent: 3, ..ok }), (SILENT, true));
        assert_eq!(urgency(&rules, &Delivery { all_urgent: 8, ..ok }), (SILENT, true));
        assert_eq!(urgency(&rules, &Delivery { pushes: 10, ..ok }), (SILENT, true));
    }
}
