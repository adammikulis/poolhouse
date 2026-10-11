//! The certificate of the endpoint a phone talks to: self-signed P-256 (a certificate every Android version verifies), valid ten
//! years. The code a person scans carries its SHA-256, so the phone trusts that certificate and nothing else.

use std::path::Path;
use std::sync::Arc;

use rcgen::{date_time_ymd, CertificateParams, DistinguishedName, DnType, KeyPair, PKCS_ECDSA_P256_SHA256};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::ServerConfig;

use crate::cert::civil;
use crate::error::{Error, Result};
use crate::fsutil::{sha256_hex, write_atomic};

const CERT_FILE: &str = "phone.cert";
const KEY_FILE: &str = "phone.key";
const TEN_YEARS_S: i64 = 10 * 365 * 24 * 3600 + 3 * 24 * 3600;

/// The phone endpoint's certificate and key.
#[derive(Clone)]
pub struct Endpoint {
    pub der: Vec<u8>,
    pkcs8: Vec<u8>,
}

impl Endpoint {
    /// The SHA-256 of the certificate: what a phone pins.
    pub fn fingerprint(&self) -> String {
        sha256_hex(&self.der)
    }

    /// The server side of the endpoint: TLS 1.3, no certificate asked of the phone.
    pub fn server_config(&self) -> Result<Arc<ServerConfig>> {
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let fail = |e: rustls::Error| Error::Invalid(format!("phone tls: {e}"));
        let config = ServerConfig::builder_with_provider(provider)
            .with_protocol_versions(&[&rustls::version::TLS13]).map_err(fail)?
            .with_no_client_auth()
            .with_single_cert(vec![CertificateDer::from(self.der.clone())], PrivateKeyDer::from(PrivatePkcs8KeyDer::from(self.pkcs8.clone()))).map_err(fail)?;
        Ok(Arc::new(config))
    }
}

fn make() -> Result<Endpoint> {
    let fail = |e: rcgen::Error| Error::Invalid(format!("phone certificate: {e}"));
    let signing = KeyPair::generate_for(&PKCS_ECDSA_P256_SHA256).map_err(fail)?;
    let mut params = CertificateParams::new(Vec::<String>::new()).map_err(fail)?;
    let mut name = DistinguishedName::new();
    name.push(DnType::CommonName, "poolhouse-phone");
    params.distinguished_name = name;
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs() as i64);
    let ymd = |s: i64| {
        let (y, m, d) = civil(s.div_euclid(86_400));
        date_time_ymd(y, m, d)
    };
    params.not_before = ymd(now - 24 * 3600);
    params.not_after = ymd(now + TEN_YEARS_S);
    let cert = params.self_signed(&signing).map_err(fail)?;
    Ok(Endpoint { der: cert.der().to_vec(), pkcs8: signing.serialize_der() })
}

/// Load the endpoint's certificate from ``dir``, making it on first use.
pub fn load_or_create(dir: &Path) -> Result<Endpoint> {
    if let (Ok(der), Ok(pkcs8)) = (std::fs::read(dir.join(CERT_FILE)), std::fs::read(dir.join(KEY_FILE))) {
        return Ok(Endpoint { der, pkcs8 });
    }
    let made = make()?;
    write_atomic(&dir.join(KEY_FILE), &made.pkcs8)?;
    write_atomic(&dir.join(CERT_FILE), &made.der)?;
    Ok(made)
}
