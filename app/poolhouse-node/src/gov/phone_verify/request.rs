use serde::{Deserialize, Serialize};

use crate::error::Result;
use super::{bad, Act, Binding, GovPool};
use super::super::body::{self, Body};

const REQUEST_WINDOW_MS: u64 = 15 * 60 * 1000;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub id: String,
    pub nonce: String,
    pub created_ms: u64,
    pub expires_ms: u64,
    pub act: Act,
}

pub struct Verified {
    pub(super) request: Request,
    pub(super) bytes: Vec<u8>,
    pub(super) card: String,
}

fn target_fits(parsed: &Body, pool: &GovPool, binding: &Binding) -> Result<bool> {
    let (target, veto) = match parsed {
        Body::Cancel(target) => (target, false),
        Body::Veto(target) => (target, true),
        _ => return Ok(false),
    };
    let Some(held) = pool.acts().get(&target.act) else { return bad("The phone needs the signed change this request cancels.") };
    let (epoch, head) = pool.context();
    let contested = pool.state().chain.dispute.as_ref().is_some_and(|d| d.contested.contains(&target.act));
    let waiting = matches!(pool.status(&target.act), super::super::pool::ActStatus::Pending | super::super::pool::ActStatus::Short(_, _));
    if !held.is_chain() || held.epoch != epoch || held.prev != head || (!waiting && !contested) {
        return bad("That signed change is no longer waiting for approval or cancellation.");
    }
    if veto && !matches!(body::parse(held)?, Body::AdminRemove(remove) if remove.key == binding.public) {
        return bad("Only the strong key named by this removal can veto it.");
    }
    Ok(contested)
}

impl Verified {
    pub fn bytes(&self) -> &[u8] { &self.bytes }
    pub fn card(&self) -> &str { &self.card }
    pub fn request(&self) -> &Request { &self.request }
}

pub(super) fn verify(pool: &GovPool, binding: &Binding, request: Request, now: u64) -> Result<Verified> {
    let hex32 = |text: &str| text.len() == 32 && text.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
    if !hex32(&request.id) || !hex32(&request.nonce) || request.created_ms == 0 || request.created_ms > now || now >= request.expires_ms
        || request.expires_ms.checked_sub(request.created_ms).is_none_or(|n| n == 0 || n > REQUEST_WINDOW_MS) {
        return bad("This signing request has expired or is not valid on this phone.");
    }
    let act = &request.act;
    act.check_shape()?;
    let (epoch, head) = pool.context();
    if act.pool != pool.id || act.epoch != epoch || act.prev != head || act.at_ms > now || now >= act.not_after || act.signatures.iter().any(|s| s.key == binding.public) {
        return bad("This signing request does not name the pool this phone currently verifies.");
    }
    let charter = &pool.state().chain.latest().charter;
    if !charter.is_strong(&binding.public) {
        return bad("This phone key cannot sign under the pool's current governance.");
    }
    let parsed = body::parse(act)?;
    let resolving = target_fits(&parsed, pool, binding)?;
    if pool.state().chain.dispute.is_some() && !resolving {
        return bad("Only a cancellation or veto of the contested changes can be signed during this dispute.");
    }
    if matches!(parsed, Body::Found(_) | Body::Leave(_)) {
        return bad("This request requires a different kind of signature.");
    }
    if matches!(&parsed, Body::AdminAdd(add) if add.entry.strength == super::super::keys::Strength::Strong) {
        return bad("The phone needs verified proof that this new admin key requires its owner's approval for every signature.");
    }
    if act.is_chain() {
        if let Err(why) = super::super::chain::proposal_fits(&pool.state().chain, &pool.id, act) { return bad(&why) }
    }
    let card = super::card::render(act, pool, binding)?;
    Ok(Verified { bytes: act.sign_bytes(), card, request })
}
