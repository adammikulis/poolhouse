use super::*;
use proof::{Caller, Cleanup, CleanupOutcome, Completion, Held};

pub(super) enum Consume { Execute(Execution), Pending, Recorded(Vec<u8>) }
impl Registry {
    pub(super) fn binding(&self, right: &Reservation) -> Result<Binding, Error> {
        Ok(self.lock()?.invocations.get(&right.key).ok_or(Error::Stale)?.binding.clone())
    }
    pub(super) fn bind(&self, owner: &SessionRight, right: &Reservation, held: Held) -> Result<(), BindFailure> {
        // Keep ownership outside the validation closure so every failure returns the held child.
        let checked = (|| {
            let mut store = self.lock()?;
            let item = store.invocations.get(&right.key).ok_or(Error::Stale)?;
            if item.binding.session != owner.key { return Err(Error::Mismatch); }
            if item.state != InvocationState::Reserved { return Err(Error::State); }
            let valid = store.live(owner.key).is_ok_and(|session| {
                let body = held.body();
                body.binding == item.binding && Arc::ptr_eq(&body.producer, &session.producer)
                    && body.process.live() && body.holder.live() && body.completion_owner.live()
            });
            if !valid {
                store.invocations.get_mut(&right.key).unwrap().state = InvocationState::Invalidated;
                return Err(Error::Mismatch);
            }
            Ok(store)
        })();
        match checked {
            Err(error) => Err(BindFailure { error, held }),
            Ok(mut store) => {
                let item = store.invocations.get_mut(&right.key).unwrap();
                item.held = Some(Arc::new(held)); item.state = InvocationState::Bound; Ok(())
            },
        }
    }
    pub(super) fn release(&self, owner: &SessionRight, right: &Reservation, binding: &Binding) -> Result<(), Error> {
        let mut store = self.lock()?;
        store.check_live(right.key, owner.key, binding)?;
        let item = store.invocations.get_mut(&right.key).unwrap();
        if item.state != InvocationState::Bound { return Err(Error::State); }
        item.state = InvocationState::Released;
        Ok(())
    }
    pub(super) fn cleanup(&self, receipt: Cleanup) -> Result<CleanupState, Error> {
        let mut store = self.lock()?;
        let item = store.invocations.get_mut(&receipt.binding().invocation).ok_or(Error::Stale)?;
        let held = item.held.as_ref().ok_or(Error::State)?.body();
        if receipt.binding() != &item.binding || receipt.phase() != item.cleanup_phase
            || !Arc::ptr_eq(receipt.process(), &held.process)
            || !Arc::ptr_eq(receipt.holder(), &held.holder) || !held.holder.live() {
            return Err(Error::Mismatch);
        }
        item.cleanup = match receipt.outcome() { CleanupOutcome::Closed => CleanupState::Closed,
            CleanupOutcome::Failed => CleanupState::Failed };
        Ok(item.cleanup)
    }
    pub(super) fn consume(&self, owner: &SessionRight, right: &Reservation, caller: &Caller,
               binding: &Binding) -> Result<Consume, Error> {
        let mut store = self.lock()?;
        // Terminal retries validate the same immutable caller/binding, but never issue another permit.
        let item = store.invocations.get(&right.key).ok_or(Error::Stale)?;
        if item.binding.session != owner.key || &item.binding != binding || caller.binding() != binding {
            return Err(Error::Mismatch);
        }
        let held = item.held.as_ref().ok_or(Error::State)?.body();
        if !Arc::ptr_eq(caller.process(), &held.process) { return Err(Error::Mismatch); }
        store.check_live(right.key, owner.key, binding)?;
        if item.state == InvocationState::Consumed {
            return Ok(match &item.result { Some(result) => Consume::Recorded(result.clone()), None => Consume::Pending });
        }
        let item = store.invocations.get(&right.key).unwrap();
        if item.state != InvocationState::Released { return Err(Error::State); }
        if item.cleanup != CleanupState::Closed { return Err(Error::Cleanup); }
        let permit = store.allocate()?;
        let item = store.invocations.get_mut(&right.key).unwrap();
        item.state = InvocationState::Consumed;
        item.permit = Some(permit);
        Ok(Consume::Execute(Execution { invocation: right.key, permit }))
    }
    pub(super) fn complete(&self, execution: &Execution, receipt: Completion) -> Result<Vec<u8>, Error> {
        let mut store = self.lock()?;
        let item = store.invocations.get_mut(&execution.invocation).ok_or(Error::Stale)?;
        let held = item.held.as_ref().ok_or(Error::State)?.body();
        if item.state != InvocationState::Consumed || item.permit != Some(execution.permit)
            || receipt.permit() != execution.permit || receipt.binding() != &item.binding
            || !Arc::ptr_eq(receipt.owner(), &held.completion_owner) { return Err(Error::Mismatch); }
        if let Some(result) = &item.result {
            if result != receipt.result() { return Err(Error::Mismatch); }
            return Ok(result.clone());
        }
        item.result = Some(receipt.result().to_vec());
        Ok(item.result.as_ref().unwrap().clone())
    }
    pub(super) fn cancel(&self, owner: &SessionRight, right: &Reservation) -> Result<Option<Termination>, Error> {
        let mut store = self.lock()?;
        let item = store.invocations.get_mut(&right.key).ok_or(Error::Stale)?;
        if item.binding.session != owner.key { return Err(Error::Mismatch); }
        if matches!(item.state, InvocationState::Consumed | InvocationState::Cancelled | InvocationState::Invalidated) {
            return Err(Error::State);
        }
        // Authorization is revoked under the same lock before a holder receives a request.
        item.state = InvocationState::Cancelled;
        Ok(item.obligation())
    }
    pub(super) fn retire(&self, owner: &SessionRight) -> Result<Vec<Termination>, Error> {
        let mut store = self.lock()?;
        if !store.sessions.contains_key(&owner.key) { return Err(Error::Stale); }
        let mut retired = HashSet::from([owner.key]);
        loop {
            let before = retired.len();
            for (key, session) in &store.sessions {
                if session.context.parent.is_some_and(|parent| retired.contains(&parent)) { retired.insert(*key); }
            }
            if before == retired.len() { break; }
        }
        for key in &retired { store.sessions.get_mut(key).unwrap().state = SessionState::Retired; }
        let mut obligations = Vec::new();
        for item in store.invocations.values_mut() {
            if retired.contains(&item.binding.session) && item.outstanding() {
                item.state = InvocationState::Invalidated;
                if let Some(request) = item.obligation() { obligations.push(request); }
            }
        }
        Ok(obligations)
    }
    /// Detect lost exact lifetimes; no old invocation can be assigned a replacement process.
    pub(super) fn reconcile(&self) -> Result<Vec<Termination>, Error> {
        let mut store = self.lock()?;
        let lost: Vec<Key> = store.invocations.iter().filter_map(|(key, item)| {
            let live = store.live(item.binding.session).is_ok() && item.held.as_ref().is_none_or(|held| {
                let body = held.body(); body.process.live() && body.holder.live() && body.completion_owner.live()
            });
            (item.outstanding() && !live).then_some(*key)
        }).collect();
        let mut obligations = Vec::new();
        for key in lost {
            let item = store.invocations.get_mut(&key).unwrap();
            item.state = InvocationState::Invalidated;
            if let Some(request) = item.obligation() { obligations.push(request); }
        }
        Ok(obligations)
    }
}
impl Store {
    fn check_live(&self, key: Key, owner: Key, binding: &Binding) -> Result<(), Error> {
        let item = self.invocations.get(&key).ok_or(Error::Stale)?;
        self.live(owner)?;
        let held = item.held.as_ref().ok_or(Error::State)?.body();
        if item.binding.session != owner || &item.binding != binding { return Err(Error::Mismatch); }
        if !held.process.live() || !held.holder.live() || !held.completion_owner.live() { return Err(Error::Stale); }
        Ok(())
    }
}
impl Invocation {
    fn outstanding(&self) -> bool {
        matches!(self.state, InvocationState::Reserved | InvocationState::Bound | InvocationState::Released)
    }
    fn obligation(&mut self) -> Option<Termination> {
        if let Some(held) = &self.held {
            // Prior startup cleanup does not prove cancellation-time channel closure/termination.
            self.cleanup = CleanupState::Pending;
            self.cleanup_phase = CleanupPhase::Terminal;
            Some(Termination { invocation: self.binding.invocation, binding: self.binding.clone(), held: held.clone() })
        } else { self.cleanup = CleanupState::Closed; None }
    }
}
