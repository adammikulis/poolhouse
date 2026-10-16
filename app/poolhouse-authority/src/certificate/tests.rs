use super::*;
use ed25519_dalek::{Signer, SigningKey};

const POINT: &str = "046b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c2964fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5";

fn issuer() -> SigningKey { SigningKey::from_bytes(&[19; 32]) }

fn descriptor() -> Descriptor {
    let anchor = "ab".repeat(32);
    Descriptor(DescriptorFields {
        v: 1, issuer_id: encode(&Sha256::digest(issuer().verifying_key().as_bytes())),
        admission_kind: "genesis".into(), admission_ref: anchor.clone(),
        pool: anchor[..32].into(), anchor, person: "12".repeat(32),
        account: "23".repeat(32), device: "34".repeat(32), key_alg: "p256".into(),
        key: POINT.into(), role: "admin".into(), since_epoch: 1,
        acceptance_message_digest: "45".repeat(32), policy: "56".repeat(32), serial: "67".repeat(32),
    })
}

fn bytes(descriptor: &Descriptor) -> Vec<u8> { serde_json::to_vec(&descriptor.0).unwrap() }

fn signed(descriptor: &Descriptor, message: &[u8]) -> Vec<u8> {
    serde_json::to_vec(&CertificateFields {
        v: 1, descriptor: descriptor.0.clone(), signature: encode(&issuer().sign(message).to_bytes()),
    }).unwrap()
}

#[test]
fn verifies_only_expected_issuer_signature() {
    let expected = descriptor();
    let parsed = Descriptor::parse(&bytes(&expected)).unwrap();
    let certificate = Certificate::parse(&signed(&parsed, &parsed.signing_bytes())).unwrap();
    let key = issuer().verifying_key().to_bytes();
    let proof = certificate.verify(&expected, &key).unwrap();
    assert_eq!(proof.descriptor(), &expected);
    assert_eq!(proof.issuer(), &key);
    assert_eq!(proof.descriptor().digest(), expected.digest());
}

#[test]
fn every_semantic_field_is_bound_to_expected_descriptor_and_signature() {
    let expected = descriptor();
    let certificate = Certificate::parse(&signed(&expected, &expected.signing_bytes())).unwrap();
    let key = issuer().verifying_key().to_bytes();
    for field in ["issuer_id", "admission_kind", "admission_ref", "pool", "anchor", "person", "account", "device",
                  "key_alg", "key", "role", "since_epoch", "acceptance_message_digest", "policy", "serial", "v"] {
        let mut changed = expected.clone();
        match field {
            "issuer_id" => changed.0.issuer_id = "fe".repeat(32),
            "admission_kind" => changed.0.admission_kind = "act".into(),
            "admission_ref" => changed.0.admission_ref = "fe".repeat(32),
            "pool" => changed.0.pool = "fe".repeat(16),
            "anchor" => changed.0.anchor = "fe".repeat(32),
            "person" => changed.0.person = "fe".repeat(32),
            "account" => changed.0.account = "fe".repeat(32),
            "device" => changed.0.device = "fe".repeat(32),
            "key_alg" => changed.0.key_alg = "ed25519".into(),
            "key" => changed.0.key = format!("{}{}", &POINT[..128], "00"),
            "role" => changed.0.role = "recovery".into(),
            "since_epoch" => changed.0.since_epoch = 2,
            "acceptance_message_digest" => changed.0.acceptance_message_digest = "fe".repeat(32),
            "policy" => changed.0.policy = "fe".repeat(32),
            "serial" => changed.0.serial = "fe".repeat(32),
            "v" => changed.0.v = 2,
            _ => unreachable!(),
        }
        assert_eq!(certificate.verify(&changed, &key).unwrap_err(), Error::WrongDescriptor, "{field}");
        assert_ne!(expected.digest(), changed.digest(), "{field}");
        let forged = Certificate(CertificateFields { v: 1, descriptor: changed.0.clone(), signature: certificate.0.signature.clone() });
        assert_eq!(forged.verify(&changed, &key).unwrap_err(),
                   if field == "issuer_id" { Error::InvalidIssuer } else { Error::InvalidSignature }, "{field}");
    }
}

#[test]
fn rejects_noncanonical_unknown_duplicate_and_oversized_encodings() {
    let canonical = bytes(&descriptor());
    let text = String::from_utf8(canonical.clone()).unwrap();
    for bad in [format!(" {text}"), format!("{text}\n"), text.replacen("\"v\":1", "\"v\":1,\"v\":1", 1),
                text.replacen("\"v\":1", "\"v\":1,\"unknown\":true", 1),
                text.replacen("\"v\":1", "\"v\":1.0", 1), text.replace("\"admin\"", "\"\\u0061dmin\""),
                text.replacen("\"v\":1,", "", 1), "[[]]".into(), " ".repeat(MAX_DESCRIPTOR + 1)] {
        assert!(Descriptor::parse(bad.as_bytes()).is_err(), "{bad}");
    }
    let certificate = signed(&descriptor(), &descriptor().signing_bytes());
    let text = String::from_utf8(certificate).unwrap();
    for bad in [format!("{text} "), text.replacen("\"v\":1", "\"v\":1,\"v\":1", 1),
                text.replacen("\"v\":1", "\"v\":1,\"active\":true", 1), " ".repeat(MAX_CERTIFICATE + 1)] {
        assert!(Certificate::parse(bad.as_bytes()).is_err());
    }
}

#[test]
fn rejects_malformed_scope_and_key_descriptors() {
    let expected = descriptor();
    for (field, value) in [("pool", "aa".repeat(16)), ("anchor", "ff".repeat(32)),
        ("issuer_id", "AB".repeat(32)), ("person", String::new()), ("account", "00".repeat(31)),
        ("device", "gg".repeat(32)), ("key_alg", "ed25519".into()), ("key", "00".repeat(65)),
        ("key", "04".to_owned() + &"00".repeat(64)), ("role", "member".into()),
        ("admission_kind", "self".into()), ("admission_ref", "aa".repeat(32)),
        ("policy", "0".repeat(65)), ("serial", "0".repeat(63))] {
        let mut value_json = serde_json::to_value(&expected.0).unwrap();
        value_json[field] = value.into();
        let fields: DescriptorFields = serde_json::from_value(value_json).unwrap();
        assert!(Descriptor::parse(&serde_json::to_vec(&fields).unwrap()).is_err(), "{field}");
    }
    let mut changed = expected;
    changed.0.since_epoch = 0;
    assert!(Descriptor::parse(&bytes(&changed)).is_err());
    changed.0.since_epoch = 1;
    changed.0.v = 0;
    assert!(Descriptor::parse(&bytes(&changed)).is_err());
}

#[test]
fn accepts_explicit_established_act_reference_without_issuer_admission_claim() {
    let mut expected = descriptor();
    expected.0.admission_kind = "act".into();
    expected.0.admission_ref = "98".repeat(32);
    let expected = Descriptor::parse(&bytes(&expected)).unwrap();
    Certificate::parse(&signed(&expected, &expected.signing_bytes())).unwrap()
        .verify(&expected, &issuer().verifying_key().to_bytes()).unwrap();
}

#[test]
fn rejects_cross_domain_tampered_noncanonical_and_wrong_issuer_signatures() {
    let expected = descriptor();
    let key = issuer().verifying_key().to_bytes();
    for message in [bytes(&expected), [b"other-domain\0".as_slice(), &bytes(&expected)].concat(), expected.digest().to_vec()] {
        assert_eq!(Certificate::parse(&signed(&expected, &message)).unwrap().verify(&expected, &key).unwrap_err(), Error::InvalidSignature);
    }
    let certificate = Certificate::parse(&signed(&expected, &expected.signing_bytes())).unwrap();
    assert_eq!(certificate.verify(&expected, &SigningKey::from_bytes(&[20; 32]).verifying_key().to_bytes()).unwrap_err(), Error::InvalidIssuer);
    let mut bad = certificate.clone();
    let mut signature = decode::<64>(&bad.0.signature).unwrap();
    signature[0] ^= 1;
    bad.0.signature = encode(&signature);
    assert_eq!(bad.verify(&expected, &key).unwrap_err(), Error::InvalidSignature);
    signature[32..].fill(255);
    bad.0.signature = encode(&signature);
    assert_eq!(bad.verify(&expected, &key).unwrap_err(), Error::InvalidSignature);
    for signature in ["00".repeat(64), "ff".repeat(64)] {
        bad.0.signature = signature;
        assert_eq!(bad.verify(&expected, &key).unwrap_err(), Error::InvalidSignature);
    }
}

#[test]
fn rejects_weak_invalid_and_noncanonical_issuer_points() {
    for key in [[0; 32], { let mut b = [0; 32]; b[0] = 1; b }, [255; 32],
                { let mut b = [255; 32]; b[0] = 0xee; b[31] = 0x7f; b }] {
        let mut expected = descriptor();
        expected.0.issuer_id = encode(&Sha256::digest(key));
        let certificate = Certificate::parse(&signed(&expected, &expected.signing_bytes())).unwrap();
        assert_eq!(certificate.verify(&expected, &key).unwrap_err(), Error::InvalidIssuer);
    }
}
