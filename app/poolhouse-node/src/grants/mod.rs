//! Standing grants: what a session may do that no session may do by default.
//!
//! A person signs a grant once with a key enrolled on the node (`person`); it lands on the board as a `grant` entry
//! naming who may take which action where, until when and how often (`spec`). Every use of one writes an entry
//! saying who used it for what, and the person revokes it with a second signed entry. Nothing is allowed that no
//! grant names, and the floors in `spec::FLOORS` are not grantable. `book` folds the entries; `calls` is the API.
//!
//! | piece | does |
//! |---|---|
//! | `spec` | the action registry, the floors, the shape of a grant, the bytes a person signs |
//! | `person` | the person keys enrolled on this node and the signature check |
//! | `authority` | which key may sign what: any enrolled key on an ungoverned device, the pool's admin keys in a pool |
//! | `book` | the grant rows of a board, which of them count, and their state now |
//! | `distrust` | the rows that cut an admin key off from granting and deciding |
//! | `calls` | `grant_issue`, `grant_revoke`, `grant_distrust`, `grants`, `grant_log`, `person_enroll`, `person_keys` |

pub mod authority;
pub mod book;
pub mod calls;
pub mod distrust;
pub mod person;
pub mod spec;

use serde_json::json;

use crate::error::{Error, Result};
use crate::identity::{Holder, ModelState, Registry};
use crate::lineage::{running, shares, Refusal, ENFORCED};
use crate::node::{Node, NOT_KNOWN};
use crate::row::Kind;

use spec::{covers, grantable, Grantee};

/// What a use of a grant is about: the thing it is used on, and whether an independent review stands behind it.
pub struct Scope<'a> {
    pub resource: &'a str,
    pub reviewed: bool,
}

impl<'a> Scope<'a> {
    /// A use on ``resource`` with no independent review behind it.
    pub fn on(resource: &'a str) -> Scope<'a> {
        Scope { resource, reviewed: false }
    }

    /// A use that names no particular thing.
    pub fn any() -> Scope<'static> {
        Scope::on("*")
    }
}

/// How far up the parent links a grant to a session's subagents reaches.
const MOST_DEPTH: usize = 16;

/// Whether ``name`` has ``top`` among its ancestors in ``registry``.
fn under(registry: &Registry, name: &str, top: &str) -> bool {
    let mut at = name;
    for _ in 0..MOST_DEPTH {
        at = match registry.get(at) {
            Some(i) if !i.parent.is_empty() => i.parent.as_str(),
            _ => return false,
        };
        if at == top {
            return true;
        }
    }
    false
}

/// Whether the grantee ``to`` names the session ``name`` of the board whose sessions ``registry`` holds.
pub fn names(registry: &Registry, name: &str, to: &Grantee) -> bool {
    let Some(who) = registry.get(name) else { return false };
    match to {
        Grantee::Session { name: n, subagents } => n == name || (*subagents && under(registry, name, n)),
        Grantee::Class { family, main, verified } => who.family == *family && (!main || who.parent.is_empty()) && (!verified || who.model_state == ModelState::Verified),
        Grantee::Account { model } => who.model == *model && who.model_state == ModelState::Verified,
    }
}

/// Why the process asking may not spend what ``holder`` holds, when it may not: the token it presented was registered from a lineage it
/// shares (a token is a bearer secret in a file, and what it spends is bound to where it was registered), and, for a main session, no process
/// of the lineage the session is registered from still runs without it sharing that lineage with the asker, so one live agent cannot spend
/// what a live sibling holds. A token that names no lineage, or another session than ``holder``, or no request at all, is not held to it here.
/// Both refusals end by themselves: a stale token registers its session again (`Refusal::Ended`), and the live sibling's lineage ending
/// leaves the session to the one that is still running (`Refusal::Sibling`).
fn lineage_refusal(node: &Node, holder: &Holder) -> Option<Refusal> {
    if !ENFORCED {
        return None;
    }
    let ask = &node.asking;
    let (who, bound) = node.tokens.bound(&ask.token)?;
    if who != holder {
        return None;
    }
    let here = ask.peer.lineage();
    if !shares(bound, &here) {
        return Some(if running(bound) { Refusal::Sibling } else { Refusal::Ended });
    }
    let own = node.boards.get(&holder.board).and_then(|h| h.names.get(&holder.name)).filter(|i| i.parent.is_empty());
    own.filter(|i| running(&i.lineage) && !shares(&i.lineage, bound)).map(|_| Refusal::Sibling)
}

/// The id of the live grant that lets ``holder`` take ``action`` on ``scope`` now, if one does and the token asking is used from
/// the lineage that registered it.
pub fn find(node: &mut Node, holder: &Holder, action: &str, scope: &Scope) -> Result<Option<String>> {
    grantable(action)?;
    if node.host(&holder.board)?.names.get(&holder.name).is_none_or(|i| i.retired) {
        return Ok(None);
    }
    let device = node.cert.fingerprint();
    let standing = book::standing(node, &holder.board)?;
    let registry = &node.host(&holder.board)?.names;
    let found = standing.grants.iter().find(|g| {
        let (i, p) = (&g.issue, &g.issue.place);
        g.status == "live" && i.action == action && names(registry, &holder.name, &i.to) && p.board == holder.board && (p.device == "*" || p.device == device)
            && covers(&p.resource, scope.resource) && (!i.review || scope.reviewed)
    }).map(|g| g.issue.gid.clone());
    if found.is_some() {
        if let Some(why) = lineage_refusal(node, holder) {
            node.asking.refused = Some(why);
            return Ok(None);
        }
    }
    Ok(found)
}

/// Whether ``holder`` holds a grant for ``action`` on ``scope``; nothing is spent or written.
pub fn holds(node: &mut Node, holder: &Holder, action: &str, scope: &Scope) -> Result<bool> {
    Ok(find(node, holder, action, scope)?.is_some())
}

/// Use a grant: true, with the use written to the board, when ``holder`` holds one for ``action`` on ``scope``.
pub fn check(node: &mut Node, holder: &Holder, action: &str, scope: &Scope) -> Result<bool> {
    let Some(gid) = find(node, holder, action, scope)? else { return Ok(false) };
    let shown: String = scope.resource.chars().filter(char::is_ascii_graphic).take(120).collect();
    let target = if shown.is_empty() { "-".to_string() } else { shown };
    let body = json!({"op": "use", "gid": gid, "action": action, "target": target, "user": holder.name});
    crate::fold::check_local(Kind::Grant, &holder.name, &body, "")?;
    node.host(&holder.board)?.board.append(Kind::Grant, &holder.name, body, "")?;
    Ok(true)
}

/// The refusal for the session behind ``token`` that holds no grant for ``action`` on ``resource``: ``message`` says why, and the
/// error names what a person's client would sign to allow it. A grant the session holds that this request could not spend says why
/// in words that need nobody to do anything: a stale token is answered as an unknown one, which its client replaces by registering
/// the session again, and a live sibling is told the grant is spent where the session runs.
pub fn refused_for(node: &Node, token: &str, action: &str, resource: &str, message: &str) -> Error {
    let holder = node.tokens.resolve(token);
    match (node.asking.refused, holder) {
        (Some(Refusal::Ended), _) => Error::Denied(format!("{NOT_KNOWN}: it was registered from a terminal or process tree that has ended, so this process registers the session \
                                                           again and gets a token of its own ({message})")),
        (Some(Refusal::Sibling), holder) => Error::Denied(format!("{message}: the grant is held by {}, which is in use in another terminal or process tree that is still \
                                                                  running. A grant is spent only where its session runs, so a second live process cannot spend it; \
                                                                  it is spent from there, and from here as soon as that one ends. Nothing needs to be run.",
                                                                  holder.map_or("this session", |h| h.name.as_str()))),
        (None, Some(h)) => Error::NoGrant {
            message: message.into(),
            grant: json!({"action": action, "resource": resource, "board": h.board, "name": h.name, "device": node.cert.fingerprint()}),
        },
        (None, None) => Error::Denied(message.into()),
    }
}
