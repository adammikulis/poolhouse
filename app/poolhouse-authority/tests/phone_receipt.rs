use ed25519_dalek::{Signer, SigningKey};
use poolhouse_authority::receipt::{Listing, PhoneBootstrapReceipt, ReceiptError, Scope};

fn receipt(scope: Scope) -> PhoneBootstrapReceipt {
    PhoneBootstrapReceipt { authority: [1; 32], installation: [2; 32], installation_generation: 3,
        request: [4; 32], nonce: [5; 32], transcript: [6; 32], node: [7; 32], tls: [8; 32],
        phone_key: [9; 32], offer: [10; 32], token_hash: [15; 32], phone_id: [18; 16], offer_expires: 100, scope,
        authority_head: [11; 32], authority_generation: 12, prepared_envelope_head: [16; 32], prepared_envelope_generation: 17, committed_at: 90 }
}

#[test]
fn receipt_is_canonical_and_binds_every_field_to_its_signer() {
    let signer = SigningKey::from_bytes(&[42; 32]);
    let public = signer.verifying_key().to_bytes();
    for scope in [Scope::Unfounded, Scope::Governed { anchor: [13; 32], head: [14; 32], listing: Listing::Unlisted },
                  Scope::Governed { anchor: [13; 32], head: [14; 32], listing: Listing::Active },
                  Scope::Governed { anchor: [13; 32], head: [14; 32], listing: Listing::Pending }] {
        let original = receipt(scope);
        let id = original.phone_id_text().unwrap();
        assert_eq!(PhoneBootstrapReceipt::parse_phone_id(&id), Ok(original.phone_id));
        assert!(PhoneBootstrapReceipt::parse_phone_id(&id.to_uppercase()).is_err());
        assert!(PhoneBootstrapReceipt::parse_phone_id("ph-00000000000000000000000000000000").is_err());
        let encoded = original.encode().unwrap();
        let signature = signer.sign(&encoded).to_bytes();
        assert_eq!(PhoneBootstrapReceipt::decode(&encoded).unwrap(), original);
        assert_eq!(PhoneBootstrapReceipt::verify(&encoded, &signature, &public).unwrap(), original);
        for index in 0..encoded.len() {
            let mut changed = encoded.clone();
            changed[index] ^= 1;
            assert!(PhoneBootstrapReceipt::verify(&changed, &signature, &public).is_err(), "byte {index}");
        }
        assert!(PhoneBootstrapReceipt::verify(&encoded, &signature, &[19; 32]).is_err());
        assert!(PhoneBootstrapReceipt::verify(&encoded, &signature[..63], &public).is_err());
        assert_eq!(PhoneBootstrapReceipt::decode(&encoded[..encoded.len() - 1]), Err(ReceiptError::Invalid));
        let mut extra = encoded.clone();
        extra.push(0);
        assert_eq!(PhoneBootstrapReceipt::decode(&extra), Err(ReceiptError::Invalid));
        let mut old_version = encoded.clone();
        let version = b"poolhouse/native-authority/receipt/v4\0".len() - 2;
        for old in [b'1', b'2', b'3'] {
            old_version[version] = old;
            assert_eq!(PhoneBootstrapReceipt::decode(&old_version), Err(ReceiptError::Invalid));
        }
    }
}

#[test]
fn invalid_identifiers_and_late_decisions_are_not_serialized() {
    let mut invalid = receipt(Scope::Unfounded);
    invalid.node = [0; 32];
    assert_eq!(invalid.encode(), Err(ReceiptError::Invalid));
    invalid = receipt(Scope::Unfounded);
    invalid.token_hash = [0; 32];
    assert_eq!(invalid.encode(), Err(ReceiptError::Invalid));
    invalid = receipt(Scope::Unfounded);
    invalid.phone_id = [0; 16];
    assert_eq!(invalid.encode(), Err(ReceiptError::Invalid));
    invalid = receipt(Scope::Unfounded);
    invalid.committed_at = invalid.offer_expires + 1;
    assert_eq!(invalid.encode(), Err(ReceiptError::Invalid));
    invalid.committed_at = invalid.offer_expires;
    assert_eq!(invalid.encode(), Err(ReceiptError::Invalid));
    invalid = receipt(Scope::Governed { anchor: [0; 32], head: [1; 32], listing: Listing::Active });
    assert_eq!(invalid.encode(), Err(ReceiptError::Invalid));
}
