//! Private pending DATA bookkeeping only: no authenticated owner, source custody,
//! resource admission, execution, ready state or launch fence is issued here.
use std::fs::File;
use super::update_activity::Registry;
use serde::{Deserialize, Serialize};
use crate::contract::{JobInstallationRef, JobManagedTestUnitIntent, JobPreparationDigest, RequestId, ResourceGeneration};

mod store;
mod allocations;
#[cfg(test)] mod tests;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct OwnerObservation {
    pub principal_digest: JobPreparationDigest,
    pub incarnation: ResourceGeneration,
    pub installation: JobInstallationRef,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all="snake_case")]
pub(super) enum PendingState { PendingSourceAdmission, CancelledPending }
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Record {
    pub owner: OwnerObservation,
    pub request: RequestId,
    pub intent: JobManagedTestUnitIntent,
    pub revision: u64,
    pub state: PendingState,
}
#[derive(Debug)]
pub(super) enum Failure { Invalid, Conflict, Io(std::io::ErrorKind), Unavailable, PersistenceUnknown }
#[derive(Debug, PartialEq, Eq)]
pub(super) enum Remembered { Created, Attached }

pub(super) struct Book {
    store: store::Store,
    activity: Registry,
    records: Vec<Record>,
    allocations: Option<allocations::Authority>,
    allocation_projection:Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
    // Preserve both alternatives after uncertain publication; never mutate again.
    uncertain: Option<(Vec<Record>,Option<allocations::Authority>)>,
}
impl Book {
    pub(super) fn open(parent: File, activity:Registry) -> Result<Self, Failure> {
        let _writer=activity.local_writer().map_err(|_|Failure::Unavailable)?;
        let store=store::Store::open(parent)?;
        Self::from_store(store,activity)
    }
    pub(super) fn open_existing(parent:File,activity:Registry)->Result<Self,Failure> {
        Self::from_store(store::Store::open_existing(parent)?,activity)
    }
    fn from_store(store:store::Store,activity:Registry)->Result<Self,Failure> {
        let (records,allocations)=store.read_snapshot()?;
        validate(&records)?;
        if let Some(a)=&allocations {a.validate(&records)?;}
        Ok(Self { store,activity,records,allocations,allocation_projection:None,uncertain:None })
    }
    pub(super) fn remember_pending(&mut self, owner: OwnerObservation, request: RequestId,
        intent: JobManagedTestUnitIntent) -> Result<Remembered, Failure> {
        self.available()?;
        if owner.installation != intent.source.installation { return Err(Failure::Invalid); }
        if let Some(old)=self.records.iter().find(|r| r.owner==owner && r.request==request) {
            return if old.intent==intent { Ok(Remembered::Attached) } else { Err(Failure::Conflict) };
        }
        let mut candidate=self.records.clone();
        candidate.push(Record { owner,request,intent,revision:0,state:PendingState::PendingSourceAdmission });
        let _writer=self.activity.local_writer().map_err(|_|Failure::Unavailable)?;
        self.publish(candidate)?;
        Ok(Remembered::Created)
    }
    pub(super) fn inspect_pending(&self, owner: &OwnerObservation, request: &RequestId) -> Result<Option<&Record>,Failure> {
        self.available()?;
        Ok(self.records.iter().find(|r| &r.owner==owner && &r.request==request))
    }
    pub(super) fn cancel_pending(&mut self, owner: &OwnerObservation, request: &RequestId, revision:u64) -> Result<(),Failure> {
        self.available()?;
        let index=self.records.iter().position(|r| &r.owner==owner && &r.request==request).ok_or(Failure::Conflict)?;
        let old=&self.records[index];
        if old.revision!=revision { return Err(Failure::Conflict); }
        if self.allocations.as_ref().is_some_and(|a|a.blocks_pending_cancel(old)) {return Err(Failure::Unavailable);}
        if old.state==PendingState::CancelledPending { return Ok(()); }
        let mut candidate=self.records.clone();
        candidate[index].revision=revision.checked_add(1).ok_or(Failure::Invalid)?;
        candidate[index].state=PendingState::CancelledPending;
        let _writer=self.activity.local_maintenance().map_err(|_|Failure::Unavailable)?;
        self.publish(candidate)
    }
    fn available(&self)->Result<(),Failure> {
        if self.uncertain.is_some() { Err(Failure::PersistenceUnknown) } else { Ok(()) }
    }
    fn publish(&mut self, candidate:Vec<Record>)->Result<(),Failure> {
        self.publish_snapshot(candidate,self.allocations.clone())
    }
    fn publish_snapshot(&mut self,candidate:Vec<Record>,allocations:Option<allocations::Authority>)->Result<(),Failure> {
        validate(&candidate)?;
        if let Some(a)=&allocations {a.validate(&candidate)?;}
        if allocations.as_ref().is_some_and(|a|a.unsettled()) {self.project_allocation_recovery(true);}
        match self.store.write_snapshot(&candidate,allocations.as_ref()) {
            Ok(())=> {
                self.records=candidate; self.allocations=allocations;
                self.project_allocation_recovery(self.allocations.as_ref().is_some_and(|a|a.unsettled()));Ok(())
            },
            Err(Failure::PersistenceUnknown)=> {
                if allocations.is_some() {self.project_allocation_recovery(true);}
                self.uncertain=Some((candidate,allocations));Err(Failure::PersistenceUnknown)
            },
            Err(other)=> {self.project_allocation_recovery(self.allocations.as_ref().is_some_and(|a|a.unsettled()));Err(other)},
        }
    }
}
fn validate(records:&[Record])->Result<(),Failure> {
    for (i,r) in records.iter().enumerate() {
        if r.owner.installation!=r.intent.source.installation ||
            (r.revision,r.state.clone()) != match r.state {
                PendingState::PendingSourceAdmission=>(0,PendingState::PendingSourceAdmission),
                PendingState::CancelledPending=>(1,PendingState::CancelledPending),
            } || records[..i].iter().any(|old| old.owner==r.owner && old.request==r.request) {
            return Err(Failure::Invalid);
        }
    }
    Ok(())
}
