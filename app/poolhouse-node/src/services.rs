//! Node-authorized subordinate compute certificates.

use std::collections::BTreeMap;
use std::net::IpAddr;

use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::cert::{cert_fingerprint, device_public, valid_fingerprint};
use crate::error::{Error, Result};
use crate::fsutil::{hex, unhex};
use crate::node::Node;

const DOMAIN: &[u8] = b"poolhouse/compute-service/v1\n";
pub const SERVICE: &str = "compute";

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Body {
    pub pool: String,
    pub device: String,
    pub service: String,
    pub cert: String,
    pub fingerprint: String,
    pub endpoint: String,
    pub generation: String,
    pub not_before_ms: u64,
    pub not_after_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Binding { pub body: Body, pub signature: String }

fn denied() -> Error { Error::Denied("this certificate has no current compute service binding".into()) }
fn short_hash(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn bytes(body: &Body) -> Result<Vec<u8>> {
    let mut out = DOMAIN.to_vec();
    out.extend(crate::row::canonical(&serde_json::to_value(body)?).as_bytes());
    Ok(out)
}

pub fn endpoint_valid(endpoint: &str) -> bool {
    let Some(authority) = endpoint.strip_prefix("https://") else { return false };
    if authority.bytes().any(|c| !c.is_ascii_graphic() || b"/@?#\\%".contains(&c)) { return false; }
    let Some((host, port)) = authority.rsplit_once(':') else { return false };
    if !port.parse::<u16>().is_ok_and(|p| p > 0) { return false; }
    if let Some(ip) = host.strip_prefix('[').and_then(|h| h.strip_suffix(']')) {
        return ip.parse::<std::net::Ipv6Addr>().is_ok();
    }
    host.parse::<IpAddr>().is_ok() || (!host.is_empty() && host.len() <= 253 && host.split('.').all(|label| {
        !label.is_empty() && label.len() <= 63 && !label.starts_with('-') && !label.ends_with('-')
            && label.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
    }))
}

impl Body {
    pub fn valid(&self, now: u64) -> bool {
        self.service == SERVICE && short_hash(&self.pool) && valid_fingerprint(&self.device)
            && valid_fingerprint(&self.fingerprint) && short_hash(&self.generation)
            && self.not_before_ms <= now && now < self.not_after_ms && endpoint_valid(&self.endpoint)
            && unhex(&self.cert).is_some_and(|der| hex(&der) == self.cert && cert_fingerprint(&der) == self.fingerprint
                && certificate(&der) == Some((self.not_before_ms, self.not_after_ms)))
    }
}

fn certificate(der: &[u8]) -> Option<(u64, u64)> {
    use x509_parser::prelude::{FromDer, X509Certificate};
    let (rest, parsed) = X509Certificate::from_der(der).ok()?;
    if !rest.is_empty() || parsed.public_key().algorithm.algorithm.to_id_string() != "1.2.840.10045.2.1"
        || !parsed.public_key().algorithm.parameters.as_ref()?.as_oid().ok()
            .is_some_and(|oid| oid.to_id_string() == "1.2.840.10045.3.1.7") { return None; }
    let timestamp = |seconds: i64| u64::try_from(seconds).ok()?.checked_mul(1000);
    Some((timestamp(parsed.validity().not_before.timestamp())?, timestamp(parsed.validity().not_after.timestamp())?))
}

impl Binding {
    pub fn signed(body: Body, key: &SigningKey) -> Result<Self> {
        Ok(Self { signature: hex(&key.sign(&bytes(&body)?).to_bytes()), body })
    }

    pub fn verify(&self, pool: &str, device: &str, device_cert: &[u8], now: u64) -> Result<()> {
        if self.body.pool != pool || self.body.device != device || cert_fingerprint(device_cert) != device || !self.body.valid(now) {
            return Err(denied());
        }
        let signature: [u8; 64] = unhex(&self.signature).and_then(|v| v.try_into().ok()).ok_or_else(denied)?;
        let key = VerifyingKey::from_bytes(&device_public(device_cert)?).map_err(|_| denied())?;
        key.verify(&bytes(&self.body)?, &Signature::from_bytes(&signature)).map_err(|_| denied())
    }

    pub fn resource(&self) -> Result<String> {
        Ok(format!("service:{}", crate::fsutil::sha256_hex(&bytes(&self.body)?)))
    }
}

pub struct Services {
    bindings: BTreeMap<String, Binding>,
    epoch: String,
    own_process: Option<crate::lineage::Anchor>,
}

impl Default for Services {
    fn default() -> Self { Self { bindings: BTreeMap::new(), epoch: String::new(), own_process: None } }
}

impl Services {
    pub fn new() -> Result<Self> {
        Ok(Self { epoch: crate::fsutil::random_hex(16)?, ..Self::default() })
    }

    pub fn take(&mut self, binding: Binding, pool: &str, device: &str, device_cert: &[u8], now: u64) -> Result<()> {
        binding.verify(pool, device, device_cert, now)?;
        if self.bindings.get(device).is_some_and(|old| old.body.generation == binding.body.generation && old != &binding) { return Err(denied()); }
        if self.bindings.iter().any(|(fp, other)| fp != device && other.body.pool == pool
            && other.body.fingerprint == binding.body.fingerprint) { return Err(denied()); }
        self.bindings.insert(device.to_string(), binding);
        Ok(())
    }

    pub fn binding(&self, device: &str) -> Option<&Binding> { self.bindings.get(device) }
}

impl Node {
    fn service_row(&self, binding: &Binding) -> Result<Value> {
        let body = &binding.body;
        if body.device == self.cert.fingerprint() && !self.services.own_process.is_some_and(|anchor| {
            crate::lease::live::inspect(anchor.pid).is_some_and(|p| !p.zombie && p.start == anchor.start)
        }) { return Err(denied()); }
        let member = self.members.get(&body.device).filter(|_| self.members.is_active(&body.device)).ok_or_else(denied)?;
        binding.verify(&self.members.id, &body.device, &member.der().ok_or_else(denied)?, self.now_ms())?;
        Ok(json!({"pool": body.pool, "device": body.device, "service": body.service, "name": member.name,
            "cert": body.cert, "fingerprint": body.fingerprint, "endpoint": body.endpoint, "generation": body.generation}))
    }

    pub fn service_members(&mut self) -> Result<Value> {
        self.gov_tick()?;
        let services: Vec<Value> = self.services.bindings.values().filter_map(|binding| self.service_row(binding).ok()).collect();
        Ok(json!({"pool": self.members.id, "services": services}))
    }

    pub fn service_authorize(&mut self, fingerprint: &str) -> Result<Value> {
        self.gov_tick()?;
        if !valid_fingerprint(fingerprint) { return Err(denied()); }
        let mut found = self.services.bindings.values().filter(|binding| binding.body.fingerprint == fingerprint)
            .filter_map(|binding| self.service_row(binding).ok());
        let one = found.next().ok_or_else(denied)?;
        if found.next().is_some() { return Err(denied()); }
        Ok(one)
    }

    pub fn service_authorize_local(&mut self, fingerprint: &str) -> Result<Value> {
        let row = self.service_authorize(fingerprint)?;
        if row["device"] != self.cert.fingerprint() { return Err(denied()); }
        Ok(row)
    }

    pub fn take_service_binding(&mut self, from: &str, value: &Value) -> Result<()> {
        if !self.members.is_active(from) || from == self.cert.fingerprint() { return Err(denied()); }
        if value.is_null() { self.services.bindings.remove(from); return Ok(()); }
        let binding: Binding = serde_json::from_value(value.clone()).map_err(|_| denied())?;
        let member = self.members.get(from).filter(|_| self.members.is_active(from)).ok_or_else(denied)?;
        let der = member.der().ok_or_else(denied)?;
        self.services.take(binding, &self.members.id, from, &der, self.now_ms())
    }

    pub fn own_service_binding(&self) -> Value {
        self.services.binding(&self.cert.fingerprint()).filter(|binding| self.service_row(binding).is_ok())
            .and_then(|binding| serde_json::to_value(binding).ok()).unwrap_or(Value::Null)
    }

    fn service_draft_body(&self, cert: &str, endpoint: &str) -> Result<(Body, crate::lineage::Anchor)> {
        let pid = self.asking.peer.pid.ok_or_else(|| Error::Denied("the compute service caller process is unavailable".into()))?;
        let process = crate::lease::live::inspect(pid).filter(|p| !p.zombie && p.start > 0).ok_or_else(denied)?;
        let anchor = crate::lineage::Anchor { pid, start: process.start };
        let der = unhex(cert).filter(|der| hex(der) == cert).ok_or_else(denied)?;
        let (not_before_ms, not_after_ms) = certificate(&der).ok_or_else(denied)?;
        let generation = crate::fsutil::sha256_hex(format!("{}:{pid}:{}:{cert}:{endpoint}", self.services.epoch, anchor.start).as_bytes())[..32].to_string();
        let body = Body { pool: self.members.id.clone(), device: self.cert.fingerprint(), service: SERVICE.into(),
            cert: cert.into(), fingerprint: cert_fingerprint(&der), endpoint: endpoint.into(), generation,
            not_before_ms, not_after_ms };
        if !body.valid(self.now_ms()) { return Err(denied()); }
        Ok((body, anchor))
    }

    pub fn service_draft(&self, cert: &str, endpoint: &str) -> Result<Value> {
        let (body, _) = self.service_draft_body(cert, endpoint)?;
        let binding = Binding { body, signature: String::new() };
        Ok(json!({"binding": binding.body, "resource": binding.resource()?}))
    }

    pub fn service_bind(&mut self, token: &str, cert: &str, endpoint: &str) -> Result<Value> {
        let (body, anchor) = self.service_draft_body(cert, endpoint)?;
        if self.services.binding(&body.device).is_some_and(|held| held.body == body) {
            return self.service_authorize(&body.fingerprint);
        }
        let binding = Binding::signed(body, &self.key)?;
        let resource = binding.resource()?;
        let who = crate::poolapi::authorize(self, token, "service_bind", &resource)?;
        self.services.take(binding.clone(), &self.members.id, &self.cert.fingerprint(), &self.cert.der, self.now_ms())?;
        self.services.own_process = Some(anchor);
        self.record_event("service_bind", &binding.body.fingerprint, &format!("{} by {}/{}", binding.body.endpoint, who.board, who.name))?;
        self.service_authorize(&binding.body.fingerprint)
    }
}

pub fn call(node: &mut Node, method: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let text = |key: &str| p.get(key).and_then(Value::as_str).ok_or_else(|| Error::Invalid(format!("{key} is text")));
    match method {
        "service_draft" => node.service_draft(text("cert")?, text("endpoint")?),
        "service_bind" => node.service_bind(token, text("cert")?, text("endpoint")?),
        _ => Err(Error::Invalid("unknown service method".into())),
    }
}

impl crate::net::Net {
    pub(crate) fn authorize_service(&self, fingerprint: &str) -> Result<Value> {
        use crate::net::tracked_node;
        use crate::node::update_activity::Source;
        let (row, device, address) = {
            let mut node = tracked_node(&self.node, Source::Network)?;
            let row = node.service_authorize(fingerprint)?;
            let device = row["device"].as_str().ok_or_else(denied)?.to_string();
            if device == node.cert.fingerprint() { return Ok(row); }
            let address = node.facts.peers.get(&device).and_then(|f| f.addr.as_deref()).and_then(|a| a.parse().ok());
            (row, device, address)
        };
        let reply = self.call_member(&device, address, &json!({"op": "service_binding", "fingerprint": fingerprint, "generation": row["generation"]}))?;
        let mut node = tracked_node(&self.node, Source::Network)?;
        node.take_service_binding(&device, &reply)?;
        let current = node.service_authorize(fingerprint)?;
        if current != row { return Err(denied()); }
        Ok(current)
    }
}

pub fn peer_binding(node: &mut Node, request: &Value) -> Result<Value> {
    if !request.as_object().is_some_and(|m| m.len() == 3 && m.keys().all(|k| matches!(k.as_str(), "op" | "fingerprint" | "generation"))) {
        return Err(Error::Invalid("a service binding request names its certificate and generation".into()));
    }
    let own = node.own_service_binding();
    if own.is_null() || own["body"]["fingerprint"] != request["fingerprint"] || own["body"]["generation"] != request["generation"] { return Err(denied()); }
    Ok(own)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (SigningKey, Vec<u8>, Binding) {
        let dir = tempfile::tempdir().unwrap();
        let key = SigningKey::from_bytes(&[11; 32]);
        let cert = crate::cert::load_or_create(dir.path(), &key).unwrap();
        let service = service_cert(); let service_der = unhex(&service).unwrap();
        let (not_before_ms, not_after_ms) = certificate(&service_der).unwrap();
        let body = Body { pool: "a".repeat(32), device: cert.fingerprint(), service: SERVICE.into(), cert: service,
            fingerprint: cert_fingerprint(&service_der), endpoint: "https://127.0.0.1:8770".into(), generation: "1".repeat(32),
            not_before_ms, not_after_ms };
        let binding = Binding::signed(body, &key).unwrap();
        (key, cert.der, binding)
    }
    #[test]
    fn forged_signature_wrong_pool_and_wrong_member_are_refused() {
        let (_, cert, binding) = fixture(); let body = &binding.body;
        let now = body.not_before_ms + 1;
        assert!(binding.verify(&body.pool, &body.device, &cert, now).is_ok());
        assert!(binding.verify(&"b".repeat(32), &body.device, &cert, now).is_err());
        assert!(binding.verify(&body.pool, &"c".repeat(64), &cert, now).is_err());
        let mut forged = binding.clone(); forged.signature = "0".repeat(128);
        assert!(forged.verify(&body.pool, &body.device, &cert, now).is_err());
    }
    #[test]
    fn signed_endpoint_service_and_generation_cannot_be_rewritten() {
        let (_, cert, binding) = fixture(); let original = &binding.body;
        for field in ["endpoint", "service", "generation"] {
            let mut value = serde_json::to_value(&binding).unwrap();
            value["body"][field] = json!("attacker");
            let changed: Binding = serde_json::from_value(value).unwrap();
            assert!(changed.verify(&original.pool, &original.device, &cert, original.not_before_ms + 1).is_err());
        }
        assert!(serde_json::from_value::<Binding>(json!({"body": original, "signature": binding.signature, "owner": "x"})).is_err());
    }
    #[test]
    fn validity_and_certificate_hash_are_checked_before_authorization() {
        let (key, cert, binding) = fixture(); let body = &binding.body;
        for now in [body.not_before_ms - 1, body.not_after_ms] { assert!(binding.verify(&body.pool, &body.device, &cert, now).is_err()); }
        let mut changed = body.clone(); changed.fingerprint = "f".repeat(64);
        assert!(Binding::signed(changed, &key).unwrap().verify(&body.pool, &body.device, &cert, body.not_before_ms + 1).is_err());
    }
    #[test]
    fn service_locations_exclude_credentials_paths_and_unbounded_destinations() {
        for endpoint in ["http://localhost:1", "https://user@localhost:1", "https://localhost:1/path", "https://localhost:1?x", "https://localhost:0", "https://localhost:65536", "https://localhost:1\\x"] {
            assert!(!endpoint_valid(endpoint), "{endpoint}");
        }
        assert!(endpoint_valid("https://[::1]:8770"));
    }
    #[test]
    fn replacing_generation_replaces_the_authoritative_certificate() {
        let (key, cert, first) = fixture(); let body = &first.body;
        let mut services = Services::default();
        let now = body.not_before_ms + 1;
        services.take(first.clone(), &body.pool, &body.device, &cert, now).unwrap();
        let mut forged_same_generation = body.clone(); forged_same_generation.endpoint = "https://localhost:9999".into();
        assert!(services.take(Binding::signed(forged_same_generation, &key).unwrap(), &body.pool, &body.device, &cert, now).is_err());
        let mut next = body.clone(); next.generation = "2".repeat(32);
        let next = Binding::signed(next, &key).unwrap();
        services.take(next.clone(), &body.pool, &body.device, &cert, now).unwrap();
        assert_eq!(services.binding(&body.device), Some(&next));
        assert_ne!(first.resource().unwrap(), next.resource().unwrap());
    }
    fn service_cert() -> String {
        let key = rcgen::KeyPair::generate().unwrap();
        let params = rcgen::CertificateParams::new(Vec::<String>::new()).unwrap();
        hex(params.self_signed(&key).unwrap().der())
    }
    #[test]
    fn draft_generation_is_bound_to_actual_process_and_node_lifetime() {
        let dir = tempfile::tempdir().unwrap();
        let mut node = Node::open(dir.path()).unwrap();
        node.members.id = "a".repeat(32);
        let cert = service_cert();
        assert!(node.service_draft(&cert, "https://localhost:8770").is_err());
        node.asking.peer.pid = Some(std::process::id());
        let first = node.service_draft(&cert, "https://localhost:8770").unwrap();
        assert_eq!(first, node.service_draft(&cert, "https://localhost:8770").unwrap());
        let endpoint = node.service_draft(&cert, "https://localhost:8771").unwrap();
        assert_ne!(first["resource"], endpoint["resource"]);
        drop(node);
        let mut restarted = Node::open(dir.path()).unwrap(); restarted.members.id = "a".repeat(32); restarted.asking.peer.pid = Some(std::process::id());
        assert_ne!(first["resource"], restarted.service_draft(&cert, "https://localhost:8770").unwrap()["resource"]);
    }
    #[test]
    fn an_unapproved_local_process_cannot_publish_its_service_certificate() {
        let dir = tempfile::tempdir().unwrap(); let mut node = Node::open(dir.path()).unwrap();
        node.members.id = "a".repeat(32);
        node.asking.peer.pid = Some(std::process::id());
        let cert = service_cert();
        assert!(node.service_bind("", &cert, "https://localhost:8770").is_err());
        assert_eq!(node.service_members().unwrap()["services"], json!([]));
        let cert_fp = cert_fingerprint(&unhex(&cert).unwrap());
        assert!(node.service_authorize(&cert_fp).is_err());
        assert!(node.take_service_binding(&"0".repeat(64), &Value::Null).is_err());
    }
    #[test]
    fn binding_grants_cannot_wildcard_devices_resources_or_repeat_uses() {
        let resource = format!("service:{}", "a".repeat(64));
        let base = json!({"op":"issue","board":"pool","gid":"g-1111111111111111", "action":"service_bind",
            "person":"a".repeat(64), "to":{"kind":"session","name":"agent-test"},
            "scope":{"board":"pool","device":"b".repeat(64),"resource":resource},
            "at_ms":100,"until_ms":1000,"uses":1});
        assert!(crate::grants::spec::parse_issue(base.as_object().unwrap()).is_ok());
        for field in ["device", "resource"] {
            let mut changed = base.clone(); changed["scope"][field] = json!("*");
            assert!(crate::grants::spec::parse_issue(changed.as_object().unwrap()).is_err());
        }
        let mut changed = base; changed["uses"] = json!(2);
        assert!(crate::grants::spec::parse_issue(changed.as_object().unwrap()).is_err());
    }
}
