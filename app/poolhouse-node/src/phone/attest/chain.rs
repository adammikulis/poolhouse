use std::time::{SystemTime, UNIX_EPOCH};

use rustls::pki_types::{CertificateDer, UnixTime};
use x509_parser::prelude::{FromDer, X509Certificate};

use super::{der, AttestationError, FreshRevocations, Result};

pub(super) const GOOGLE_ROOTS: &[&[u8]] = &[
    include_bytes!("google-root-0.der"),
    include_bytes!("google-root-1.der"),
];
const ATTESTATION: &str = "1.3.6.1.4.1.11129.2.1.17";
const PROVISIONING: &str = "1.3.6.1.4.1.11129.2.1.30";

pub(super) fn check(
    chain: &[Vec<u8>],
    public: &[u8; 65],
    now: SystemTime,
    revocations: &FreshRevocations,
    roots: &[&[u8]],
) -> Result<Vec<u8>> {
    if !(2..=8).contains(&chain.len())
        || chain.iter().map(Vec::len).sum::<usize>() > 65_536
        || chain.iter().any(|c| c.len() > 16_384)
    {
        return Err(AttestationError::Bounds);
    }
    let mut certificates = Vec::new();
    for bytes in chain {
        der::bounded(bytes, 0)?;
        let (rest, certificate) =
            X509Certificate::from_der(bytes).map_err(|_| AttestationError::Encoding)?;
        if !rest.is_empty()
            || certificate
                .extensions()
                .iter()
                .enumerate()
                .any(|(i, extension)| {
                    certificate.extensions()[..i]
                        .iter()
                        .any(|previous| previous.oid == extension.oid)
                })
        {
            return Err(AttestationError::Encoding);
        }
        revocations.check(certificate.raw_serial())?;
        certificates.push(certificate);
    }
    let root = certificates.last().ok_or(AttestationError::Chain)?;
    let trusted = roots
        .iter()
        .find_map(|bytes| {
            X509Certificate::from_der(bytes)
                .ok()
                .filter(|(_, cert)| cert.public_key().raw == root.public_key().raw)
                .map(|_| *bytes)
        })
        .ok_or(AttestationError::Chain)?;
    if root.subject() != root.issuer()
        || chain
            .iter()
            .enumerate()
            .any(|(i, c)| chain[..i].contains(c))
    {
        return Err(AttestationError::Chain);
    }
    for pair in certificates.windows(2) {
        if pair[0].issuer() != pair[1].subject() {
            return Err(AttestationError::Chain);
        }
    }
    let leaf = &certificates[0];
    if public[0] != 4
        || leaf.public_key().subject_public_key.unused_bits != 0
        || leaf.public_key().subject_public_key.data.as_ref() != public
        || leaf.public_key().algorithm.algorithm.to_id_string() != "1.2.840.10045.2.1"
        || leaf
            .public_key()
            .algorithm
            .parameters
            .as_ref()
            .and_then(|p| p.as_oid().ok())
            .is_none_or(|oid| oid.to_id_string() != "1.2.840.10045.3.1.7")
    {
        return Err(AttestationError::KeyPolicy);
    }
    if leaf
        .key_usage()
        .map_err(|_| AttestationError::Chain)?
        .is_none_or(|usage| !usage.value.digital_signature())
    {
        return Err(AttestationError::Chain);
    }
    let index = certificates
        .iter()
        .rposition(|cert| {
            cert.extensions()
                .iter()
                .any(|e| e.oid.to_id_string() == ATTESTATION)
        })
        .ok_or(AttestationError::Hardware)?;
    if index != 0 {
        return Err(AttestationError::KeyPolicy);
    }
    if let Some(provisioning) = certificates.iter().rposition(|cert| {
        cert.extensions()
            .iter()
            .any(|e| e.oid.to_id_string() == PROVISIONING)
    }) {
        if provisioning != 1 {
            return Err(AttestationError::Chain);
        }
    }
    let actual_now = now
        .duration_since(UNIX_EPOCH)
        .map_err(|_| AttestationError::Chain)?
        .as_secs();
    let factory = X509Certificate::from_der(GOOGLE_ROOTS[0])
        .map_err(|_| AttestationError::Chain)?
        .1;
    let trusted_cert = X509Certificate::from_der(trusted)
        .map_err(|_| AttestationError::Chain)?
        .1;
    revocations.check(trusted_cert.raw_serial())?;
    let legacy = root.public_key().raw == factory.public_key().raw;
    let validation_time = if legacy {
        let from = certificates[..certificates.len() - 1]
            .iter()
            .map(|c| c.validity().not_before.timestamp())
            .max()
            .ok_or(AttestationError::Chain)?;
        let until = certificates[..certificates.len() - 1]
            .iter()
            .map(|c| c.validity().not_after.timestamp())
            .min()
            .ok_or(AttestationError::Chain)?;
        if from > until {
            return Err(AttestationError::Unsupported);
        }
        u64::try_from(from.max(0)).map_err(|_| AttestationError::Unsupported)?
    } else {
        actual_now
    };
    if !legacy
        && (!root.validity().is_valid_at(
            x509_parser::time::ASN1Time::from_timestamp(
                i64::try_from(actual_now).map_err(|_| AttestationError::Chain)?,
            )
            .map_err(|_| AttestationError::Chain)?,
        ) || !trusted_cert.validity().is_valid_at(
            x509_parser::time::ASN1Time::from_timestamp(
                i64::try_from(actual_now).map_err(|_| AttestationError::Chain)?,
            )
            .map_err(|_| AttestationError::Chain)?,
        ))
    {
        return Err(AttestationError::Chain);
    }
    let root_der = CertificateDer::from(trusted);
    let anchors =
        [webpki::anchor_from_trusted_cert(&root_der).map_err(|_| AttestationError::Chain)?];
    let leaf_der = CertificateDer::from(chain[0].as_slice());
    let end = webpki::EndEntityCert::try_from(&leaf_der).map_err(|_| AttestationError::Chain)?;
    let intermediates: Vec<_> = chain[1..chain.len() - 1]
        .iter()
        .map(|bytes| CertificateDer::from(bytes.as_slice()))
        .collect();
    let path = end
        .verify_for_usage(
            webpki::ALL_VERIFICATION_ALGS,
            &anchors,
            &intermediates,
            UnixTime::since_unix_epoch(std::time::Duration::from_secs(validation_time)),
            webpki::KeyUsage::client_auth(),
            None,
            None,
        )
        .map_err(|_| AttestationError::Chain)?;
    let verified: Vec<_> = path.intermediate_certificates().map(|c| c.der()).collect();
    if verified.len() != intermediates.len()
        || verified.iter().zip(&intermediates).any(|(a, b)| a != b)
    {
        return Err(AttestationError::Chain);
    }
    let extension = leaf
        .extensions()
        .iter()
        .find(|e| e.oid.to_id_string() == ATTESTATION)
        .ok_or(AttestationError::Hardware)?;
    Ok(extension.value.to_vec())
}
