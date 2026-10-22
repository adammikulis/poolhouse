//! Adding a phone to a device in a pool. The phone is listed as an admin key of the person whose phone approves it: it accepts being listed
//! when it is added, the approving phone signs the `admin_add` act with its approval, and the node hands the act to the pool at that tap.

use serde_json::{json, Value};

use crate::approvals::content::ADD_SCOPE;
use crate::approvals::Approval;
use crate::error::{Error, Result};
use crate::gov::act::{Act, ActSig, SigKind};
use crate::gov::keys::{AdminKey, Alg, Role, Strength};
use crate::gov::MAX_ADMINS;
use crate::grants::authority::Authority;
use crate::node::Node;
use crate::poolops::wall_ms;

/// Why a phone that no admin key lists is refused where no admin phone is connected.
pub const NO_ADMIN_PHONE: &str = "An admin's phone adds this phone; none is connected here.";
const NO_PERSON: &str = "More than one person's admin phone is added here: ask for the code from the phone of the person this one is for.";
const OLD_APP: &str = "This pool lists the phones that approve for it, and this app did not accept being listed: update the Poolhouse app, then scan a new code.";
const STALE: &str = "This phone did not accept being listed under the terms its code carries, so it was not added: ask for a new code.";
const SECOND_SIGNATURE: &str = "Adding this phone as an admin takes a second signature from the approving phone, and it is missing or does not match, so nothing was approved.";

/// The person a code made now is for: the person of the phone that made it, or the one person whose admin phones this device holds.
pub fn person_for(node: &Node, maker: Option<&str>) -> String {
    let authority = Authority::of(node);
    maker.and_then(|fp| authority.person_of(fp)).map(str::to_string).or_else(|| authority.sole_person()).unwrap_or_default()
}

/// What a code carries so that a phone can accept being listed: the pool, the charter its listing follows, the epoch it would join at and the
/// person it would belong to. None on a device in no pool, and for a code that names no person.
pub fn terms(node: &Node, person: &str) -> Option<Value> {
    let gov = node.members.gov().filter(|_| !person.is_empty())?;
    let (epoch, prev) = gov.context();
    Some(json!({"pool": gov.id, "prev": prev, "since": epoch + 1, "person": person}))
}

/// The `admin_add` act that lists the phone ``key`` named ``name`` as an admin key of ``person``, once the key's own ``acceptance`` of it checks.
pub fn draft(node: &Node, person: &str, name: &str, key: &str, acceptance: &str) -> Result<Act> {
    let gov = node.members.gov().ok_or_else(|| Error::Invalid("This device is in no pool.".into()))?;
    if person.is_empty() {
        return Err(Error::Denied(NO_PERSON.into()));
    }
    if acceptance.is_empty() {
        return Err(Error::Denied(OLD_APP.into()));
    }
    let (epoch, prev) = gov.context();
    let charter = &gov.state().chain.latest().charter;
    if charter.admins.len() >= MAX_ADMINS {
        return Err(Error::Denied("A pool names at most sixteen keys, so no phone is added to it.".into()));
    }
    if charter.admin(key).is_some() {
        return Err(Error::Denied("That key is already listed in this pool, so it is not listed again.".into()));
    }
    let entry = AdminKey { key: key.into(), alg: Alg::P256, role: Role::Admin, strength: Strength::Strong, person: person.into(), since: epoch + 1, acceptance: acceptance.into(), label: name.into() };
    if entry.check().is_err() || !entry.accepted(&gov.id, &prev) {
        return Err(Error::Denied(STALE.into()));
    }
    gov.new_act("admin_add", json!({"entry": entry}), wall_ms())
}

/// The request's text for a phone that is listed: what the person signs, the hold, and the person it is listed for.
pub fn card(node: &Node, text: &str, act: &Act) -> String {
    let hours = node.members.gov().map_or(0, |g| g.state().chain.latest().charter.holds.admin_add_h);
    let person = act.body["entry"]["person"].as_str().unwrap_or("");
    let scope = format!("SCOPE: That phone is added to this device now and listed as an admin key of {person}. It counts as an admin only after a hold of {hours} hours, and until then it approves nothing; another admin can cancel it within the hold. You sign twice: this approval, and the listing.");
    text.replacen(ADD_SCOPE, &scope, 1)
}

fn act_of(a: &Approval) -> Option<Act> {
    a.action.get("act").and_then(|v| serde_json::from_value(v.clone()).ok())
}

/// Whether the key ``key`` may approve the request ``a`` with the second signature ``act_sig``: it belongs to the person the listing is for, and
/// the signature is its own over the act. A request that lists nothing needs neither.
pub fn check_decision(node: &Node, a: &Approval, key: &str, act_sig: &str) -> Result<()> {
    let Some(act) = act_of(a) else { return Ok(()) };
    let person = act.body["entry"]["person"].as_str().unwrap_or("");
    if Authority::of(node).person_of(key) != Some(person) {
        return Err(Error::Denied(format!("Only an admin phone of {person} approves a phone that is listed for {person}, so nothing was decided.")));
    }
    let public = node.persons.get(key).map(|k| k.public.clone()).unwrap_or_default();
    if !Alg::P256.verifies(&public, &act.sign_bytes(), act_sig) {
        return Err(Error::Denied(SECOND_SIGNATURE.into()));
    }
    Ok(())
}

/// Hand the pool the act of the request ``a`` with the second signature of ``key``: the hold starts now.
pub fn submit(node: &mut Node, a: &Approval, key: &str, act_sig: &str) -> Result<()> {
    let Some(mut act) = act_of(a) else { return Ok(()) };
    let public = node.persons.get(key).map(|k| k.public.clone()).unwrap_or_default();
    act.add_signature(ActSig { kind: SigKind::Admin, key: public, sig: act_sig.into() });
    let sent = node.pool_submit(&serde_json::to_value(&act)?)?;
    if matches!(sent["status"].as_str(), Some("void" | "stale")) {
        return Err(Error::Denied(sent["why"].as_str().unwrap_or("the pool did not take the listing").into()));
    }
    let _ = node.record_event("phone.admit", &act.id(), &format!("{}; listed by {}", a.action["name"].as_str().unwrap_or(""), key.get(..12).unwrap_or("")));
    Ok(())
}
