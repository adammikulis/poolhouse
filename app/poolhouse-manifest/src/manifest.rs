//! Canonical manifest values. Parsing is not installation authentication.

use serde::Deserialize;
use sha2::{Digest, Sha256};

const LIMIT: usize = 16 * 1024;
const SERVICE: &str = "app.poolhouse.authority";
const DOMAIN: &[u8] = b"poolhouse.native-authority.code-policy.v2\0app.poolhouse.authority\0";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CodePin {
    pub cdhash: [u8; 20],
    pub sha256: [u8; 32],
    pub publisher_kind: u8,
    pub publisher_digest: [u8; 32],
    pub requirement_digest: [u8; 32],
    pub entitlements_digest: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Manifest {
    pub owner_uid: u32,
    pub authority_id: [u8; 32],
    pub installation_id: [u8; 32],
    pub generation: u64,
    pub receipt_public_key: [u8; 32],
    pub policy_digest: [u8; 32],
    pub purpose_policy: [u8; 32],
    pub rotation_policy: [u8; 32],
    pub helper: CodePin,
    pub gui: CodePin,
    pub node: CodePin,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WirePin {
    cdhash: String,
    sha256: String,
    publisher_kind: u8,
    publisher_digest: String,
    requirement_digest: String,
    entitlements_digest: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireHelper {
    service: String,
    cdhash: String,
    sha256: String,
    publisher_kind: u8,
    publisher_digest: String,
    requirement_digest: String,
    entitlements_digest: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireManifest {
    version: u8,
    owner_uid: u32,
    authority_id: String,
    installation_id: String,
    generation: u64,
    receipt_algorithm: String,
    receipt_public_key: String,
    policy_digest: String,
    purpose_policy: String,
    rotation_policy: String,
    helper: WireHelper,
    gui: WirePin,
    node: WirePin,
}

fn hex<const N: usize>(value: &str) -> Option<[u8; N]> {
    let bytes = value.as_bytes();
    if bytes.len() != N * 2 { return None; }
    fn nibble(byte: u8) -> Option<u8> {
        match byte { b'0'..=b'9' => Some(byte - b'0'), b'a'..=b'f' => Some(byte - b'a' + 10), _ => None }
    }
    let mut output = [0; N];
    for (index, slot) in output.iter_mut().enumerate() {
        *slot = (nibble(bytes[index * 2])? << 4) | nibble(bytes[index * 2 + 1])?;
    }
    output.iter().any(|byte| *byte != 0).then_some(output)
}

impl WirePin {
    fn decode(self) -> Option<CodePin> {
        if !matches!(self.publisher_kind, 1 | 2) { return None; }
        Some(CodePin {
            cdhash: hex(&self.cdhash)?, sha256: hex(&self.sha256)?, publisher_kind: self.publisher_kind,
            publisher_digest: hex(&self.publisher_digest)?, requirement_digest: hex(&self.requirement_digest)?,
            entitlements_digest: hex(&self.entitlements_digest)?,
        })
    }
}

impl Manifest {
    /// Parses public values only; callers must authenticate their protected source.
    pub fn parse(bytes: &[u8]) -> Option<Self> {
        if bytes.is_empty() || bytes.len() > LIMIT || bytes.iter().any(|b| *b == b'\\' || *b > 126) { return None; }
        let wire: WireManifest = serde_json::from_slice(bytes).ok()?;
        if wire.version != 2 || wire.owner_uid == 0 || wire.owner_uid == u32::MAX || wire.generation == 0
            || wire.receipt_algorithm != "ed25519" || wire.helper.service != SERVICE { return None; }
        let helper = WirePin {
            cdhash: wire.helper.cdhash, sha256: wire.helper.sha256, publisher_kind: wire.helper.publisher_kind,
            publisher_digest: wire.helper.publisher_digest, requirement_digest: wire.helper.requirement_digest,
            entitlements_digest: wire.helper.entitlements_digest,
        }.decode()?;
        let manifest = Self {
            owner_uid: wire.owner_uid, authority_id: hex(&wire.authority_id)?, installation_id: hex(&wire.installation_id)?,
            generation: wire.generation, receipt_public_key: hex(&wire.receipt_public_key)?, policy_digest: hex(&wire.policy_digest)?,
            purpose_policy: hex(&wire.purpose_policy)?, rotation_policy: hex(&wire.rotation_policy)?,
            helper, gui: wire.gui.decode()?, node: wire.node.decode()?,
        };
        (manifest.digest() == manifest.policy_digest).then_some(manifest)
    }

    fn digest(&self) -> [u8; 32] {
        let mut hash = Sha256::new();
        hash.update(DOMAIN);
        for code in [&self.helper, &self.gui, &self.node] {
            hash.update(code.cdhash); hash.update(code.sha256); hash.update([code.publisher_kind]);
            hash.update(code.publisher_digest); hash.update(code.requirement_digest); hash.update(code.entitlements_digest);
        }
        hash.update(self.purpose_policy); hash.update(self.rotation_policy);
        hash.finalize().into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn text(bytes: &[u8]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }

    fn valid() -> Value {
        let pin = json!({"cdhash": text(&[1; 20]), "sha256": text(&[2; 32]), "publisher_kind": 1,
            "publisher_digest": text(&[3; 32]), "requirement_digest": text(&[4; 32]), "entitlements_digest": text(&[5; 32])});
        let mut helper = pin.clone(); helper["service"] = json!(SERVICE);
        let mut hash = Sha256::new(); hash.update(DOMAIN);
        for _ in 0..3 {
            hash.update([1; 20]); hash.update([2; 32]); hash.update([1]);
            hash.update([3; 32]); hash.update([4; 32]); hash.update([5; 32]);
        }
        hash.update([6; 32]); hash.update([7; 32]);
        json!({"version":2, "owner_uid":501, "authority_id":text(&[8;32]), "installation_id":text(&[9;32]),
            "generation":u64::MAX, "receipt_algorithm":"ed25519", "receipt_public_key":text(&[10;32]),
            "policy_digest":text(&hash.finalize()), "purpose_policy":text(&[6;32]), "rotation_policy":text(&[7;32]),
            "helper":helper, "gui":pin, "node":pin})
    }

    #[test]
    fn closed_manifest_and_policy_digest() {
        let valid = valid(); let bytes = serde_json::to_vec(&valid).unwrap();
        let parsed = Manifest::parse(&bytes).unwrap();
        assert_eq!(parsed.owner_uid, 501); assert_eq!(parsed.generation, u64::MAX);
        let encoded = String::from_utf8(bytes).unwrap();
        for changed in [encoded.replacen("\"version\":2", "\"version\":2,\"version\":2", 1),
                        encoded.replacen("\"version\":2", "\"vers\\u0069on\":2", 1),
                        encoded.replacen("\"version\":2", "\"version\":02", 1),
                        format!("{encoded}{{}}"), " ".repeat(LIMIT + 1)] {
            assert!(Manifest::parse(changed.as_bytes()).is_none());
        }
        for (field, value) in [("version", json!(1)), ("owner_uid", json!(true)), ("owner_uid", json!(0)),
                               ("owner_uid", json!(u32::MAX)), ("generation", json!(0)),
                               ("receipt_algorithm", json!("other")), ("unknown", json!(1)),
                               ("policy_digest", json!(text(&[0;32])))] {
            let mut invalid = valid.clone(); invalid[field] = value;
            assert!(Manifest::parse(&serde_json::to_vec(&invalid).unwrap()).is_none(), "{field}");
        }
        let mut invalid = valid; invalid["gui"]["publisher_kind"] = json!(3);
        assert!(Manifest::parse(&serde_json::to_vec(&invalid).unwrap()).is_none());
    }
}
