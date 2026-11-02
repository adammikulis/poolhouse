//! Durable accounting obligations only. No physical reservation, provider or permission issuer.
use serde::{Deserialize,Serialize};
use crate::contract::{OperationId,ResourceGeneration};
use super::{Book,Failure,Record};

#[derive(Clone,Debug,PartialEq,Eq,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Demand {
    pub cpu_slots:u32, pub memory_bytes:u64, pub storage_bytes:u64,
    pub output_bytes:u64, pub work_units:u64,
}
#[derive(Clone,Debug,PartialEq,Eq,Serialize,Deserialize)]
#[serde(rename_all="snake_case")]
enum Phase { Intent, ProviderCallFenced, SettledUnused }
#[derive(Clone,Debug,PartialEq,Eq,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry { pending:Record, operation:OperationId, demand:Demand, phase:Phase }
#[derive(Clone,Debug,PartialEq,Eq,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Authority {
    pub id:ResourceGeneration, pub format_generation:u32, entries:Vec<Entry>,
}
impl Authority {
    pub fn validate(&self,records:&[Record])->Result<(),Failure> {
        if self.format_generation!=1 {return Err(Failure::Invalid);}
        for (i,e) in self.entries.iter().enumerate() {
            super::validate(std::slice::from_ref(&e.pending))?;
            let current=records.iter().find(|r|r.owner==e.pending.owner && r.request==e.pending.request).ok_or(Failure::Invalid)?;
            if e.pending.revision!=0 || current.intent!=e.pending.intent ||
                (e.phase!=Phase::SettledUnused && current!=&e.pending) ||
                self.entries[..i].iter().any(|old|old.operation==e.operation || (old.pending.owner==e.pending.owner && old.pending.request==e.pending.request)) {
                return Err(Failure::Invalid);
            }
            let amounts=[u64::from(e.demand.cpu_slots),e.demand.memory_bytes,e.demand.storage_bytes,e.demand.output_bytes,e.demand.work_units];
            if amounts.contains(&0) {return Err(Failure::Invalid);}
        }
        self.occupancy()?;Ok(())
    }
    pub fn occupancy(&self)->Result<[u64;5],Failure> {
        let mut total=[0u64;5];
        for e in self.entries.iter().filter(|e|e.phase!=Phase::SettledUnused) {
            for (sum,n) in total.iter_mut().zip([u64::from(e.demand.cpu_slots),e.demand.memory_bytes,e.demand.storage_bytes,e.demand.output_bytes,e.demand.work_units]) {
                *sum=sum.checked_add(n).ok_or(Failure::Invalid)?;
            }
        } Ok(total)
    }
    pub fn unsettled(&self)->bool {self.entries.iter().any(|e|e.phase!=Phase::SettledUnused)}
    pub fn blocks_pending_cancel(&self,record:&Record)->bool {
        self.entries.iter().any(|e|e.pending.owner==record.owner && e.pending.request==record.request && e.phase!=Phase::SettledUnused)
    }
}
fn uuid()->Result<String,Failure> {
    let h=crate::fsutil::random_hex(16).map_err(|_|Failure::Unavailable)?;
    Ok(format!("{}-{}-{}-{}-{}",&h[..8],&h[8..12],&h[12..16],&h[16..20],&h[20..]))
}
impl Book {
    pub(in crate::node) fn bind_allocation_projection(&mut self,projection:std::sync::Arc<std::sync::atomic::AtomicBool>) {
        self.allocation_projection=Some(projection);
        let unknown=self.uncertain.as_ref().is_some_and(|(_,a)|a.is_some()) ||
            (self.uncertain.is_some() && self.allocations.is_some());
        self.project_allocation_recovery(unknown || self.allocations.as_ref().is_some_and(|a|a.unsettled()));
    }
    pub(super) fn project_allocation_recovery(&self,unresolved:bool) {
        if let Some(p)=&self.allocation_projection {p.store(unresolved,std::sync::atomic::Ordering::Release);}
    }
    pub(in crate::node) fn allocation_parent(&self)->Result<std::fs::File,Failure> {self.store.parent_clone()}
    // No production caller: only a future genuine provider controller may start this
    // bookkeeping protocol. Ordinary pending request data never invokes it.
    pub(in crate::node) fn initialize_allocation_authority(&mut self)->Result<ResourceGeneration,Failure> {
        self.available()?;
        if let Some(a)=&self.allocations {return Ok(a.id.clone());}
        let authority=Authority {id:uuid()?.try_into().map_err(|_|Failure::Invalid)?,format_generation:1,entries:Vec::new()};
        let id=authority.id.clone();
        let _writer=self.activity.local_writer().map_err(|_|Failure::Unavailable)?;
        self.publish_snapshot(self.records.clone(),Some(authority))?;Ok(id)
    }
    pub(in crate::node) fn has_allocation_authority(&self)->bool {self.allocations.is_some()}
    pub(in crate::node) fn allocation_identity(&self)->Result<(ResourceGeneration,u32,bool),Failure> {
        self.available()?;let a=self.allocations.as_ref().ok_or(Failure::Invalid)?;
        Ok((a.id.clone(),a.format_generation,a.unsettled()))
    }
    pub(in crate::node) fn verify_allocation_format(&self)->Result<(),Failure> {
        self.available()?;let a=self.allocations.as_ref().ok_or(Failure::Invalid)?;
        self.store.check_lease_format(&a.id,a.format_generation)
    }
    pub(super) fn allocation_occupancy(&self)->Result<[u64;5],Failure> {
        self.available()?;self.allocations.as_ref().ok_or(Failure::Invalid)?.occupancy()
    }
    pub(in crate::node) fn write_allocation_format(&mut self,bytes:&[u8])->Result<(),Failure> {
        self.available()?;
        let (_,fence)=crate::lease::allocation_format::decode(bytes).map_err(|_|Failure::Invalid)?;
        let fence=fence.ok_or(Failure::Invalid)?;
        let (id,generation,_)=self.allocation_identity()?;
        if fence.authority!=id || fence.generation!=generation {return Err(Failure::Invalid);}
        let _writer=self.activity.local_writer().map_err(|_|Failure::Unavailable)?;
        if let Err(e)=self.store.write_lease_format(bytes) {
            if matches!(e,Failure::PersistenceUnknown) {
                self.project_allocation_recovery(true);
                self.uncertain=Some((self.records.clone(),self.allocations.clone()));
            }
            return Err(e);
        } Ok(())
    }
    // Private ledger transitions return an operation IDENTIFIER, never a resource
    // handle or permission. No production provider/consumer calls these transitions.
    pub(super) fn persist_allocation_intent(&mut self,pending:&Record,demand:Demand)->Result<OperationId,Failure> {
        self.available()?;
        if self.records.iter().find(|r|r.owner==pending.owner && r.request==pending.request)!=Some(pending) || pending.revision!=0 {return Err(Failure::Conflict);}
        let mut authority=self.allocations.clone().ok_or(Failure::Invalid)?;
        self.store.check_lease_format(&authority.id,authority.format_generation)?;
        if let Some(old)=authority.entries.iter().find(|e|e.pending.owner==pending.owner && e.pending.request==pending.request) {
            return if old.pending==*pending && old.demand==demand {Ok(old.operation.clone())}else{Err(Failure::Conflict)};
        }
        let operation:OperationId=uuid()?.try_into().map_err(|_|Failure::Invalid)?;
        authority.entries.push(Entry{pending:pending.clone(),operation:operation.clone(),demand,phase:Phase::Intent});
        let _writer=self.activity.local_writer().map_err(|_|Failure::Unavailable)?;
        self.publish_snapshot(self.records.clone(),Some(authority))?;Ok(operation)
    }
    pub(super) fn persist_provider_call_fence(&mut self,operation:&OperationId)->Result<(),Failure> {
        self.transition(operation,false)
    }
    pub(super) fn settle_unused_intent(&mut self,operation:&OperationId)->Result<(),Failure> {
        self.transition(operation,true)
    }
    fn transition(&mut self,operation:&OperationId,settle_unused:bool)->Result<(),Failure> {
        self.available()?;let mut authority=self.allocations.clone().ok_or(Failure::Invalid)?;
        self.store.check_lease_format(&authority.id,authority.format_generation)?;
        let entry=authority.entries.iter_mut().find(|e|&e.operation==operation).ok_or(Failure::Conflict)?;
        if entry.phase!=Phase::Intent {return Err(Failure::Conflict);}
        entry.phase=if settle_unused{Phase::SettledUnused}else{Phase::ProviderCallFenced};
        if settle_unused {
            let _writer=self.activity.local_maintenance().map_err(|_|Failure::Unavailable)?;
            self.publish_snapshot(self.records.clone(),Some(authority))
        } else {
            let _writer=self.activity.local_writer().map_err(|_|Failure::Unavailable)?;
            self.publish_snapshot(self.records.clone(),Some(authority))
        }
    }
}
