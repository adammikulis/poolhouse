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
//! | `book` | the grant rows of a board, which of them count, and their state now |
//! | `calls` | `grant_issue`, `grant_revoke`, `grants`, `grant_log`, `person_enroll`, `person_keys` |

pub mod book;
pub mod calls;
pub mod person;
pub mod spec;

use serde_json::json;

use crate::error::{Error, Result};
use crate::identity::{Holder, Ident, ModelState};
use crate::node::Node;
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

/// Whether the grantee ``to`` names the session ``name`` whose identity is ``who``.
pub fn names(who: &Ident, name: &str, to: &Grantee) -> bool {
    match to {
        Grantee::Session(n) => n == name,
        Grantee::Class { family, main, verified } => who.family == *family && (!main || who.parent.is_empty()) && (!verified || who.model_state == ModelState::Verified),
        Grantee::Account { model } => who.model == *model && who.model_state == ModelState::Verified,
    }
}

/// The id of the live grant that lets ``holder`` take ``action`` on ``scope`` now, if one does.
pub fn find(node: &mut Node, holder: &Holder, action: &str, scope: &Scope) -> Result<Option<String>> {
    grantable(action)?;
    let Some(who) = node.host(&holder.board)?.names.get(&holder.name).filter(|i| !i.retired).cloned() else { return Ok(None) };
    let device = node.cert.fingerprint();
    let standing = book::standing(node, &holder.board)?;
    Ok(standing.grants.iter().find(|g| {
        let (i, p) = (&g.issue, &g.issue.place);
        g.status == "live" && i.action == action && names(&who, &holder.name, &i.to) && p.board == holder.board && (p.device == "*" || p.device == device)
            && covers(&p.resource, scope.resource) && (!i.review || scope.reviewed)
    }).map(|g| g.issue.gid.clone()))
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
/// error names what a person's client would sign to allow it.
pub fn refused_for(node: &Node, token: &str, action: &str, resource: &str, message: &str) -> Error {
    match node.tokens.resolve(token) {
        Some(h) => Error::NoGrant {
            message: message.into(),
            grant: json!({"action": action, "resource": resource, "board": h.board, "name": h.name, "device": node.cert.fingerprint()}),
        },
        None => Error::Denied(message.into()),
    }
}
