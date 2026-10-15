use super::*;
use rcgen::{
    BasicConstraints, CertificateParams, CustomExtension, DistinguishedName, DnType, IsCa, Issuer,
    KeyPair, KeyUsagePurpose,
};
use std::time::{Duration, SystemTime};

const SIGNER: [u8; 32] = [7; 32];
const CHALLENGE: [u8; 32] = [8; 32];

fn tlv(tag: &[u8], bytes: &[u8]) -> Vec<u8> {
    let mut result = tag.to_vec();
    if bytes.len() < 128 {
        result.push(bytes.len() as u8);
    } else if bytes.len() < 256 {
        result.extend([129, bytes.len() as u8]);
    } else {
        result.extend([130, (bytes.len() >> 8) as u8, bytes.len() as u8]);
    }
    result.extend(bytes);
    result
}
fn seq(parts: &[Vec<u8>]) -> Vec<u8> {
    tlv(&[48], &parts.concat())
}
fn num(n: u8) -> Vec<u8> {
    tlv(&[2], &[n])
}
fn octets(bytes: &[u8]) -> Vec<u8> {
    tlv(&[4], bytes)
}
fn tagged(tag: u32, inner: &[u8]) -> Vec<u8> {
    if tag < 31 {
        return tlv(&[160 | tag as u8], inner);
    }
    if tag < 128 {
        return tlv(&[191, tag as u8], inner);
    }
    tlv(&[191, 128 | (tag >> 7) as u8, (tag & 127) as u8], inner)
}
fn app(signer: &[u8; 32]) -> Vec<u8> {
    seq(&[
        tlv(&[49], &seq(&[octets(b"app.poolhouse.phone"), num(1)])),
        tlv(&[49], &octets(signer)),
    ])
}
fn extension(
    auth: u8,
    timeout: bool,
    level: u8,
    signer: &[u8; 32],
    challenge: &[u8; 32],
) -> Vec<u8> {
    let mut hardware = vec![
        tagged(1, &tlv(&[49], &num(2))),
        tagged(2, &num(3)),
        tagged(3, &tlv(&[2], &[1, 0])),
        tagged(5, &tlv(&[49], &num(4))),
        tagged(10, &num(1)),
        tagged(504, &num(auth)),
        tagged(702, &num(0)),
        tagged(
            704,
            &seq(&[
                octets(&[1; 32]),
                tlv(&[1], &[255]),
                tlv(&[10], &[0]),
                octets(&[2; 32]),
            ]),
        ),
    ];
    if timeout {
        hardware.push(tagged(505, &num(0)));
    }
    seq(&[
        num(4),
        tlv(&[10], &[level]),
        num(41),
        tlv(&[10], &[level]),
        octets(challenge),
        octets(&[]),
        seq(&[tagged(709, &octets(&app(signer)))]),
        seq(&hardware),
    ])
}

fn valid() -> Vec<u8> {
    extension(2, false, 1, &SIGNER, &CHALLENGE)
}

struct Fixture {
    chain: Vec<Vec<u8>>,
    expected: ExpectedAttestation,
    root: Vec<u8>,
}

fn fixture(extension: &[u8]) -> Fixture {
    let root_key = KeyPair::generate().unwrap();
    let mut root_params = CertificateParams::new(Vec::<String>::new()).unwrap();
    root_params.distinguished_name = DistinguishedName::new();
    root_params
        .distinguished_name
        .push(DnType::CommonName, "Fixture root");
    root_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    root_params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    root_params.serial_number = Some(100u64.into());
    let root = root_params.self_signed(&root_key).unwrap().der().to_vec();
    let issuer = Issuer::new(root_params, &root_key);
    let leaf_key = KeyPair::generate().unwrap();
    let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
    params.serial_number = Some(101u64.into());
    params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
    params
        .custom_extensions
        .push(CustomExtension::from_oid_content(
            &[1, 3, 6, 1, 4, 1, 11129, 2, 1, 17],
            extension.to_vec(),
        ));
    let leaf = params.signed_by(&leaf_key, &issuer).unwrap().der().to_vec();
    Fixture {
        chain: vec![leaf, root.clone()],
        expected: ExpectedAttestation {
            public: leaf_key.public_key_raw().try_into().unwrap(),
            challenge: CHALLENGE,
        },
        root,
    }
}

fn revocations(body: &[u8]) -> FreshRevocations {
    FreshRevocations::from_official_https(
        body,
        SystemTime::now(),
        Duration::from_secs(60),
        Duration::ZERO,
    )
    .unwrap()
}
fn empty() -> FreshRevocations {
    revocations(br#"{"entries":{}}"#)
}
fn check(f: &Fixture) -> Result<VerifiedAttestation> {
    let freshness = empty();
    verify_with(
        &f.chain,
        &f.expected,
        SystemTime::now(),
        &freshness,
        &SIGNER,
        1,
        &[&f.root],
    )
}

#[test]
fn valid_hardware_policy_and_full_path() {
    for level in [1, 2] {
        let f = fixture(&extension(2, false, level, &SIGNER, &CHALLENGE));
        let proof = check(&f).unwrap();
        assert_eq!(proof.public(), &f.expected.public);
        assert_eq!(
            proof.hardware_level(),
            if level == 1 {
                HardwareLevel::TrustedEnvironment
            } else {
                HardwareLevel::StrongBox
            }
        );
        assert_ne!(proof.chain_digest(), &[0; 32]);
        assert_eq!(
            proof.revocations_digest(),
            &<[u8; 32]>::from(Sha256::digest(br#"{"entries":{}}"#))
        );
        assert!(proof.revocations_fetched_at() <= proof.checked_at());
        assert!(proof.checked_at() < proof.revocations_valid_until());
    }
}

#[test]
fn authentication_and_unknown_hardware_are_refused() {
    for ext in [
        extension(1, false, 1, &SIGNER, &CHALLENGE),
        extension(3, false, 1, &SIGNER, &CHALLENGE),
        extension(2, true, 1, &SIGNER, &CHALLENGE),
        extension(2, false, 0, &SIGNER, &CHALLENGE),
        extension(2, false, 3, &SIGNER, &CHALLENGE),
    ] {
        assert!(check(&fixture(&ext)).is_err());
    }
}

#[test]
fn challenge_app_identity_and_public_key_are_bound() {
    assert!(check(&fixture(&extension(2, false, 1, &[9; 32], &CHALLENGE))).is_err());
    assert!(check(&fixture(&extension(2, false, 1, &SIGNER, &[9; 32]))).is_err());
    let mut f = fixture(&valid());
    f.expected.public[5] ^= 1;
    assert!(check(&f).is_err());
}

#[test]
fn roots_signatures_and_all_chain_serials_are_checked() {
    let f = fixture(&valid());
    let freshness = empty();
    assert!(verify_with(
        &f.chain,
        &f.expected,
        SystemTime::now(),
        &freshness,
        &SIGNER,
        1,
        chain::GOOGLE_ROOTS
    )
    .is_err());
    for serial in ["64", "65"] {
        let body = format!("{{\"entries\":{{\"{serial}\":{{\"status\":\"REVOKED\"}}}}}}");
        let statuses = revocations(body.as_bytes());
        assert!(matches!(
            verify_with(
                &f.chain,
                &f.expected,
                SystemTime::now(),
                &statuses,
                &SIGNER,
                1,
                &[&f.root]
            ),
            Err(AttestationError::Revoked)
        ));
    }
    let mut broken = fixture(&valid());
    let last = broken.chain[0].len() - 1;
    broken.chain[0][last] ^= 1;
    assert!(check(&broken).is_err());
}

#[test]
fn revocation_schema_and_deadlines_fail_closed() {
    for body in [
        br#"{"entries":{"64":{"status":"VALID"}}}"#.as_slice(),
        br#"{"entries":{"64":{"status":"REVOKED"},"64":{"status":"SUSPENDED"}}}"#,
        br#"{"entries":{},"other":true}"#,
        br#"{"entries":{"64":{"status":"SUSPENDED","expires":"2026-02-30"}}}"#,
    ] {
        assert!(FreshRevocations::from_official_https(
            body,
            SystemTime::now(),
            Duration::from_secs(60),
            Duration::ZERO
        )
        .is_err());
    }
    let mut r = empty();
    r.received_for_test(Duration::from_secs(61));
    assert!(r.current(SystemTime::now()).is_err());
    assert!(empty().current(SystemTime::UNIX_EPOCH).is_err());
    assert!(FreshRevocations::from_official_https(
        br#"{"entries":{}}"#,
        SystemTime::now() + Duration::from_secs(1),
        Duration::from_secs(60),
        Duration::ZERO
    )
    .is_err());
    assert!(FreshRevocations::from_official_https(
        br#"{"entries":{}}"#,
        SystemTime::now(),
        Duration::from_secs(60),
        Duration::from_secs(60)
    )
    .is_err());
}

#[test]
fn bounds_and_noncanonical_der_are_refused() {
    assert!(der::one(&[2, 129, 1, 1], 2).is_err());
    assert!(der::one(&[2, 128, 0, 0], 2).is_err());
    assert!(der::number(der::one(&[2, 2, 0, 1], 2).unwrap(), 2).is_err());
    let mut nested = num(1);
    for _ in 0..18 {
        nested = seq(&[nested]);
    }
    assert!(der::bounded(&nested, 0).is_err());
    let mut f = fixture(&valid());
    f.chain.push(f.root.clone());
    assert!(check(&f).is_err());
    f.chain = vec![vec![0; 16_385], f.root.clone()];
    assert!(check(&f).is_err());
}

#[test]
fn older_publisher_app_version_is_refused() {
    let f = fixture(&valid());
    let freshness = empty();
    assert!(verify_with(
        &f.chain,
        &f.expected,
        SystemTime::now(),
        &freshness,
        &SIGNER,
        2,
        &[&f.root]
    )
    .is_err());
}

fn extended(attested_intermediate: bool, ca: bool, expired: bool) -> Fixture {
    let root_key = KeyPair::generate().unwrap();
    let mut root_params = CertificateParams::new(Vec::<String>::new()).unwrap();
    root_params
        .distinguished_name
        .push(DnType::CommonName, "Path root");
    root_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    root_params.key_usages = vec![KeyUsagePurpose::KeyCertSign];
    root_params.serial_number = Some(100u64.into());
    let root = root_params.self_signed(&root_key).unwrap().der().to_vec();
    let root_issuer = Issuer::new(root_params, &root_key);
    let middle_key = KeyPair::generate().unwrap();
    let mut middle_params = CertificateParams::new(Vec::<String>::new()).unwrap();
    middle_params
        .distinguished_name
        .push(DnType::CommonName, "Path intermediate");
    middle_params.is_ca = if ca {
        IsCa::Ca(BasicConstraints::Unconstrained)
    } else {
        IsCa::ExplicitNoCa
    };
    middle_params.key_usages = vec![KeyUsagePurpose::KeyCertSign];
    middle_params.serial_number = Some(102u64.into());
    if attested_intermediate {
        middle_params
            .custom_extensions
            .push(CustomExtension::from_oid_content(
                &[1, 3, 6, 1, 4, 1, 11129, 2, 1, 17],
                valid(),
            ));
    }
    let middle = middle_params
        .signed_by(&middle_key, &root_issuer)
        .unwrap()
        .der()
        .to_vec();
    let middle_issuer = Issuer::new(middle_params, &middle_key);
    let leaf_key = KeyPair::generate().unwrap();
    let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
    params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
    params.serial_number = Some(101u64.into());
    params
        .custom_extensions
        .push(CustomExtension::from_oid_content(
            &[1, 3, 6, 1, 4, 1, 11129, 2, 1, 17],
            valid(),
        ));
    if expired {
        params.not_before = rcgen::date_time_ymd(2020, 1, 1);
        params.not_after = rcgen::date_time_ymd(2021, 1, 1);
    }
    let leaf = params
        .signed_by(&leaf_key, &middle_issuer)
        .unwrap()
        .der()
        .to_vec();
    Fixture {
        chain: vec![leaf, middle, root.clone()],
        expected: ExpectedAttestation {
            public: leaf_key.public_key_raw().try_into().unwrap(),
            challenge: CHALLENGE,
        },
        root,
    }
}

#[test]
fn pkix_intermediates_dates_and_appended_attestation_are_checked() {
    assert!(check(&extended(false, true, false)).is_ok());
    assert!(check(&extended(false, false, false)).is_err());
    assert!(check(&extended(false, true, true)).is_err());
    assert!(check(&extended(true, true, false)).is_err());
    let mut f = extended(false, true, false);
    f.chain.swap(0, 1);
    assert!(check(&f).is_err());
}
