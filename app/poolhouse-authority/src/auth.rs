use crate::{Error, Hash};

#[allow(unsafe_code)]
mod native;
mod policy;

#[derive(Clone, Debug, PartialEq, Eq)]
struct Context {
    digest: Hash,
    head: Hash,
    generation: u64,
    nonce: Hash,
    expires_at: u64,
    owner_uid: u32,
    audit_session: u32,
    gui_hash: Hash,
}

struct VerifiedCard(Context);
struct VerifiedCaller {
    owner_uid: u32,
    audit_session: u32,
    gui_hash: Hash,
}
pub(crate) struct PersonApproval(Context);
impl PersonApproval {
    /// A sealed approval can authorize only the immutable phone effect displayed
    /// for its exact prepared authority revision. This does not issue approvals.
    pub(crate) fn matches_phone_bootstrap(self, receipt: &crate::receipt::PhoneBootstrapReceipt, now: u64) -> bool {
        receipt.decision_digest().is_ok_and(|digest| digest == self.0.digest)
            && self.0.head == receipt.prepared_envelope_head && self.0.generation == receipt.prepared_envelope_generation
            && self.0.nonce == receipt.nonce && now > 0 && now < self.0.expires_at && self.0.expires_at <= receipt.offer_expires
    }

    #[cfg(test)]
    pub(crate) fn fixture_phone(receipt: &crate::receipt::PhoneBootstrapReceipt) -> Self {
        Self(Context { digest: receipt.decision_digest().unwrap(), head: receipt.prepared_envelope_head,
            generation: receipt.prepared_envelope_generation, nonce: receipt.nonce, expires_at: receipt.offer_expires,
            owner_uid: 501, audit_session: 7, gui_hash: [8; 32] })
    }
}
struct ExternalForm(Box<[u8; 32]>);
impl Drop for ExternalForm {
    fn drop(&mut self) {
        native::wipe(self.0.as_mut());
    }
}

trait Api {
    type Reference;
    fn create_fresh(&self) -> Result<Self::Reference, Error>;
    fn externalize(&self, reference: &Self::Reference) -> Result<ExternalForm, Error>;
    fn import(&self, form: &ExternalForm) -> Result<Self::Reference, Error>;
    fn policy(&self) -> Result<serde_json::Value, Error>;
    fn identity(&self) -> &policy::RuleIdentity;
    fn request(&self, reference: &Self::Reference) -> Result<(), Error>;
    fn destroy(&self, reference: Self::Reference) -> Result<(), Error>;
}

struct GuiSession<'a, A: Api> {
    api: &'a A,
    reference: Option<A::Reference>,
    context: Context,
    exported: bool,
}
impl<'a, A: Api> GuiSession<'a, A> {
    fn after_card(api: &'a A, card: VerifiedCard) -> Result<Self, Error> {
        Ok(Self {
            api,
            reference: Some(api.create_fresh()?),
            context: card.0,
            exported: false,
        })
    }
    fn export(&mut self, context: &Context) -> Result<ExternalForm, Error> {
        if self.exported || *context != self.context {
            return Err(Error::WrongBinding);
        }
        self.exported = true;
        self.api
            .externalize(self.reference.as_ref().ok_or(Error::Unauthorized)?)
    }
    fn finish(mut self) -> Result<(), Error> {
        self.api
            .destroy(self.reference.take().ok_or(Error::Unauthorized)?)
    }
}
impl<A: Api> Drop for GuiSession<'_, A> {
    fn drop(&mut self) {
        if let Some(reference) = self.reference.take() {
            let _ = self.api.destroy(reference);
        }
    }
}

fn authorize<A: Api>(
    api: &A,
    form: ExternalForm,
    pending: &Context,
    caller: &VerifiedCaller,
    now: u64,
    current: impl FnOnce() -> Result<(Context, u64), Error>,
) -> Result<PersonApproval, Error> {
    if now >= pending.expires_at
        || caller.owner_uid != pending.owner_uid
        || caller.audit_session != pending.audit_session
        || caller.gui_hash != pending.gui_hash
    {
        return Err(Error::Unauthorized);
    }
    policy::verify(&api.policy()?, api.identity())?;
    let reference = api.import(&form)?;
    let granted = api.request(&reference);
    let destroyed = api.destroy(reference);
    granted?;
    destroyed?;
    let (state, finished) = current()?;
    if finished < now || finished >= pending.expires_at {
        return Err(Error::Expired);
    }
    if state != *pending {
        return Err(Error::StaleState);
    }
    Ok(PersonApproval(state))
}

#[cfg(test)]
mod tests;
