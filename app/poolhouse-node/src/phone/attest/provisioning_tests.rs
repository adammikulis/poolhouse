use super::{chain, FreshRevocations};
use rcgen::{
    BasicConstraints, CertificateParams, CustomExtension, DistinguishedName, DnType, IsCa, Issuer,
    KeyPair, KeyUsagePurpose,
};
use std::time::{Duration, SystemTime};

enum Profile {
    Factory,
    Remote,
    Unknown,
    FactoryWithProvisioning,
}

fn params(name: &str, serial: u64) -> CertificateParams {
    let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
    params.distinguished_name = DistinguishedName::new();
    params.distinguished_name.push(DnType::CommonName, name);
    params.serial_number = Some(serial.into());
    params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    params.key_usages = vec![KeyUsagePurpose::KeyCertSign];
    params
}

fn sign(
    params: &CertificateParams,
    key: &KeyPair,
    parent: &CertificateParams,
    parent_key: &KeyPair,
) -> Vec<u8> {
    params
        .clone()
        .signed_by(key, &Issuer::new(parent.clone(), parent_key))
        .unwrap()
        .der()
        .to_vec()
}

struct Path {
    certificates: Vec<Vec<u8>>,
    public: [u8; 65],
    root: Vec<u8>,
}

fn path(profile: Profile, expired: bool) -> Path {
    let root_key = KeyPair::generate().unwrap();
    let root_params = params("Fixture pinned root", 100);
    let root = root_params
        .clone()
        .self_signed(&root_key)
        .unwrap()
        .der()
        .to_vec();
    let remote = matches!(profile, Profile::Remote);
    let first_key = KeyPair::generate().unwrap();
    let mut first_params = params(
        if remote {
            "fixture-remote-intermediate"
        } else {
            "Fixture factory intermediate"
        },
        101,
    );
    if matches!(profile, Profile::Factory | Profile::FactoryWithProvisioning) {
        first_params.distinguished_name.push(
            DnType::CustomDnType(vec![2, 5, 4, 5]),
            "fixture-factory-serial",
        );
    }
    let first = sign(&first_params, &first_key, &root_params, &root_key);
    let second_key = KeyPair::generate().unwrap();
    let second_params = params("fixture-remote-server", 102);
    let second = sign(&second_params, &second_key, &first_params, &first_key);
    let attestation_key = KeyPair::generate().unwrap();
    let mut attestation_params = params("Fixture attestation issuer", 103);
    if expired {
        attestation_params.not_before = rcgen::date_time_ymd(2020, 1, 1);
        attestation_params.not_after = rcgen::date_time_ymd(2021, 1, 1);
    }
    if remote || matches!(profile, Profile::FactoryWithProvisioning) {
        attestation_params
            .custom_extensions
            .push(CustomExtension::from_oid_content(
                &[1, 3, 6, 1, 4, 1, 11129, 2, 1, 30],
                vec![0xa0],
            ));
    }
    let attestation = if remote {
        sign(
            &attestation_params,
            &attestation_key,
            &second_params,
            &second_key,
        )
    } else {
        sign(
            &attestation_params,
            &attestation_key,
            &first_params,
            &first_key,
        )
    };
    let leaf_key = KeyPair::generate().unwrap();
    let mut leaf_params = params("Fixture attested key", 104);
    leaf_params.is_ca = IsCa::ExplicitNoCa;
    leaf_params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
    leaf_params
        .custom_extensions
        .push(CustomExtension::from_oid_content(
            &[1, 3, 6, 1, 4, 1, 11129, 2, 1, 17],
            vec![48, 0],
        ));
    let leaf = sign(
        &leaf_params,
        &leaf_key,
        &attestation_params,
        &attestation_key,
    );
    let certificates = if remote {
        vec![leaf, attestation, second, first, root.clone()]
    } else {
        vec![leaf, attestation, first, root.clone()]
    };
    Path {
        certificates,
        public: leaf_key.public_key_raw().try_into().unwrap(),
        root,
    }
}

fn check(path: &Path, factory_pin: &[u8]) -> bool {
    let revocations = FreshRevocations::from_official_https(
        br#"{"entries":{}}"#,
        SystemTime::now(),
        Duration::from_secs(60),
        Duration::ZERO,
    )
    .unwrap();
    chain::check_fixture(
        &path.certificates,
        &path.public,
        SystemTime::now(),
        &revocations,
        &path.root,
        factory_pin,
    )
    .is_ok()
}

#[test]
fn old_root_rkp_requires_current_dates_and_factory_waiver_is_positive() {
    let remote = path(Profile::Remote, false);
    assert!(check(&remote, &remote.root));
    let expired_remote = path(Profile::Remote, true);
    assert!(!check(&expired_remote, &expired_remote.root));
    let factory = path(Profile::Factory, true);
    assert!(check(&factory, &factory.root));
    assert!(!check(&factory, &remote.root));
    let unknown = path(Profile::Unknown, true);
    assert!(!check(&unknown, &unknown.root));
    let contradictory = path(Profile::FactoryWithProvisioning, true);
    assert!(!check(&contradictory, &contradictory.root));
}
