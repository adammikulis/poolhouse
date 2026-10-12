//! Android Key Attestation for tests: a root the test makes and trusts, and chains it signs about a phone's key that say what the test
//! wants the phone's secure hardware to have said.

use poolhouse_node::fsutil::{hex, sha256_hex};
use rcgen::{BasicConstraints, CertificateParams, CustomExtension, IsCa, Issuer, KeyPair, PublicKeyData, PKCS_ECDSA_P256_SHA256};

const KEY_DESCRIPTION: [u64; 9] = [1, 3, 6, 1, 4, 1, 11129, 2, 1];

/// What the hardware said about the key: by default that it made the key itself, for this addition, a P-256 key that needs the person's
/// fingerprint for every signature.
#[derive(Clone, Copy)]
pub struct Claims {
    pub hardware: bool,
    pub no_auth_required: bool,
    pub auth_timeout: bool,
    pub fingerprint: bool,
    pub p256: bool,
    pub for_another_addition: bool,
}

impl Default for Claims {
    fn default() -> Claims {
        Claims { hardware: true, no_auth_required: false, auth_timeout: false, fingerprint: true, p256: true, for_another_addition: false }
    }
}

fn der(tag: &[u8], body: &[u8]) -> Vec<u8> {
    let length = if body.len() < 0x80 { vec![body.len() as u8] } else { vec![0x82, (body.len() >> 8) as u8, body.len() as u8] };
    [tag, &length, body].concat()
}

fn integer(v: u8) -> Vec<u8> {
    der(&[2], &[v])
}

/// A field of an authorization list: ``tag`` explicitly, in the long form every tag of 31 or more takes.
fn field(tag: u32, inner: Vec<u8>) -> Vec<u8> {
    let ident = if tag < 31 { vec![0xA0 | tag as u8] } else { vec![0xBF, 0x80 | (tag >> 7) as u8, (tag & 0x7f) as u8] };
    der(&ident, &inner)
}

fn description(challenge: &[u8], c: Claims) -> Vec<u8> {
    let level = u8::from(c.hardware);
    let mut tee = vec![field(2, integer(3)), field(10, integer(if c.p256 { 1 } else { 2 }))].concat();
    if c.no_auth_required {
        tee.extend(field(503, der(&[5], &[])));
    }
    if c.fingerprint {
        tee.extend(field(504, integer(2)));
    }
    if c.auth_timeout {
        tee.extend(field(505, integer(30)));
    }
    let other = [challenge, b" and another"].concat();
    let asked = if c.for_another_addition { &other[..] } else { challenge };
    let body = [integer(4), der(&[10], &[level]), integer(4), der(&[10], &[level]), der(&[4], asked), der(&[4], &[]), der(&[0x30], &[]), der(&[0x30], &tee)].concat();
    der(&[0x30], &body)
}

/// A root that signs attestation chains.
pub struct Root {
    key: KeyPair,
    params: CertificateParams,
    der: Vec<u8>,
    /// What a node is told to trust: the SHA-256 of the root's key.
    pub pin: String,
}

impl Root {
    pub fn new() -> Root {
        let key = KeyPair::generate_for(&PKCS_ECDSA_P256_SHA256).unwrap();
        let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        let der = params.self_signed(&key).unwrap().der().to_vec();
        let pin = sha256_hex(&key.subject_public_key_info());
        Root { key, params, der, pin }
    }

    /// The chain about the key made from ``pkcs8``: its certificate first, the root last, each in hex.
    pub fn chain(&self, pkcs8: &[u8], challenge: &[u8], claims: Claims) -> Vec<String> {
        let subject = KeyPair::try_from(pkcs8).unwrap();
        let mut leaf = CertificateParams::new(Vec::<String>::new()).unwrap();
        let mut oid = KEY_DESCRIPTION.to_vec();
        oid.push(17);
        leaf.custom_extensions.push(CustomExtension::from_oid_content(&oid, description(challenge, claims)));
        let cert = leaf.signed_by(&subject, &Issuer::from_params(&self.params, &self.key)).unwrap();
        vec![hex(cert.der()), hex(&self.der)]
    }
}
