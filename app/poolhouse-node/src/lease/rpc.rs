//! The socket methods of the lease service: `lease_acquire`, `lease_renew`, `lease_release`,
//! `lease_list` and `lease_wait`. Who holds a lease is the name behind the token, never a
//! request field; a lease can be renewed or released only by its holder.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};
use std::time::{Duration, Instant};

use serde_json::{json, Map, Value};

use super::merge;
use super::table::{Event, Foreign, Table, Why};
use super::types::{Class, Lease, Request, Resource, State, LOCAL};
use super::Leases;
use crate::error::{Error, Result};
use crate::fsutil::random_hex;
use crate::lineage::{Anchor, Peer};
use crate::node::Node;
use crate::row::Kind;

pub const METHODS: [&str; 5] = ["lease_acquire", "lease_renew", "lease_release", "lease_list", "lease_wait"];
pub const MAX_WAIT_MS: u64 = 600_000;
/// `lease_wait` requests a device answers at once.
pub const MOST_WAITERS: usize = 64;
/// `lease_wait` requests the processes of one process tree have waiting at once.
pub const MOST_WAITERS_PER_LINEAGE: usize = 16;
const POLL: Duration = Duration::from_millis(25);

/// The params each lease method takes.
pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "lease_acquire" => &["resources", "class", "estimate_s", "ttl_s", "pid", "remote", "local", "wait", "tag", "quiet"],
        "lease_renew" => &["id", "ttl_s", "pid"],
        "lease_release" => &["id"],
        "lease_list" => &[],
        "lease_wait" => &["id", "timeout_ms"],
        _ => return None,
    })
}

fn num(p: &Map<String, Value>, key: &str) -> Result<Option<u64>> {
    match p.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(v) => v.as_u64().map(Some).ok_or_else(|| Error::Invalid(format!("{key} is a whole number"))),
    }
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    p.get(key).and_then(Value::as_str).ok_or_else(|| Error::Invalid(format!("{key} is text")))
}

/// The pool-wide claims other devices hold on the boards this table has dealings with: the boards of the leases in it that hold
/// one, and ``also``. Only a board a pool-wide claim is on is folded.
fn foreign(node: &mut Node, also: &[&str]) -> Result<Foreign> {
    let mut boards: Vec<String> = also.iter().map(|b| b.to_string()).collect();
    boards.extend(node.leases.table.leases.iter().filter(|l| l.resources.iter().any(Resource::pool_wide)).map(|l| l.board.clone()));
    boards.sort();
    boards.dedup();
    let mut out = Foreign::new();
    for b in boards {
        if node.boards.contains_key(&b) {
            let entries = node.view(&b)?;
            out.extend(merge::foreign_holds(&b, &entries));
        }
    }
    Ok(out)
}

fn lines(l: &Lease) -> Vec<String> {
    l.resources.iter().map(Resource::line).collect()
}

/// Whether the board is told of an event: not when the lease it is about is quiet or holds claims of things on this device alone.
fn on_board(event: &Event) -> bool {
    match event {
        Event::Granted(l) | Event::Ended(l, _) => l.on_board(),
        Event::Takeover { to, .. } => to.on_board(),
    }
}

/// Write what happened to the boards of the holders it concerns. The table is the record of a lease: a board that refuses the entry
/// for a limit (the log is full, or the holder wrote its share) is skipped.
pub fn mirror(node: &mut Node, events: &[Event]) -> Result<()> {
    if node.leases.hold(events) {
        return Ok(());
    }
    for event in events {
        if let Event::Granted(l) = event {
            let tree = node.boards.get(&l.board).map(|h| h.names.tree(&l.name)).unwrap_or_default();
            let now = node.leases.now();
            node.leases.holders.note(l, &tree, now)?;
        }
    }
    let mut drafts: BTreeMap<String, Vec<(Kind, String, Value)>> = BTreeMap::new();
    for event in events.iter().filter(|e| on_board(e)) {
        let (l, kind, body) = match event {
            Event::Granted(l) => (l, Kind::Lease, json!({"lease": l.id, "action": "acquire", "resources": lines(l)})),
            Event::Ended(l, why) if l.state == State::Held => (l, Kind::Lease, json!({"lease": l.id, "action": why.word(), "resources": lines(l)})),
            Event::Ended(..) => continue,
            Event::Takeover { from, why, to } => {
                let detail = format!("took the resources of {} ({} {}) for {}", from.holder, from.id, why.word(), to.id);
                (&**to, Kind::Audit, json!({"event": "lease_takeover", "subject": to.id, "detail": detail}))
            }
        };
        drafts.entry(l.board.clone()).or_default().push((kind, l.name.clone(), body));
    }
    for (board, rows) in drafts {
        node.host(&board)?.board.append_many(rows)?;
    }
    for event in events {
        if let Event::Ended(l, Why::Expired) = event {
            if l.quiet {
                continue;
            }
            for (kind, key) in crate::claims::held_by(l) {
                let said = format!("Your claim {kind} {key} expired: its time ran out before it was renewed. Take it again with: \
                                    poolhouse-workspace claim {kind} {key} (renew what you hold with: poolhouse-workspace heartbeat)");
                crate::notice::tell(node, &l.board, &l.name, "claim expired", &said)?;
            }
        }
    }
    Ok(())
}

/// Sweep, grant what is now free, tell the boards.
pub fn tick(node: &mut Node, also: &[&str]) -> Result<()> {
    let foreign = foreign(node, also)?;
    let events = node.leases.tick(&foreign)?;
    mirror(node, &events)
}

fn show(t: &Table, node: &Node, l: &Lease, viewer: &str) -> Value {
    let (position, wait_estimate_s) = t.standing(&node.leases.cfg, node.leases.now(), &l.id);
    let own = l.board == viewer;
    json!({"id": l.id, "state": l.state, "holder": if own { l.holder.as_str() } else { "other-board" },
           "resources": if own || l.state == State::Held { json!(l.resources) } else { json!([]) },
           "class": l.class, "estimate_s": l.estimate_s, "since_ms": l.since_ms, "granted_ms": l.granted_ms,
           "expires_ms": l.expires_ms, "ttl_s": l.ttl_s, "pid": if own { l.pid } else { 0 }, "remote": l.remote,
           "tag": if own { l.tag.as_str() } else { "" }, "quiet": l.quiet, "position": position, "wait_estimate_s": wait_estimate_s})
}

fn authority(r: &Resource) -> Result<()> {
    if r.device() != LOCAL {
        return Err(Error::Denied(format!("device {} decides its own leases; this node is authoritative for `{LOCAL}`", r.device())));
    }
    Ok(())
}

fn acquire(node: &mut Node, board: &str, who: &str, p: &Map<String, Value>) -> Result<Value> {
    let resources: Vec<Resource> = serde_json::from_value(p.get("resources").cloned().unwrap_or(Value::Null))
        .map_err(|e| Error::Invalid(format!("resources is a list of typed resources: {e}")))?;
    resources.iter().try_for_each(authority)?;
    if !p.get("quiet").and_then(Value::as_bool).unwrap_or(false) {
        node.work_gate()?;
    }
    let class = match p.get("class").and_then(Value::as_str) {
        None | Some("interactive") => Class::Interactive,
        Some("background") => Class::Background,
        _ => return Err(Error::Invalid("class is interactive or background".into())),
    };
    let req = Request {
        board: board.into(), name: who.into(), resources, class, estimate_s: num(p, "estimate_s")?, ttl_s: num(p, "ttl_s")?,
        pid: num(p, "pid")?.map_or(Ok(0), |n| u32::try_from(n).map_err(|_| Error::Invalid("pid is a process id".into())))?,
        remote: p.get("remote").and_then(Value::as_bool).unwrap_or(false), local: p.get("local").and_then(Value::as_bool).unwrap_or(false), wait: p.get("wait").and_then(Value::as_bool).unwrap_or(true),
        tag: p.get("tag").map_or(Ok(""), |v| v.as_str().ok_or_else(|| Error::Invalid("tag is text".into())))?.to_string(),
        quiet: p.get("quiet").and_then(Value::as_bool).unwrap_or(false),
    };
    if req.pid != 0 && req.remote {
        return Err(Error::Invalid("a remote holder has no local pid; its lease lives by expiry".into()));
    }
    let also: &[&str] = if req.resources.iter().any(Resource::pool_wide) { &[board] } else { &[] };
    let (tree_key, now) = (node.boards.get(board).map(|h| h.names.tree(who)).unwrap_or_default(), node.leases.now());
    let per_tree = node.leases.cfg.max_worktree_records;
    node.leases.holders.room_for(&req, (&tree_key, per_tree), now)?;
    tick(node, also)?;
    let (now, id) = (node.leases.now(), format!("l{}", random_hex(8)?));
    let tree = node.tree_leases(board, who);
    let id = node.leases.table.enqueue(&node.leases.cfg, now, &req, (id, tree))?;
    tick(node, also)?;
    node.leases.save()?;
    let table = &node.leases.table;
    let lease = table.find(&id).cloned().ok_or_else(|| Error::Invalid("the lease ended at once".into()))?;
    if lease.state == State::Queued && !req.wait {
        let elsewhere = foreign(node, also)?;
        let table = &node.leases.table;
        let mut blockers: Vec<Value> = lease.resources.iter().filter_map(|r| elsewhere.get(&(board.to_string(), r.key())))
            .map(|holder| json!({"holder": holder, "expires_ms": 0})).collect();
        blockers.extend(table.leases.iter().filter(|o| o.state == State::Held && o.party() != lease.party()
            && o.resources.iter().any(|x| lease.resources.iter().any(|r| r.conflicts(x) || (!r.exclusive() && r.key() == x.key()))))
            .map(|o| json!({"holder": if o.board == board { o.holder.as_str() } else { "other-board" }, "expires_ms": o.expires_ms})));
        node.leases.table.release(&id, &lease.holder)?;
        node.leases.save()?;
        return Ok(json!({"state": "busy", "blockers": blockers}));
    }
    Ok(show(&node.leases.table, node, &lease, board))
}

/// Handle one lease request. ``wait_ms`` of `lease_wait` is honoured by `serve`, not here:
/// this answers with the lease's state now.
pub fn call(node: &mut Node, method: &str, board: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let access = node.access(token, board, true)?;
    if access.holder.board != board {
        return Err(Error::Denied("leases belong to the sessions of their own board".into()));
    }
    let who = access.holder.name.clone();
    let holder = format!("{board}/{who}");
    match method {
        "lease_acquire" => acquire(node, board, &who, p),
        "lease_renew" | "lease_wait" => {
            tick(node, &[])?;
            let (now, id) = (node.leases.now(), text(p, "id")?.to_string());
            let ttl = if method == "lease_renew" { num(p, "ttl_s")?.unwrap_or(0) } else { 0 };
            if let Some(pid) = if method == "lease_renew" { num(p, "pid")? } else { None } {
                let pid = u32::try_from(pid).map_err(|_| Error::Invalid("pid is a process id".into()))?;
                node.leases.table.bind(&id, &holder, pid)?;
            }
            let Leases { cfg, table, .. } = &mut node.leases;
            table.renew(cfg, now, &id, &holder, ttl)?;
            if method == "lease_renew" {
                node.leases.save()?;
            }
            let lease = node.leases.table.find(&id).cloned().ok_or_else(|| Error::Invalid("no such lease".into()))?;
            Ok(show(&node.leases.table, node, &lease, board))
        }
        "lease_release" => {
            tick(node, &[])?;
            let ended = node.leases.table.release(text(p, "id")?, &holder)?;
            node.leases.save()?;
            mirror(node, &[Event::Ended(ended.clone(), super::table::Why::Released)])?;
            tick(node, &[])?;
            Ok(json!({"id": ended.id, "released": true}))
        }
        "lease_list" => {
            tick(node, &[])?;
            let t = &node.leases.table;
            let all: Vec<Value> = t.leases.iter().map(|l| show(t, node, l, board)).collect();
            Ok(json!({"leases": all, "config": node.leases.cfg}))
        }
        _ => Err(Error::Invalid("unknown method".into())),
    }
}

static WAITING: AtomicUsize = AtomicUsize::new(0);
static TREES: Mutex<BTreeMap<(u32, u64), usize>> = Mutex::new(BTreeMap::new());

/// A place among the `lease_wait` requests being answered; given back when dropped.
struct Waiting {
    tree: Option<Anchor>,
}

impl Waiting {
    /// A place for a request of the process tree ``tree`` (None when it is not known, which holds only to the places of the device).
    fn take(tree: Option<Anchor>) -> std::result::Result<Waiting, Error> {
        if WAITING.fetch_add(1, Ordering::SeqCst) >= MOST_WAITERS {
            WAITING.fetch_sub(1, Ordering::SeqCst);
            return Err(crate::limits::refuse("this device is waiting for as many lease requests as it may: ask again shortly", MOST_WAITERS, "MOST_WAITERS", "lease/rpc.rs"));
        }
        if let Some(top) = tree {
            let mut trees = TREES.lock().unwrap_or_else(PoisonError::into_inner);
            let held = trees.entry((top.pid, top.start)).or_default();
            if *held >= MOST_WAITERS_PER_LINEAGE {
                WAITING.fetch_sub(1, Ordering::SeqCst);
                return Err(crate::limits::refuse("the processes of this process tree are waiting for as many lease requests as they may: ask again shortly", MOST_WAITERS_PER_LINEAGE, "MOST_WAITERS_PER_LINEAGE", "lease/rpc.rs"));
            }
            *held += 1;
        }
        Ok(Waiting { tree })
    }
}

impl Drop for Waiting {
    fn drop(&mut self) {
        WAITING.fetch_sub(1, Ordering::SeqCst);
        if let Some(top) = self.tree {
            let mut trees = TREES.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(held) = trees.get_mut(&(top.pid, top.start)) {
                *held -= 1;
                if *held == 0 {
                    trees.remove(&(top.pid, top.start));
                }
            }
        }
    }
}

/// Answer a request, letting `lease_wait` block up to its `timeout_ms` for a grant without
/// holding the node's lock while it sleeps. A device waits for `MOST_WAITERS` requests at once, and the processes of one process tree
/// for `MOST_WAITERS_PER_LINEAGE` of them.
pub fn serve(run: &dyn Fn(&Value) -> Result<Value>, request: &Value, peer: &Peer) -> Result<Value> {
    let waits = request.get("method").and_then(Value::as_str) == Some("lease_wait");
    let timeout = request.pointer("/params/timeout_ms").and_then(Value::as_u64).unwrap_or(0).min(MAX_WAIT_MS);
    let _place = if waits && timeout > 0 {
        match Waiting::take(crate::lineage::tree_of(&peer.lineage())) {
            Ok(place) => Some(place),
            Err(refused) => return Ok(crate::api::respond(request, Err(refused))),
        }
    } else {
        None
    };
    let (start, mut reply) = (Instant::now(), run(request)?);
    while waits && reply["ok"] == true && reply["result"]["state"] == "queued" && start.elapsed() < Duration::from_millis(timeout) {
        std::thread::sleep(POLL);
        reply = run(request)?;
    }
    Ok(reply)
}
