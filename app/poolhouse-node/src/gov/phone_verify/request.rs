use serde::{Deserialize, Serialize};

use crate::error::Result;
use crate::row::canonical;
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
    if !charter.is_strong(&binding.public) || pool.state().chain.dispute.is_some() {
        return bad("This phone key cannot sign under the pool's current governance.");
    }
    if matches!(body::parse(act)?, Body::Found(_) | Body::Leave(_)) {
        return bad("This request requires a different kind of signature.");
    }
    let statement = super::super::describe::describe(act, pool);
    let card = format!("{statement}\n\nPhone-verified pool terms (epoch {epoch}):\n{}\n\nExact change:\n{}\n\nRequest {}\nExpires {}\nGovernance evidence is what this phone has observed; the computer checks current authority again before applying this signature.",
        canonical(&serde_json::to_value(charter)?), canonical(&act.unsigned()), request.id, request.expires_ms);
    Ok(Verified { bytes: act.sign_bytes(), card, request })
}
