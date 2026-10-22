//! Distrust rows: an admin-role key of the charter in force cuts another key the charter lists off from granting and deciding, at once, on
//! every board. A row only narrows: the one reader of the set it makes subtracts a key from the authority keys.

use std::collections::BTreeSet;

use serde_json::{Map, Value};

use crate::gov::keys::Role;
use crate::gov::pool::GovPool;
use crate::node::Node;
use crate::poolops::POOL_BOARD;

use super::person::verifies_with;
use super::spec::{parse_distrust, signing_bytes};

/// The key the `distrust` row ``map`` cuts off, or why it counts for nothing: it is signed by an admin-role key of the charter in force and the
/// signature verifies under the charter's key, it is for this pool, and it names a key the charter in force lists, at the epoch that key was listed.
pub fn subject(gov: &GovPool, map: &Map<String, Value>) -> Result<String, &'static str> {
    let named = parse_distrust(map).map_err(|_| "the row is not a distrust")?;
    if named.pool != gov.id {
        return Err("it is for another pool");
    }
    let charter = &gov.state().chain.latest().charter;
    let signer = charter.admins.iter().find(|k| k.role == Role::Admin && k.fingerprint() == named.person).ok_or("it is signed by a key that is not an admin key of this pool now")?;
    if !verifies_with(signer.alg.name(), &signer.key, &signing_bytes(map), map.get("sig").and_then(Value::as_str).unwrap_or("")) {
        return Err("the signature is not that admin key's");
    }
    let target = charter.admins.iter().find(|k| k.fingerprint() == named.key).ok_or("that key is not listed by this pool now")?;
    if target.since == named.since { Ok(named.key) } else { Err("that key was listed again since") }
}

/// The fingerprints of the keys the distrust rows of the pool board cut off now.
pub fn set(node: &Node) -> BTreeSet<String> {
    let (Some(gov), Some(hosted)) = (node.members.gov(), node.boards.get(POOL_BOARD)) else { return BTreeSet::new() };
    let floor = node.members.grants_floor_ms;
    hosted.book.distrusts(&hosted.board).into_iter().filter(|r| r.hlc.0 > floor)
        .filter_map(|r| r.body.as_object().and_then(|m| subject(gov, m).ok())).collect()
}
