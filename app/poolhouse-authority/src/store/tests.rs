use super::*;
use std::fs;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::sync::{Arc, Barrier};

fn fixture() -> (tempfile::TempDir, u32, Envelope) {
    let dir = tempfile::tempdir().unwrap();
    fs::set_permissions(dir.path(), fs::Permissions::from_mode(0o700)).unwrap();
    let uid = fs::metadata(dir.path()).unwrap().uid();
    let mut initial = Envelope {
        version: 1,
        authority: [1; 32],
        owner_uid: 501,
        generation: 0,
        head: [0; 32],
        previous: [0; 32],
        pristine: true,
        payload: vec![],
    };
    initial.head = initial.digest();
    fs::write(
        dir.path().join(STATE),
        serde_json::to_vec(&initial).unwrap(),
    )
    .unwrap();
    fs::write(dir.path().join(LOCK), []).unwrap();
    for name in [STATE, LOCK] {
        fs::set_permissions(dir.path().join(name), fs::Permissions::from_mode(0o600)).unwrap();
    }
    (dir, uid, initial)
}
fn open(dir: &tempfile::TempDir, uid: u32) -> io::Result<Store> {
    Store::from_directory(File::open(dir.path())?, uid)
}
fn next(previous: &Envelope, payload: Vec<u8>) -> Envelope {
    let mut value = Envelope {
        version: 1,
        authority: previous.authority,
        owner_uid: previous.owner_uid,
        generation: previous.generation + 1,
        previous: previous.head,
        head: [0; 32],
        pristine: false,
        payload,
    };
    value.head = value.digest();
    value
}

#[test]
fn missing_or_corrupt_state_never_reinitializes() {
    for bytes in [
        None,
        Some(b"{}".to_vec()),
        Some(vec![b' '; MAX_RECORD as usize + 1]),
    ] {
        let (dir, uid, _) = fixture();
        fs::remove_file(dir.path().join(STATE)).unwrap();
        if let Some(bytes) = bytes {
            fs::write(dir.path().join(STATE), bytes).unwrap();
        }
        assert!(open(&dir, uid).is_err());
    }
}

#[test]
fn rejects_symlink_hardlink_and_writable_state() {
    for variant in 0..3 {
        let (dir, uid, _) = fixture();
        let path = dir.path().join(STATE);
        match variant {
            0 => {
                fs::rename(&path, dir.path().join("other")).unwrap();
                symlink("other", &path).unwrap();
            }
            1 => fs::hard_link(&path, dir.path().join("other")).unwrap(),
            _ => fs::set_permissions(&path, fs::Permissions::from_mode(0o620)).unwrap(),
        }
        assert!(open(&dir, uid).is_err());
    }
}

#[test]
fn rejects_wrong_owner_and_directory_permissions() {
    let (dir, uid, _) = fixture();
    assert!(open(&dir, uid.checked_add(1).unwrap()).is_err());
    fs::set_permissions(dir.path(), fs::Permissions::from_mode(0o770)).unwrap();
    assert!(open(&dir, uid).is_err());
}

#[test]
fn descriptor_traversal_never_follows_symlink() {
    let (dir, _, _) = fixture();
    let anchor = File::open(dir.path()).unwrap();
    fs::create_dir(dir.path().join("real")).unwrap();
    symlink("real", dir.path().join("alias")).unwrap();
    assert!(fd::open_directory(&anchor, "alias").is_err());
    for path in ["../real", "real/child", ".", "..", ""] {
        assert!(fd::open_directory(&anchor, path).is_err());
    }
}

#[test]
fn exact_cas_persists_and_replay_or_identity_substitution_fails() {
    let (dir, uid, initial) = fixture();
    let store = open(&dir, uid).unwrap();
    let updated = next(&initial, vec![7]);
    store.replace(&initial, &updated).unwrap();
    assert_eq!(store.snapshot().unwrap(), updated);
    assert_eq!(open(&dir, uid).unwrap().snapshot().unwrap(), updated);
    assert!(store.replace(&initial, &updated).is_err());
    let mut wrong = next(&updated, vec![8]);
    wrong.authority = [9; 32];
    wrong.head = wrong.digest();
    assert!(store.replace(&updated, &wrong).is_err());
    let mut wrong = next(&updated, vec![8]);
    wrong.previous = [9; 32];
    wrong.head = wrong.digest();
    assert!(store.replace(&updated, &wrong).is_err());
    assert_eq!(store.snapshot().unwrap(), updated);
}

#[test]
fn concurrent_handles_allow_exactly_one_generation_advance() {
    let (dir, uid, initial) = fixture();
    let first = open(&dir, uid).unwrap();
    let second = open(&dir, uid).unwrap();
    let barrier = Arc::new(Barrier::new(2));
    let threads = [first, second]
        .into_iter()
        .enumerate()
        .map(|(index, store)| {
            let barrier = Arc::clone(&barrier);
            let initial = initial.clone();
            std::thread::spawn(move || {
                barrier.wait();
                store
                    .replace(&initial, &next(&initial, vec![index as u8]))
                    .is_ok()
            })
        })
        .collect::<Vec<_>>();
    assert_eq!(
        threads
            .into_iter()
            .filter_map(|thread| thread.join().unwrap().then_some(()))
            .count(),
        1
    );
    assert_eq!(open(&dir, uid).unwrap().snapshot().unwrap().generation, 1);
}

#[test]
fn uncertain_write_poisons_handle_and_reopen_verifies_committed_record() {
    let (dir, uid, initial) = fixture();
    let store = open(&dir, uid).unwrap();
    let updated = next(&initial, vec![7]);
    store
        .fail_after_rename
        .store(true, std::sync::atomic::Ordering::SeqCst);
    assert!(store.replace(&initial, &updated).is_err());
    assert!(store.snapshot().is_err());
    assert!(store.replace(&initial, &updated).is_err());
    assert_eq!(open(&dir, uid).unwrap().snapshot().unwrap(), updated);
}

#[test]
fn malformed_record_bounds_and_head_are_rejected() {
    let (dir, uid, initial) = fixture();
    for kind in 0..4 {
        let mut value = initial.clone();
        match kind {
            0 => value.version = 2,
            1 => value.payload = vec![1; MAX_PAYLOAD + 1],
            2 => value.head = [0; 32],
            _ => {
                value.generation = 1;
                value.head = value.digest();
            }
        };
        fs::write(dir.path().join(STATE), serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(open(&dir, uid).is_err());
    }
}

#[test]
fn descriptor_acls_reject_extra_allow_and_preserve_deny_only() {
    let (dir, uid, _) = fixture();
    let directory = File::open(dir.path()).unwrap();
    acl::set_fixture(&directory, 1, 1 << 2).unwrap();
    assert!(checked(&directory, uid, true, true).is_err());
    assert!(checked(&directory, uid, true, false).is_err());
    acl::set_fixture(&directory, 1, 1 << 1).unwrap();
    assert!(checked(&directory, uid, true, true).is_err());
    checked(&directory, uid, true, false).unwrap();
    acl::set_fixture(&directory, 2, 1 << 4).unwrap();
    assert!(checked(&directory, uid, true, true).is_ok());
    let state = File::open(dir.path().join(STATE)).unwrap();
    acl::set_fixture(&state, 1, 1 << 1).unwrap();
    assert!(checked(&state, uid, false, true).is_err());
}

#[test]
fn pre_rename_failure_removes_only_own_temporary_file() {
    let (dir, uid, initial) = fixture();
    let store = open(&dir, uid).unwrap();
    fs::write(dir.path().join(".state-preserved.tmp"), b"unrelated").unwrap();
    store
        .fail_before_rename
        .store(true, std::sync::atomic::Ordering::SeqCst);
    assert!(store.replace(&initial, &next(&initial, vec![7])).is_err());
    assert!(store.snapshot().is_err());
    let mut names = fs::read_dir(dir.path())
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect::<Vec<_>>();
    names.sort();
    assert_eq!(
        names,
        vec![".state-preserved.tmp", "state.json", "state.lock"]
    );
    assert_eq!(open(&dir, uid).unwrap().snapshot().unwrap(), initial);
}
