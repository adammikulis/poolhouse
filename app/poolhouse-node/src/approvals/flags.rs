//! What looks off about a request, from what this node knows of the session and of what a person already granted.

use std::collections::BTreeSet;

use crate::approvals::content::{names_a_resource, risk_of, ROUTINE};
use crate::approvals::APPROVED;
use crate::error::Result;
use crate::grants::book::{standing, Grant};
use crate::grants::names;
use crate::grants::spec::covers;
use crate::node::Node;

const DAY_MS: u64 = 24 * 3600 * 1000;

/// One ask of a request of grants, as the flags read it.
pub struct Asked<'a> {
    pub action: &'a str,
    pub resource: &'a str,
    pub lasts_ms: u64,
    pub uses: Option<u64>,
}

/// What the node found out about a request of grants: what looks off (written as `FLAG:` lines, which open the whole request on the
/// phone first) and what is only news (written as `NOTE:` lines, which do not).
#[derive(Default)]
pub struct Findings {
    pub flags: Vec<String>,
    pub notes: Vec<String>,
}

/// What a request of grants from ``session`` on ``board`` earns. ``policy`` is the pool's join policy now; ``task_unknown`` says the
/// request named a task the board does not have. That a session is new, and that an action was never asked for on this device, are facts
/// every first request has: they are notes for a routine request, so it can be approved from the notification, and flags for anything else.
pub fn for_grants(node: &mut Node, board: &str, session: &str, asks: &[Asked], policy: &str, task_unknown: bool) -> Result<Findings> {
    let st = standing(node, board)?;
    let (parent, mine) = {
        let hosted = node.host(board)?;
        let parent = hosted.names.get(session).map(|i| i.parent.clone()).unwrap_or_default();
        let mine: Vec<&Grant> = st.grants.iter().filter(|g| g.status != "ignored" && names(&hosted.names, session, &g.issue.to)).collect();
        (parent, mine)
    };
    let routine = asks.iter().all(|a| risk_of(a.action).0 == ROUTINE);
    let (mut out, mut news) = (Vec::new(), Vec::new());
    if mine.is_empty() {
        news.push(format!("No grant you signed has ever named {session}."));
    }
    if !parent.is_empty() {
        out.push(format!("A subagent of {parent} is asking, not a session you started."));
    }
    let mut seen = BTreeSet::new();
    for ask in asks.iter().filter(|a| seen.insert(a.action)) {
        let granted = st.grants.iter().any(|g| g.status != "ignored" && g.issue.action == ask.action)
            || node.approvals.all().iter().any(|a| a.state == APPROVED && a.asks().iter().any(|k| k["action"] == ask.action));
        if !granted {
            news.push(format!("This is the first request for {} on this device.", ask.action));
        }
    }
    for ask in asks {
        if let Some(last) = mine.iter().filter(|g| g.issue.action == ask.action).max_by_key(|g| g.issue.at_ms) {
            let mut more = Vec::new();
            if ask.lasts_ms > last.issue.until_ms - last.issue.at_ms {
                more.push("a longer time");
            }
            if last.issue.uses.is_some_and(|u| ask.uses.is_none_or(|a| a > u)) {
                more.push("more uses");
            }
            if !covers(&last.issue.place.resource, ask.resource) {
                more.push("a wider resource");
            }
            if !more.is_empty() {
                out.push(format!("It asks for more than its last {} grant: {}.", ask.action, more.join(", ")));
            }
        }
        if (ask.resource == "*" || ask.resource.ends_with('*')) && names_a_resource(ask.action) {
            out.push(format!("It asks for {} on {}: a whole class of things, not one.", ask.action, crate::approvals::content::resource_words(ask.resource)));
        }
        if ask.lasts_ms > DAY_MS {
            out.push(format!("The {} grant would last more than a day.", ask.action));
        }
        if ask.action == "set_join_policy" && policy == "secure" && covers(ask.resource, "open") {
            out.push("This would change the pool from secure to open.".into());
        }
    }
    if task_unknown {
        out.push("It names a task that is not on the board.".into());
    }
    let (flags, notes) = if routine { (out, news) } else { ([news, out].concat(), Vec::new()) };
    Ok(Findings { flags, notes })
}
