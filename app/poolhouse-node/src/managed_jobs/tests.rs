#![cfg(unix)]
use super::*;
use crate::sys::{try_lock,Lock};
use std::path::Path;

fn parent(root:&Path)->(Lock,File) {
    crate::fsutil::private_dir(root).unwrap();
    let lock=try_lock(&root.join("node.lock")).unwrap().unwrap();
    let dir=lock.checked_parent(root,"node.lock").unwrap(); (lock,dir)
}
fn data()->(OwnerObservation,RequestId,JobManagedTestUnitIntent) {
    let owner=serde_json::from_value(serde_json::json!({"principal_digest":"11".repeat(32),"incarnation":"00000000-0000-0000-0000-000000000004",
        "installation":{"id":"00000000-0000-0000-0000-000000000001","generation":"00000000-0000-0000-0000-000000000005"}})).unwrap();
    let request=serde_json::from_value(serde_json::json!("00000000-0000-0000-0000-000000000002")).unwrap();
    let intent=serde_json::from_value(serde_json::json!({"source":{"id":"00000000-0000-0000-0000-000000000003",
        "generation":"00000000-0000-0000-0000-000000000005","installation":{"id":"00000000-0000-0000-0000-000000000001","generation":"00000000-0000-0000-0000-000000000005"}},"selector":"tests/test_unit.py::test_item[a*b]"})).unwrap();
    (owner,request,intent)
}
#[test]
fn durable_attach_conflict_cancel_cas_and_reopen() {
    let root=tempfile::tempdir().unwrap();let (_lock,dir)=parent(root.path());
    let mut book=Book::open(dir.try_clone().unwrap(),Registry::new()).unwrap();let(o,r,i)=data();
    assert_eq!(book.remember_pending(o.clone(),r.clone(),i.clone()).unwrap(),Remembered::Created);
    assert_eq!(book.remember_pending(o.clone(),r.clone(),i.clone()).unwrap(),Remembered::Attached);
    let mut other=i.clone();other.selector=serde_json::from_value(serde_json::json!("other")).unwrap();
    assert!(matches!(book.remember_pending(o.clone(),r.clone(),other),Err(Failure::Conflict)));
    assert!(matches!(book.cancel_pending(&o,&r,4),Err(Failure::Conflict)));
    book.cancel_pending(&o,&r,0).unwrap();drop(book);
    let mut reopened=Book::open(dir,Registry::new()).unwrap();
    assert_eq!(reopened.inspect_pending(&o,&r).unwrap().unwrap().state,PendingState::CancelledPending);
    assert_eq!(reopened.remember_pending(o,r,i).unwrap(),Remembered::Attached);
}
#[test]
fn injected_write_failures_preserve_old_or_candidate_without_ready_work() {
    for point in ["before_rename","after_rename"] {
        let root=tempfile::tempdir().unwrap();let(_lock,dir)=parent(root.path());
        let mut book=Book::open(dir.try_clone().unwrap(),Registry::new()).unwrap();let(o,r,i)=data();
        book.remember_pending(o.clone(),r.clone(),i).unwrap();book.store.fail_at=Some(point);
        let result=book.cancel_pending(&o,&r,0);
        if point=="after_rename" {
            assert!(matches!(result,Err(Failure::PersistenceUnknown)));
            assert!(book.uncertain.is_some());
            assert!(matches!(book.inspect_pending(&o,&r),Err(Failure::PersistenceUnknown)));
        } else { assert!(matches!(result,Err(Failure::Io(_)))); }
        drop(book);let recovered=Book::open(dir,Registry::new()).unwrap();
        let record=recovered.inspect_pending(&o,&r).unwrap().unwrap();
        assert_eq!(record.state,if point=="after_rename"{PendingState::CancelledPending}else{PendingState::PendingSourceAdmission});
    }
}
#[test]
fn nofollow_replaced_lock_and_corrupt_or_unknown_records_refuse() {
    use std::os::unix::fs::{symlink,PermissionsExt};
    let root=tempfile::tempdir().unwrap();let(lock,dir)=parent(root.path());
    std::fs::hard_link(root.path().join("node.lock"),root.path().join("linked-lock")).unwrap();
    assert!(lock.checked_parent(root.path(),"node.lock").is_err());
    std::fs::remove_file(root.path().join("linked-lock")).unwrap();
    let book=Book::open(dir.try_clone().unwrap(),Registry::new()).unwrap();drop(book);
    let record=root.path().join("managed-pending/records.json");
    let outside=root.path().join("outside");std::fs::write(&outside,b"untouched").unwrap();
    std::fs::remove_file(&record).unwrap();
    symlink(&outside,&record).unwrap();assert!(Book::open(dir.try_clone().unwrap(),Registry::new()).is_err());
    assert_eq!(std::fs::read(&outside).unwrap(),b"untouched");std::fs::remove_file(&record).unwrap();
    std::fs::write(&record,b"{}").unwrap();
    std::fs::set_permissions(&record,std::fs::Permissions::from_mode(0o600)).unwrap();
    assert!(Book::open(dir,Registry::new()).is_err());
    std::fs::rename(root.path().join("node.lock"),root.path().join("old-lock")).unwrap();
    std::fs::write(root.path().join("node.lock"),b"").unwrap();assert!(lock.checked_parent(root.path(),"node.lock").is_err());
}
#[test]
fn unknown_schema_checksum_duplicate_and_oversized_data_never_reset() {
    let root=tempfile::tempdir().unwrap();let(_lock,dir)=parent(root.path());
    let mut book=Book::open(dir.try_clone().unwrap(),Registry::new()).unwrap();let(o,r,i)=data();
    book.remember_pending(o,r,i).unwrap();drop(book);
    let path=root.path().join("managed-pending/records.json");let original=std::fs::read(&path).unwrap();
    for field in ["version","checksum"] {
        let mut value:serde_json::Value=serde_json::from_slice(&original).unwrap();
        value[field]=if field=="version" {serde_json::json!(2)}else{serde_json::json!("bad")};
        let invalid=serde_json::to_vec(&value).unwrap();std::fs::write(&path,&invalid).unwrap();
        assert!(Book::open(dir.try_clone().unwrap(),Registry::new()).is_err());assert_eq!(std::fs::read(&path).unwrap(),invalid);
    }
    std::fs::write(&path,&original).unwrap();let mut book=Book::open(dir.try_clone().unwrap(),Registry::new()).unwrap();
    book.records.push(book.records[0].clone());book.store.write(&book.records).unwrap();
    assert!(Book::open(dir.try_clone().unwrap(),Registry::new()).is_err());
    let oversized=vec![b' ';crate::wire::MAX_FRAME+1];std::fs::write(&path,&oversized).unwrap();
    assert!(Book::open(dir,Registry::new()).is_err());assert_eq!(std::fs::read(&path).unwrap().len(),oversized.len());
}

#[test]
fn missing_established_or_preexisting_uninitialized_store_never_resets() {
    let root=tempfile::tempdir().unwrap();let(_lock,dir)=parent(root.path());
    let mut book=Book::open(dir.try_clone().unwrap(),Registry::new()).unwrap();let(o,r,i)=data();
    book.remember_pending(o,r,i).unwrap();drop(book);
    std::fs::remove_file(root.path().join("managed-pending/records.json")).unwrap();
    assert!(Book::open(dir,Registry::new()).is_err());
    assert!(!root.path().join("managed-pending/records.json").exists());
    let root=tempfile::tempdir().unwrap();let(_lock,dir)=parent(root.path());
    crate::fsutil::private_dir(&root.path().join("managed-pending")).unwrap();
    assert!(Book::open(dir,Registry::new()).is_err());
    assert!(!root.path().join("managed-pending/records.json").exists());
}

#[test]
fn existing_activity_gate_denies_new_mutation_without_hiding_pending_data() {
    let root=tempfile::tempdir().unwrap();let(_lock,dir)=parent(root.path());
    let activity=Registry::new();let mut book=Book::open(dir,activity.clone()).unwrap();let(o,r,i)=data();
    book.remember_pending(o.clone(),r.clone(),i.clone()).unwrap();activity.drain_for_test();
    assert!(book.inspect_pending(&o,&r).unwrap().is_some());
    assert_eq!(book.remember_pending(o.clone(),r.clone(),i).unwrap(),Remembered::Attached);
    let next=serde_json::from_value(serde_json::json!("00000000-0000-0000-0000-000000000009")).unwrap();
    let (_,_,intent)=data();
    assert!(matches!(book.remember_pending(o.clone(),next,intent),Err(Failure::Unavailable)));
    book.cancel_pending(&o,&r,0).unwrap();
    assert_eq!(book.inspect_pending(&o,&r).unwrap().unwrap().state,PendingState::CancelledPending);
}
fn demand()->allocations::Demand {allocations::Demand{cpu_slots:1,memory_bytes:4096,storage_bytes:8192,output_bytes:1024,work_units:32}}
#[test]
fn pending_data_alone_never_migrates_and_empty_fence_recovers_automatically() {
    let root=tempfile::tempdir().unwrap();let mut node=crate::node::Node::open(root.path()).unwrap();
    assert!(!root.path().join("managed-pending").exists());
    let(o,r,i)=data();node.pending_jobs().unwrap().remember_pending(o,r,i).unwrap();
    assert!(node.leases.allocation_fence.is_none());
    if let Ok(bytes)=std::fs::read(root.path().join("leases.json")) {assert!(serde_json::from_slice::<crate::lease::table::Table>(&bytes).is_ok());}
    node.prepare_local_allocation_format().unwrap();
    let bytes=std::fs::read(root.path().join("leases.json")).unwrap();
    assert!(serde_json::from_slice::<crate::lease::table::Table>(&bytes).is_err());
    drop(node);let mut reopened=crate::node::Node::open(root.path()).unwrap();
    assert!(!reopened.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
    reopened.prepare_local_allocation_format().unwrap();
}
#[test]
fn one_durable_ledger_retains_unknown_occupancy_and_prevents_remint() {
    let root=tempfile::tempdir().unwrap();let mut node=crate::node::Node::open(root.path()).unwrap();let(o,r,i)=data();
    node.pending_jobs().unwrap().remember_pending(o.clone(),r.clone(),i).unwrap();
    let pending=node.pending_jobs().unwrap().inspect_pending(&o,&r).unwrap().unwrap().clone();
    assert!(node.pending_jobs().unwrap().persist_allocation_intent(&pending,demand()).is_err());
    node.prepare_local_allocation_format().unwrap();
    let operation=node.pending_jobs().unwrap().persist_allocation_intent(&pending,demand()).unwrap();
    assert_eq!(node.pending_jobs().unwrap().persist_allocation_intent(&pending,demand()).unwrap(),operation);
    let mut mismatch=demand();mismatch.cpu_slots=2;
    assert!(matches!(node.pending_jobs().unwrap().persist_allocation_intent(&pending,mismatch),Err(Failure::Conflict)));
    assert_eq!(node.pending_jobs().unwrap().allocation_occupancy().unwrap(),[1,4096,8192,1024,32]);
    assert!(node.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
    node.pending_jobs().unwrap().persist_provider_call_fence(&operation).unwrap();
    assert!(node.pending_jobs().unwrap().settle_unused_intent(&operation).is_err());
    assert!(node.pending_jobs().unwrap().cancel_pending(&o,&r,0).is_err());
    drop(node);let mut reopened=crate::node::Node::open(root.path()).unwrap();
    assert!(reopened.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
    reopened.prepare_local_allocation_format().unwrap();
    assert!(reopened.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
    assert_eq!(reopened.pending_jobs().unwrap().allocation_occupancy().unwrap(),[1,4096,8192,1024,32]);
}
#[test]
fn unused_settlement_is_durable_before_projection_is_released() {
    let root=tempfile::tempdir().unwrap();let mut node=crate::node::Node::open(root.path()).unwrap();let(o,r,i)=data();
    node.pending_jobs().unwrap().remember_pending(o.clone(),r.clone(),i).unwrap();node.prepare_local_allocation_format().unwrap();
    let pending=node.pending_jobs().unwrap().inspect_pending(&o,&r).unwrap().unwrap().clone();
    let operation=node.pending_jobs().unwrap().persist_allocation_intent(&pending,demand()).unwrap();
    node.pending_jobs().unwrap().store.fail_at=Some("after_rename");
    assert!(matches!(node.pending_jobs().unwrap().settle_unused_intent(&operation),Err(Failure::PersistenceUnknown)));
    assert!(node.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
    drop(node);let mut reopened=crate::node::Node::open(root.path()).unwrap();
    assert!(!reopened.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
    assert_eq!(reopened.pending_jobs().unwrap().allocation_occupancy().unwrap(),[0;5]);
    reopened.pending_jobs().unwrap().cancel_pending(&o,&r,0).unwrap();
}
#[test]
fn established_missing_or_mismatched_ledger_blocks_compute_without_reset() {
    for missing in [true,false] {
        let root=tempfile::tempdir().unwrap();let mut node=crate::node::Node::open(root.path()).unwrap();
        node.prepare_local_allocation_format().unwrap();drop(node);
        let record=root.path().join("managed-pending/records.json");
        if missing {std::fs::remove_file(&record).unwrap();}
        else {
            let(_lock,dir)=parent(root.path());let mut book=Book::open_existing(dir,Registry::new()).unwrap();
            let mut candidate=book.allocations.clone().unwrap();
            candidate.id=serde_json::from_value(serde_json::json!("00000000-0000-0000-0000-000000000099")).unwrap();
            book.publish_snapshot(book.records.clone(),Some(candidate)).unwrap();
        }
        let node=crate::node::Node::open(root.path()).unwrap();
        assert!(node.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
        assert_eq!(!record.exists(),missing);
    }
}
#[test]
fn shared_legacy_scheduler_cannot_grant_compute_while_recovery_is_unknown() {
    use crate::lease::types::{Class,Request,Resource,LOCAL};
    let root=tempfile::tempdir().unwrap();let mut node=crate::node::Node::open(root.path()).unwrap();
    let make=|resources|Request{board:"legacy".into(),name:"actor".into(),resources,class:Class::Interactive,estimate_s:None,ttl_s:Some(60),pid:0,remote:true,local:true,wait:true,tag:"check".into(),quiet:false};
    node.leases.clock=Box::new(||1000);
    node.leases.enqueue(1000,&make(vec![Resource::CpuSlots{device:LOCAL.into(),count:1}]),("queued".into(),0)).unwrap();
    node.leases.enqueue(1000,&make(vec![Resource::Claim{kind:"branch".into(),name:"work".into()},Resource::CpuSlots{device:LOCAL.into(),count:1}]),("mixed".into(),0)).unwrap();
    node.leases.table.local_allocations_unresolved.store(true,std::sync::atomic::Ordering::Release);
    node.leases.tick(&crate::lease::table::Foreign::new()).unwrap();
    assert_eq!(node.leases.table.find("queued").unwrap().state,crate::lease::types::State::Queued);
    assert!(node.leases.enqueue(1000,&make(vec![Resource::CpuSlots{device:LOCAL.into(),count:1}]),("new".into(),0)).is_err());
    assert!(node.leases.enqueue(1000,&make(vec![Resource::Claim{kind:"branch".into(),name:"other".into()},Resource::CpuSlots{device:LOCAL.into(),count:1}]),("mixed-new".into(),0)).is_err());
    let mut claim=make(vec![Resource::Claim{kind:"branch".into(),name:"work".into()}]);claim.name="other".into();
    node.leases.enqueue(1000,&claim,("claim".into(),0)).unwrap();
    node.leases.tick(&crate::lease::table::Foreign::new()).unwrap();
    assert_eq!(node.leases.table.find("claim").unwrap().state,crate::lease::types::State::Held);
    assert_eq!(node.leases.table.find("mixed").unwrap().state,crate::lease::types::State::Queued);
}

#[test]
fn lost_format_ack_cannot_be_erased_by_legacy_save_and_recovers_without_cleanup() {
    use crate::lease::types::{Class,Request,Resource};
    let root=tempfile::tempdir().unwrap();let mut node=crate::node::Node::open(root.path()).unwrap();
    node.pending_jobs().unwrap().initialize_allocation_authority().unwrap();
    node.pending_jobs().unwrap().store.fail_at=Some("after_rename");
    assert!(matches!(node.prepare_local_allocation_format(),Err(Failure::PersistenceUnknown)));
    assert!(node.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
    let claim=Request{board:"legacy".into(),name:"actor".into(),resources:vec![Resource::Claim{kind:"branch".into(),name:"work".into()}],
        class:Class::Interactive,estimate_s:None,ttl_s:Some(60),pid:0,remote:true,local:true,wait:true,tag:"claim".into(),quiet:false};
    node.leases.clock=Box::new(||1000);node.leases.enqueue(1000,&claim,("claim".into(),0)).unwrap();
    node.leases.tick(&crate::lease::table::Foreign::new()).unwrap();
    let bytes=std::fs::read(root.path().join("leases.json")).unwrap();
    assert!(serde_json::from_slice::<crate::lease::table::Table>(&bytes).is_err());
    drop(node);let reopened=crate::node::Node::open(root.path()).unwrap();
    assert!(!reopened.leases.table.local_allocations_unresolved.load(std::sync::atomic::Ordering::Acquire));
    assert!(reopened.leases.allocation_fence.is_some());
}
