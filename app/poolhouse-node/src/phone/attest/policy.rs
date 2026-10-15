use std::collections::BTreeMap;

use super::der::{self, Value};
use super::{AttestationError, HardwareLevel, Result};

fn fields(bytes: &[u8]) -> Result<BTreeMap<u32, &[u8]>> {
    let mut fields = BTreeMap::new();
    let mut rest = bytes;
    while !rest.is_empty() {
        let value = der::take(&mut rest)?;
        if value.class != 2 || !value.constructed || fields.insert(value.tag, value.bytes).is_some()
        {
            return Err(AttestationError::Encoding);
        }
    }
    Ok(fields)
}

fn integer(fields: &BTreeMap<u32, &[u8]>, tag: u32, expected: u64) -> Result<()> {
    let bytes = fields.get(&tag).ok_or(AttestationError::KeyPolicy)?;
    if der::number(der::one(bytes, 2)?, 2)? != expected {
        return Err(AttestationError::KeyPolicy);
    }
    Ok(())
}

fn singleton(fields: &BTreeMap<u32, &[u8]>, tag: u32, expected: u64) -> Result<()> {
    let bytes = fields.get(&tag).ok_or(AttestationError::KeyPolicy)?;
    if der::number(der::one(der::one(bytes, 17)?.bytes, 2)?, 2)? != expected {
        return Err(AttestationError::KeyPolicy);
    }
    Ok(())
}

fn application(bytes: &[u8], signer: &[u8; 32]) -> Result<()> {
    let mut rest = der::one(bytes, 16)?.bytes;
    let packages = der::take(&mut rest)?;
    let signatures = der::take(&mut rest)?;
    if !rest.is_empty()
        || packages.class != 0
        || packages.tag != 17
        || !packages.constructed
        || signatures.class != 0
        || signatures.tag != 17
        || !signatures.constructed
    {
        return Err(AttestationError::Application);
    }
    let mut package = der::one(packages.bytes, 16)?.bytes;
    let name = der::take(&mut package)?;
    let version = der::take(&mut package)?;
    if !package.is_empty()
        || name.class != 0
        || name.tag != 4
        || name.constructed
        || name.bytes != b"app.poolhouse.phone"
        || der::number(version, 2)? == 0
    {
        return Err(AttestationError::Application);
    }
    if der::one(signatures.bytes, 4)?.bytes != signer {
        return Err(AttestationError::Application);
    }
    Ok(())
}

fn boot(bytes: &[u8]) -> Result<()> {
    let mut rest = der::one(bytes, 16)?.bytes;
    let key = der::take(&mut rest)?;
    let locked = der::take(&mut rest)?;
    let state = der::take(&mut rest)?;
    let hash = der::take(&mut rest)?;
    if !rest.is_empty()
        || key.class != 0
        || key.tag != 4
        || key.constructed
        || key.bytes.len() != 32
        || key.bytes.iter().all(|b| *b == 0)
        || locked.class != 0
        || locked.tag != 1
        || locked.constructed
        || locked.bytes != [255]
        || der::number(state, 10)? != 0
        || hash.class != 0
        || hash.tag != 4
        || hash.constructed
        || hash.bytes.len() != 32
    {
        return Err(AttestationError::Boot);
    }
    Ok(())
}

fn security(value: Value<'_>) -> Result<HardwareLevel> {
    match der::number(value, 10)? {
        1 => Ok(HardwareLevel::TrustedEnvironment),
        2 => Ok(HardwareLevel::StrongBox),
        _ => Err(AttestationError::Hardware),
    }
}

pub(super) fn check(
    extension: &[u8],
    challenge: &[u8; 32],
    signer: &[u8; 32],
) -> Result<HardwareLevel> {
    der::bounded(extension, 0)?;
    let mut rest = der::one(extension, 16)?.bytes;
    let version = der::number(der::take(&mut rest)?, 2)?;
    let attestation_level = security(der::take(&mut rest)?)?;
    let key_version = der::number(der::take(&mut rest)?, 2)?;
    let key_level = security(der::take(&mut rest)?)?;
    let got_challenge = der::take(&mut rest)?;
    let unique = der::take(&mut rest)?;
    let software = der::take(&mut rest)?;
    let hardware = der::take(&mut rest)?;
    if !rest.is_empty()
        || !matches!(version, 3 | 4 | 100 | 200 | 300 | 400)
        || !matches!(key_version, 4 | 41 | 100 | 200 | 300 | 400)
        || attestation_level != key_level
        || got_challenge.class != 0
        || got_challenge.tag != 4
        || got_challenge.constructed
        || got_challenge.bytes != challenge
        || unique.class != 0
        || unique.tag != 4
        || unique.constructed
        || !unique.bytes.is_empty()
        || software.class != 0
        || software.tag != 16
        || !software.constructed
        || hardware.class != 0
        || hardware.tag != 16
        || !hardware.constructed
    {
        return Err(AttestationError::KeyPolicy);
    }
    let software = fields(software.bytes)?;
    let hardware = fields(hardware.bytes)?;
    for tag in [1, 2, 3, 5, 10, 503, 504, 505, 506, 600, 702, 704] {
        if software.contains_key(&tag) {
            return Err(AttestationError::KeyPolicy);
        }
    }
    if [503, 505, 506, 600]
        .iter()
        .any(|tag| hardware.contains_key(tag))
        || hardware.contains_key(&709)
    {
        return Err(AttestationError::KeyPolicy);
    }
    singleton(&hardware, 1, 2)?;
    integer(&hardware, 2, 3)?;
    integer(&hardware, 3, 256)?;
    singleton(&hardware, 5, 4)?;
    integer(&hardware, 10, 1)?;
    integer(&hardware, 504, 2)?;
    integer(&hardware, 702, 0)?;
    boot(hardware.get(&704).ok_or(AttestationError::Boot)?)?;
    let app = der::one(software.get(&709).ok_or(AttestationError::Application)?, 4)?;
    der::bounded(app.bytes, 0)?;
    application(app.bytes, signer)?;
    Ok(key_level)
}
