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
    let mut book=Book::open(dir.try_clone().unwrap()).unwrap();let(o,r,i)=data();
    assert_eq!(book.remember_pending(o.clone(),r.clone(),i.clone()).unwrap(),Remembered::Created);
    assert_eq!(book.remember_pending(o.clone(),r.clone(),i.clone()).unwrap(),Remembered::Attached);
    let mut other=i.clone();other.selector=serde_json::from_value(serde_json::json!("other")).unwrap();
    assert!(matches!(book.remember_pending(o.clone(),r.clone(),other),Err(Failure::Conflict)));
    assert!(matches!(book.cancel_pending(&o,&r,4),Err(Failure::Conflict)));
    book.cancel_pending(&o,&r,0).unwrap();drop(book);
    let mut reopened=Book::open(dir).unwrap();
    assert_eq!(reopened.inspect_pending(&o,&r).unwrap().unwrap().state,PendingState::CancelledPending);
    assert_eq!(reopened.remember_pending(o,r,i).unwrap(),Remembered::Attached);
}
#[test]
fn injected_write_failures_preserve_old_or_candidate_without_ready_work() {
    for point in ["before_rename","after_rename"] {
        let root=tempfile::tempdir().unwrap();let(_lock,dir)=parent(root.path());
        let mut book=Book::open(dir.try_clone().unwrap()).unwrap();let(o,r,i)=data();
        book.remember_pending(o.clone(),r.clone(),i).unwrap();book.store.fail_at=Some(point);
        let result=book.cancel_pending(&o,&r,0);
        if point=="after_rename" {
            assert!(matches!(result,Err(Failure::PersistenceUnknown)));
            assert!(book.uncertain.is_some());
            assert!(matches!(book.inspect_pending(&o,&r),Err(Failure::PersistenceUnknown)));
        } else { assert!(matches!(result,Err(Failure::Io(_)))); }
        drop(book);let recovered=Book::open(dir).unwrap();
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
    let book=Book::open(dir.try_clone().unwrap()).unwrap();drop(book);
    let record=root.path().join("managed-pending/records.json");
    let outside=root.path().join("outside");std::fs::write(&outside,b"untouched").unwrap();
    symlink(&outside,&record).unwrap();assert!(Book::open(dir.try_clone().unwrap()).is_err());
    assert_eq!(std::fs::read(&outside).unwrap(),b"untouched");std::fs::remove_file(&record).unwrap();
    std::fs::write(&record,b"{}").unwrap();
    std::fs::set_permissions(&record,std::fs::Permissions::from_mode(0o600)).unwrap();
    assert!(Book::open(dir).is_err());
    std::fs::rename(root.path().join("node.lock"),root.path().join("old-lock")).unwrap();
    std::fs::write(root.path().join("node.lock"),b"").unwrap();assert!(lock.checked_parent(root.path(),"node.lock").is_err());
}
#[test]
fn unknown_schema_checksum_duplicate_and_oversized_data_never_reset() {
    let root=tempfile::tempdir().unwrap();let(_lock,dir)=parent(root.path());
    let mut book=Book::open(dir.try_clone().unwrap()).unwrap();let(o,r,i)=data();
    book.remember_pending(o,r,i).unwrap();drop(book);
    let path=root.path().join("managed-pending/records.json");let original=std::fs::read(&path).unwrap();
    for field in ["version","checksum"] {
        let mut value:serde_json::Value=serde_json::from_slice(&original).unwrap();
        value[field]=if field=="version" {serde_json::json!(2)}else{serde_json::json!("bad")};
        let invalid=serde_json::to_vec(&value).unwrap();std::fs::write(&path,&invalid).unwrap();
        assert!(Book::open(dir.try_clone().unwrap()).is_err());assert_eq!(std::fs::read(&path).unwrap(),invalid);
    }
    std::fs::write(&path,&original).unwrap();let mut book=Book::open(dir.try_clone().unwrap()).unwrap();
    book.records.push(book.records[0].clone());book.store.write(&book.records).unwrap();
    assert!(Book::open(dir.try_clone().unwrap()).is_err());
    let oversized=vec![b' ';crate::wire::MAX_FRAME+1];std::fs::write(&path,&oversized).unwrap();
    assert!(Book::open(dir).is_err());assert_eq!(std::fs::read(&path).unwrap().len(),oversized.len());
}
