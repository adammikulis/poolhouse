use super::*;

fn state() -> State {
    State {
        binding: Binding {
            authority: [1; 32],
            owner_uid: 501,
            node: [2; 32],
            tls: [3; 32],
            pool: [4; 32],
        },
        generation: 0,
        head: [0; 32],
        pending: None,
        phones: vec![],
        revoked: vec![],
    }
}
fn bootstrap(state: &mut State) -> Operation {
    state
        .prepare(
            Action::Bootstrap {
                approval_key_id: [5; 32],
                device: [6; 32],
            },
            100,
        )
        .unwrap()
}
fn proof(operation: &Operation, scope: Scope, signer: Hash) -> VerifiedAuthorization {
    let key_id = match operation.action {
        Action::Bootstrap {
            approval_key_id, ..
        }
        | Action::EnrollPhone {
            approval_key_id, ..
        }
        | Action::RevokePhone {
            approval_key_id, ..
        } => approval_key_id,
    };
    VerifiedAuthorization {
        digest: operation.digest(),
        generation: operation.generation,
        scope,
        signer,
        possession: Some(VerifiedPossession {
            digest: operation.digest(),
            key_id,
        }),
    }
}
fn initialized() -> State {
    let mut s = state();
    let op = bootstrap(&mut s);
    let p = proof(&op, Scope::InstallerPresence, [5; 32]);
    s.commit(&op, p, 101).unwrap();
    s
}

#[test]
fn schema_rejects_unknown_duplicate_and_unbounded_input() {
    let mut s = state();
    let op = bootstrap(&mut s);
    let json = serde_json::to_string(&op).unwrap();
    assert_eq!(Operation::parse(json.as_bytes()).unwrap(), op);
    for text in [
        format!("{{\"unknown\":true,{}", &json[1..]),
        format!("{{\"version\":1,{}", &json[1..]),
        " ".repeat(MAX_REQUEST + 1),
    ] {
        assert_eq!(
            Operation::parse(text.as_bytes()),
            Err(Error::InvalidRequest)
        );
    }
}

#[test]
fn bootstrap_requires_exact_presence_and_cannot_replay() {
    let mut s = state();
    let op = bootstrap(&mut s);
    assert_eq!(
        s.commit(&op, proof(&op, Scope::ApprovalKey, [5; 32]), 101),
        Err(Error::Unauthorized)
    );
    s.commit(&op, proof(&op, Scope::InstallerPresence, [5; 32]), 101)
        .unwrap();
    assert_eq!(
        s.commit(&op, proof(&op, Scope::InstallerPresence, [5; 32]), 102),
        Err(Error::StaleState)
    );
    let again = s.prepare(op.action.clone(), 102).unwrap();
    assert_eq!(
        s.commit(
            &again,
            proof(&again, Scope::InstallerPresence, [5; 32]),
            103
        ),
        Err(Error::Unauthorized)
    );
}

#[test]
fn cross_pool_node_tls_owner_and_authority_substitution_refused() {
    for field in 0..5 {
        let mut s = state();
        let mut op = bootstrap(&mut s);
        match field {
            0 => op.binding.pool = [9; 32],
            1 => op.binding.node = [9; 32],
            2 => op.binding.tls = [9; 32],
            3 => op.binding.owner_uid = 502,
            _ => op.binding.authority = [9; 32],
        };
        assert_eq!(
            s.commit(&op, proof(&op, Scope::InstallerPresence, [5; 32]), 101),
            Err(Error::WrongBinding)
        );
    }
}

#[test]
fn canonical_bytes_bind_every_request_field() {
    let mut s = state();
    let op = bootstrap(&mut s);
    let bytes = op.signing_bytes();
    let mut changed = op.clone();
    changed.expires_at -= 1;
    assert_ne!(bytes, changed.signing_bytes());
    changed = op.clone();
    changed.action = Action::EnrollPhone {
        approval_key_id: [5; 32],
        device: [6; 32],
    };
    assert_ne!(bytes, changed.signing_bytes());
    changed = op.clone();
    changed.nonce[0] ^= 1;
    assert_ne!(bytes, changed.signing_bytes());
    assert!(bytes.starts_with(DOMAIN));
}

#[test]
fn expired_future_and_overflow_fail_without_mutation() {
    for now in [99, 400] {
        let mut s = state();
        let op = bootstrap(&mut s);
        assert_eq!(
            s.commit(&op, proof(&op, Scope::InstallerPresence, [5; 32]), now),
            Err(Error::Expired)
        );
        assert_eq!(s.generation, 0);
        assert!(s.phones.is_empty());
    }
    let mut s = state();
    assert_eq!(
        s.prepare(
            Action::Bootstrap {
                approval_key_id: [5; 32],
                device: [6; 32]
            },
            u64::MAX
        ),
        Err(Error::InvalidRequest)
    );
    let mut s = initialized();
    s.generation = u64::MAX;
    let op = s
        .prepare(
            Action::RevokePhone {
                approval_key_id: [5; 32],
                device: [6; 32],
            },
            100,
        )
        .unwrap();
    assert_eq!(
        s.commit(&op, proof(&op, Scope::ApprovalKey, [5; 32]), 101),
        Err(Error::StaleState)
    );
    assert_eq!(s.phones.len(), 1);
}

#[test]
fn only_one_pending_request_and_exact_proof_digest() {
    let mut s = state();
    let op = bootstrap(&mut s);
    assert_eq!(s.prepare(op.action.clone(), 101), Err(Error::Unavailable));
    let mut p = proof(&op, Scope::InstallerPresence, [5; 32]);
    p.digest = [9; 32];
    assert_eq!(s.commit(&op, p, 101), Err(Error::Unauthorized));
    let mut changed = op.clone();
    changed.nonce = [8; 32];
    assert_eq!(
        s.commit(
            &changed,
            proof(&changed, Scope::InstallerPresence, [5; 32]),
            101
        ),
        Err(Error::StaleState)
    );
}

#[test]
fn revoked_approval_key_id_cannot_enroll_or_resurrect() {
    let mut s = initialized();
    let op = s
        .prepare(
            Action::RevokePhone {
                approval_key_id: [5; 32],
                device: [6; 32],
            },
            102,
        )
        .unwrap();
    s.commit(&op, proof(&op, Scope::ApprovalKey, [5; 32]), 103)
        .unwrap();
    let op = s
        .prepare(
            Action::EnrollPhone {
                approval_key_id: [7; 32],
                device: [8; 32],
            },
            104,
        )
        .unwrap();
    assert_eq!(
        s.commit(&op, proof(&op, Scope::ApprovalKey, [5; 32]), 105),
        Err(Error::Unauthorized)
    );
}

#[test]
fn member_cannot_revoke_another_phone() {
    let mut s = initialized();
    let op = s
        .prepare(
            Action::EnrollPhone {
                approval_key_id: [7; 32],
                device: [8; 32],
            },
            102,
        )
        .unwrap();
    s.commit(&op, proof(&op, Scope::ApprovalKey, [5; 32]), 103)
        .unwrap();
    let op = s
        .prepare(
            Action::RevokePhone {
                approval_key_id: [5; 32],
                device: [6; 32],
            },
            104,
        )
        .unwrap();
    assert_eq!(
        s.commit(&op, proof(&op, Scope::ApprovalKey, [7; 32]), 105),
        Err(Error::Unauthorized)
    );
    assert_eq!(s.phones.len(), 2);
}

#[test]
fn new_phone_possession_must_match_exact_request_and_key() {
    for kind in 0..3 {
        let mut s = state();
        let op = bootstrap(&mut s);
        let mut p = proof(&op, Scope::InstallerPresence, [5; 32]);
        match kind {
            0 => p.possession = None,
            1 => p.possession.as_mut().unwrap().digest = [9; 32],
            _ => p.possession.as_mut().unwrap().key_id = [9; 32],
        };
        assert_eq!(s.commit(&op, p, 101), Err(Error::Unauthorized));
        assert_eq!(s.generation, 0);
    }
}

#[test]
fn another_sponsor_cannot_restore_revoked_key() {
    let mut s = initialized();
    let add = s
        .prepare(
            Action::EnrollPhone {
                approval_key_id: [7; 32],
                device: [8; 32],
            },
            102,
        )
        .unwrap();
    s.commit(&add, proof(&add, Scope::ApprovalKey, [5; 32]), 103)
        .unwrap();
    let remove = s
        .prepare(
            Action::RevokePhone {
                approval_key_id: [5; 32],
                device: [6; 32],
            },
            104,
        )
        .unwrap();
    s.commit(&remove, proof(&remove, Scope::ApprovalKey, [5; 32]), 105)
        .unwrap();
    let restore = s
        .prepare(
            Action::EnrollPhone {
                approval_key_id: [5; 32],
                device: [9; 32],
            },
            106,
        )
        .unwrap();
    assert_eq!(
        s.commit(&restore, proof(&restore, Scope::ApprovalKey, [7; 32]), 107),
        Err(Error::Unauthorized)
    );
}
