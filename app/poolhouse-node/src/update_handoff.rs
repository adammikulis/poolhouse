//! Dormant update transition model, not a runtime handoff protocol or quiescence proof.
//! No channel, writer registry, storage lock or production witness issuer exists here.

#[derive(Clone, Debug, PartialEq, Eq)]
struct Intent {
    operation: [u8; 32],
    boot: [u8; 32],
    pid: u32,
    process_start: u64,
    storage_identity: [u8; 32],
    lock_identity: [u8; 32],
    target_digest: [u8; 32],
    source_commit: [u8; 20],
    source_floor: u64,
    target_epoch: u64,
    selection_digest: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Phase {
    Pending,
    Draining,
    WriterFencePending,
    OutcomeUnknown,
    Committed,
    Consumed,
    Cancelled,
    RecoveryRequired,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Refusal {
    BindingMismatch,
    WrongPhase,
    ActiveWork,
    WriterCustodyIncomplete,
    StorageCustodyLost,
    EpochRefused,
}

/// Untrusted model input. Claim-only leases are deliberately absent from active work.
struct Observation {
    compute_leases: usize,
    active_effects: usize,
    active_workers: usize,
    unaccounted_writers: usize,
    storage_custody_retained: bool,
}

/// Retained model data only: possession does not represent a real storage lock.
struct Custody {
    intent: Intent,
    storage_revision: Option<[u8; 32]>,
}

struct Model {
    custody: Custody,
    phase: Phase,
}

/// Deliberately uninhabited in production. Real authenticated channel/writer custody
/// integration needs separate review; no bool/count/JSON can construct this witness.
enum CommitWitness {
    #[cfg(test)]
    Simulated { intent: Intent, revision: [u8; 32] },
}

impl Model {
    fn new(intent: Intent) -> Result<Self, Refusal> {
        if intent.target_epoch < intent.source_floor {
            return Err(Refusal::EpochRefused);
        }
        Ok(Self { custody: Custody { intent, storage_revision: None }, phase: Phase::Pending })
    }

    fn check(&self, intent: &Intent) -> Result<(), Refusal> {
        if self.custody.intent != *intent { return Err(Refusal::BindingMismatch); }
        Ok(())
    }

    fn begin(&mut self, intent: &Intent) -> Result<Phase, Refusal> {
        self.check(intent)?;
        match self.phase {
            Phase::Pending => self.phase = Phase::Draining,
            Phase::Draining => {}, // lost begin acknowledgement is idempotent
            _ => return Err(Refusal::WrongPhase),
        }
        Ok(self.phase)
    }

    /// A request to attempt a fence, never Ready or authority to stop a process.
    fn observe(&mut self, intent: &Intent, seen: Observation) -> Result<Phase, Refusal> {
        self.check(intent)?;
        if self.phase != Phase::Draining { return Err(Refusal::WrongPhase); }
        if !seen.storage_custody_retained {
            self.phase = Phase::RecoveryRequired;
            return Err(Refusal::StorageCustodyLost);
        }
        if seen.unaccounted_writers != 0 { return Err(Refusal::WriterCustodyIncomplete); }
        if seen.compute_leases != 0 || seen.active_effects != 0 || seen.active_workers != 0 {
            return Err(Refusal::ActiveWork);
        }
        self.phase = Phase::WriterFencePending;
        Ok(self.phase)
    }

    /// Only before any fence attempt can a deadline return to a cancelled proposal.
    fn cancel(&mut self, intent: &Intent) -> Result<Phase, Refusal> {
        self.check(intent)?;
        match self.phase {
            Phase::Pending | Phase::Draining => self.phase = Phase::Cancelled,
            Phase::Cancelled => {},
            _ => return Err(Refusal::WrongPhase),
        }
        Ok(self.phase)
    }

    fn acknowledgement_lost(&mut self, intent: &Intent) -> Result<Phase, Refusal> {
        self.check(intent)?;
        if !matches!(self.phase, Phase::WriterFencePending | Phase::Committed | Phase::OutcomeUnknown) {
            return Err(Refusal::WrongPhase);
        }
        self.phase = Phase::OutcomeUnknown;
        Ok(self.phase)
    }

    fn record_commit(&mut self, witness: CommitWitness) -> Result<Phase, Refusal> {
        match witness {
            #[cfg(test)]
            CommitWitness::Simulated { intent, revision } => {
                self.check(&intent)?;
                if !matches!(self.phase, Phase::WriterFencePending | Phase::OutcomeUnknown) {
                    return Err(Refusal::WrongPhase);
                }
                if self.custody.storage_revision.is_some_and(|old| old != revision) {
                    return Err(Refusal::BindingMismatch);
                }
                self.custody.storage_revision = Some(revision);
                self.phase = Phase::Committed;
                Ok(self.phase)
            }
        }
    }

    /// Test model only. Production has no issuer or entry point for consuming custody.
    #[cfg(test)]
    fn consume_simulated(&mut self, intent: &Intent, revision: [u8; 32]) -> Result<Phase, Refusal> {
        self.check(intent)?;
        if self.phase != Phase::Committed { return Err(Refusal::WrongPhase); }
        if self.custody.storage_revision != Some(revision) { return Err(Refusal::BindingMismatch); }
        self.phase = Phase::Consumed;
        Ok(self.phase)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn intent() -> Intent {
        Intent { operation: [1;32], boot: [2;32], pid: 7, process_start: 9,
            storage_identity: [3;32], lock_identity: [4;32], target_digest: [5;32],
            source_commit: [6;20], source_floor: 2, target_epoch: 3, selection_digest: [7;32] }
    }
    fn idle() -> Observation {
        Observation { compute_leases: 0, active_effects: 0, active_workers: 0,
            unaccounted_writers: 0, storage_custody_retained: true }
    }
    fn fenced() -> (Model, Intent) {
        let i = intent(); let mut m = Model::new(i.clone()).unwrap();
        m.begin(&i).unwrap(); m.observe(&i, idle()).unwrap(); (m,i)
    }
    #[test]
    fn wrong_binding_never_advances() {
        let i=intent(); let mut m=Model::new(i.clone()).unwrap();
        let mut cases=vec![];
        let mut x=i.clone(); x.boot[0]^=1; cases.push(x);
        let mut x=i.clone(); x.target_digest[0]^=1; cases.push(x);
        let mut x=i.clone(); x.lock_identity[0]^=1; cases.push(x);
        let mut x=i.clone(); x.operation[0]^=1; cases.push(x);
        let mut x=i.clone(); x.selection_digest[0]^=1; cases.push(x);
        let mut x=i.clone(); x.pid+=1; cases.push(x);
        let mut x=i.clone(); x.process_start+=1; cases.push(x);
        let mut x=i.clone(); x.storage_identity[0]^=1; cases.push(x);
        let mut x=i.clone(); x.source_commit[0]^=1; cases.push(x);
        let mut x=i.clone(); x.source_floor+=1; cases.push(x);
        let mut x=i.clone(); x.target_epoch+=1; cases.push(x);
        for x in cases { assert_eq!(m.begin(&x),Err(Refusal::BindingMismatch)); }
        assert_eq!(m.phase,Phase::Pending);
    }
    #[test]
    fn active_work_and_unknown_writers_stay_draining() {
        let i=intent(); let mut m=Model::new(i.clone()).unwrap(); m.begin(&i).unwrap();
        for kind in 0..4 {
            let mut o=idle(); match kind {0=>o.compute_leases=1,1=>o.active_effects=1,
                2=>o.active_workers=1,_=>o.unaccounted_writers=1};
            assert!(m.observe(&i,o).is_err()); assert_eq!(m.phase,Phase::Draining);
        }
        assert_eq!(m.observe(&i,idle()),Ok(Phase::WriterFencePending));
    }
    #[test]
    fn lost_storage_retains_recovery_instead_of_settling() {
        let i=intent(); let mut m=Model::new(i.clone()).unwrap(); m.begin(&i).unwrap();
        let mut o=idle(); o.storage_custody_retained=false;
        assert_eq!(m.observe(&i,o),Err(Refusal::StorageCustodyLost));
        assert_eq!(m.phase,Phase::RecoveryRequired); assert_eq!(m.custody.intent,i);
        assert_eq!(m.cancel(&i),Err(Refusal::WrongPhase));
        assert_eq!(m.acknowledgement_lost(&i),Err(Refusal::WrongPhase));
    }
    #[test]
    fn ack_loss_retains_binding_and_requires_same_commit_revision() {
        let (mut m,i)=fenced(); m.acknowledgement_lost(&i).unwrap();
        assert_eq!(m.begin(&i),Err(Refusal::WrongPhase));
        assert_eq!(m.consume_simulated(&i,[8;32]),Err(Refusal::WrongPhase));
        m.record_commit(CommitWitness::Simulated{intent:i.clone(),revision:[8;32]}).unwrap();
        assert_eq!(m.consume_simulated(&i,[9;32]),Err(Refusal::BindingMismatch));
        m.acknowledgement_lost(&i).unwrap();
        assert_eq!(m.record_commit(CommitWitness::Simulated{intent:i.clone(),revision:[9;32]}),Err(Refusal::BindingMismatch));
        m.record_commit(CommitWitness::Simulated{intent:i.clone(),revision:[8;32]}).unwrap();
        assert_eq!(m.consume_simulated(&i,[8;32]),Ok(Phase::Consumed));
        assert_eq!(m.consume_simulated(&i,[8;32]),Err(Refusal::WrongPhase));
        assert_eq!(m.acknowledgement_lost(&i),Err(Refusal::WrongPhase));
        assert_eq!(m.record_commit(CommitWitness::Simulated{intent:i,revision:[8;32]}),Err(Refusal::WrongPhase));
    }
    #[test]
    fn commit_observation_cannot_substitute_incarnation_or_target() {
        let (mut m,i)=fenced();
        for target in [false,true] {
            let mut wrong=i.clone();
            if target { wrong.target_digest[0]^=1; } else { wrong.boot[0]^=1; }
            assert_eq!(m.record_commit(CommitWitness::Simulated{intent:wrong,revision:[8;32]}),
                Err(Refusal::BindingMismatch));
            assert_eq!(m.phase,Phase::WriterFencePending);
            assert_eq!(m.custody.storage_revision,None);
        }
    }
    #[test]
    fn deadline_cannot_clear_fenced_or_consumed_custody() {
        let (mut m,i)=fenced(); assert_eq!(m.cancel(&i),Err(Refusal::WrongPhase));
        let mut pending=Model::new(i.clone()).unwrap(); pending.begin(&i).unwrap();
        assert_eq!(pending.cancel(&i),Ok(Phase::Cancelled));
        assert_eq!(pending.begin(&i),Err(Refusal::WrongPhase));
        assert_eq!(pending.acknowledgement_lost(&i),Err(Refusal::WrongPhase));
    }
    #[test]
    fn epoch_floor_refuses_before_drain() {
        let mut i=intent(); i.target_epoch=1;
        assert!(matches!(Model::new(i),Err(Refusal::EpochRefused)));
    }
}
