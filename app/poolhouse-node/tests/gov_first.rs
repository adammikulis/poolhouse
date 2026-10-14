//! The first device is in the founding charter: the keys that accept the charter accept that computer as the pool's first member, so
//! founding is one signature and no admit follows. A node founds only a charter that names it, a pool of another charter version is not
//! founded, and a key an agent holds itself founds nothing.

mod kit;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use ed25519_dalek::pkcs8::EncodePrivateKey;
use ed25519_dalek::SigningKey;
use kit::gov::*;
use kit::govnode::*;
use kit::netkit::*;
use poolhouse_node::fsutil::hex;
use poolhouse_node::gov::charter::{Charter, First, Unit};
use poolhouse_node::gov::pool::{founding, GovPool, Ingest};
use poolhouse_node::gov::signer::{RigSigner, Signer, SignerKey};
use poolhouse_node::membership::{Policy, Pool, Status};
use poolhouse_node::poolops::wall_ms;
use rcgen::{CertificateParams, DistinguishedName, DnType, KeyPair, PKCS_ED25519};
use rustls::pki_types::PrivatePkcs8KeyDer;
use serde_json::{json, Value};

const A: u8 = 1;

/// The certificate of the node of ``dev``.
fn own(dev: &Dev) -> Vec<u8> {
    dev.node.lock().unwrap().cert.der.clone()
}

/// A certificate for the Ed25519 key ``key`` under another name than the one a device gives its own.
fn reissued(key: &SigningKey) -> Vec<u8> {
    let pkcs8 = key.to_pkcs8_der().unwrap();
    let signing = KeyPair::from_pkcs8_der_and_sign_algo(&PrivatePkcs8KeyDer::from(pkcs8.as_bytes().to_vec()), &PKCS_ED25519).unwrap();
    let mut name = DistinguishedName::new();
    name.push(DnType::CommonName, "another-name");
    let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
    params.distinguished_name = name;
    params.self_signed(&signing).unwrap().der().to_vec()
}

/// A well formed certificate of an ECDSA key, which no device is made from.
fn ecdsa_cert() -> Vec<u8> {
    let signing = KeyPair::generate().unwrap();
    CertificateParams::new(Vec::<String>::new()).unwrap().self_signed(&signing).unwrap().der().to_vec()
}

/// The founding act of a charter of ann's key that puts ``first`` in the pool first.
fn act_naming(first: First) -> Value {
    serde_json::to_value(found_act(&charter_of(&[strong(ANN, "ann")], Policy::Secure, Unit::Person, wall_ms(), first))).unwrap()
}

fn refusal(reply: &Value) -> String {
    assert_eq!(reply["ok"], false, "{reply}");
    reply["error"]["say"].as_str().unwrap_or_default().to_string()
}

/// A person that counts what it is asked to sign and keeps the sentences it was shown.
struct Counting {
    inner: RigSigner,
    asked: AtomicUsize,
    shown: Mutex<Vec<String>>,
}

impl Counting {
    fn of(seed: u8) -> Arc<Counting> {
        Arc::new(Counting { inner: RigSigner::new().with_key(seed, "ann"), asked: AtomicUsize::new(0), shown: Mutex::new(Vec::new()) })
    }
}

impl Signer for Counting {
    fn keys(&self) -> Vec<SignerKey> {
        self.inner.keys()
    }

    fn sign(&self, public: &str, message: &[u8], summary: &str) -> poolhouse_node::error::Result<String> {
        self.asked.fetch_add(1, Ordering::SeqCst);
        self.shown.lock().unwrap().push(summary.to_string());
        self.inner.sign(public, message, summary)
    }
}

// -- the charter admits its first device --------------------------------------------------------------------------------------------

#[test]
fn the_first_device_is_a_member_of_the_pool_its_charter_makes_with_no_act_held() {
    let (_d, g) = pool(&[strong(A, "ann")], Policy::Secure);
    let first = ident(FIRST);
    let held = g.devices().get(&first.fingerprint()).cloned().expect("the charter's first device is in the roster");
    assert_eq!((held.status, held.by.as_str(), held.name.as_str()), (Status::Active, "found", "first"));
    assert_eq!(held.cert, hex(&first.der));
    assert!(g.acts().is_empty(), "no admit was made for it");
    assert_eq!(g.devices().len(), 1);
}

#[test]
fn the_first_device_is_the_pools_first_member_on_the_device_that_founds_it_and_on_one_that_joins() {
    let (a, b, _c) = pool_of_three();
    for dev in [&a, &b] {
        let first = dev.member(&a.fp()).unwrap_or_else(|| panic!("{} does not list the first device", dev.fp()));
        assert_eq!((first["status"].as_str(), first["by"].as_str()), (Some("active"), Some("found")), "{first}");
    }
    let held = a.node.lock().unwrap();
    assert!(held.members.gov().unwrap().acts().values().all(|act| act.body["cert"] != json!(hex(&held.cert.der))), "no act admits the first device");
}

#[test]
fn founding_a_pool_asks_for_one_signature_and_says_which_computer_is_in_it() {
    let a = device();
    let person = Counting::of(ANN);
    enrol_as_phone_keys(&a, &RigSigner::new().with_key(ANN, "ann"));
    a.node.lock().unwrap().set_signer(person.clone());
    let made = a.ok("pool_create", "", &a.token, json!({"title": "home", "policy": "secure", "project": ""}));
    assert_eq!(person.asked.load(Ordering::SeqCst), 1, "the acceptance of the charter is the only signature founding takes");
    let shown = person.shown.lock().unwrap().clone();
    assert!(shown[0].contains("Make the pool 'home'") && shown[0].contains("put the computer"), "{shown:?}");
    assert!(a.is_member(&a.fp()), "this device is in the pool");
    assert_eq!(a.member(&a.fp()).unwrap()["by"], "found");
    assert!(a.node.lock().unwrap().members.gov().unwrap().acts().is_empty(), "and no act follows the charter");
    assert_eq!(made["pool"], a.pool());
}

#[test]
fn a_drafted_charter_names_this_device_first_and_the_card_says_so() {
    let a = device();
    enrol_phone_key(&a, ANN);
    let draft = a.ok("pool_draft_found", "", &a.token, json!({"title": "home", "policy": "secure", "project": "", "keys": [{"key": public(ANN), "person": "ann", "label": "ann's key"}]}));
    assert_eq!(draft["charter"]["first"]["cert"], hex(&own(&a)), "{draft}");
    assert!(!draft["charter"]["first"]["name"].as_str().unwrap().is_empty(), "{draft}");
    let summary = draft["summary"].as_str().unwrap();
    assert!(summary.contains("Make the pool 'home'") && summary.contains("put the computer") && summary.contains(&a.fp()[..12]), "{summary}");
    let mut charter = draft["charter"].clone();
    charter["admins"][0]["acceptance"] = json!(RigSigner::sign_as(ANN, &poolhouse_node::fsutil::unhex(draft["accept"][0]["sign_bytes"].as_str().unwrap()).unwrap()));
    a.ok("pool_found", "", &a.token, json!({"charter": charter}));
    assert!(a.is_member(&a.fp()), "the pool this device founds from its own draft has it as a member at once");
}

// -- the first device is part of what the keys accept -----------------------------------------------------------------------------

#[test]
fn the_first_device_is_part_of_the_anchor_and_of_what_each_key_accepted() {
    let one = charter_of(&[strong(A, "ann")], Policy::Secure, Unit::Person, 1_000_000, first_of(&ident(FIRST).der));
    let mut other = one.clone();
    other.first = first_of(&ident(11).der);
    assert_ne!(one.pool_id(), other.pool_id(), "another first device is another pool");
    let mut renamed = one.clone();
    renamed.first.name = "another".into();
    assert_ne!(one.pool_id(), renamed.pool_id(), "so is the name the keys saw");
    assert!(founding(&one.found_act()).is_ok());
    for changed in [&other, &renamed] {
        let err = founding(&changed.found_act()).unwrap_err().to_string();
        assert!(err.contains("accept"), "the key accepted the charter that named the other device: {err}");
    }
}

#[test]
fn a_first_device_that_is_not_the_certificate_of_an_ed25519_device_is_no_charter() {
    let own = ident(FIRST).der;
    let with = |first: First| charter_of(&[strong(A, "ann")], Policy::Secure, Unit::Person, wall_ms(), first).check_founding().unwrap_err().to_string();
    assert!(with(First { cert: "zz".into(), name: "x".into() }).contains("first device"), "not hex");
    assert!(with(First { cert: hex(&own[..own.len() - 1]), name: "x".into() }).contains("first device"), "a cut certificate");
    assert!(with(First { cert: hex(&ecdsa_cert()), name: "x".into() }).contains("Ed25519"), "a certificate of another kind of key");
    assert!(with(First { cert: hex(&own).to_uppercase(), name: "x".into() }).contains("first device"), "one device is one spelling");
    assert!(with(First { cert: hex(&own), name: String::new() }).contains("name"), "no name");
    assert!(with(First { cert: hex(&own), name: "n".repeat(41) }).contains("name"), "a long name");
    assert!(with(First { cert: hex(&own), name: "a\nb".into() }).contains("name"), "a control character");
    assert!(charter_of(&[strong(A, "ann")], Policy::Secure, Unit::Person, wall_ms(), first_of(&own)).check_founding().is_ok());
}

// -- a node founds only the charter that names it -----------------------------------------------------------------------------------

#[test]
fn a_charter_that_names_another_device_first_founds_nothing_on_this_one() {
    let a = device();
    enrol_phone_key(&a, ANN);
    let before = a.pool();
    let elsewhere = [
        ("another device", ident(11).der),
        ("this device's key under another certificate", reissued_for(&a)),
    ];
    for (what, der) in elsewhere {
        let said = refusal(&a.call("pool_found", "", &a.token, json!({"act": act_naming(first_of(&der))})));
        assert!(said.contains("another computer"), "{what}: {said}");
        assert_eq!(a.pool(), before, "{what}: it is still in no pool");
        assert_eq!(a.status()["governance"]["mode"], "unfounded");
    }
    let bad = [("a certificate of an ECDSA key", ecdsa_cert()), ("bytes that are no certificate", vec![1, 2, 3])];
    for (what, der) in bad {
        let said = refusal(&a.call("pool_found", "", &a.token, json!({"act": act_naming(first_of(&der))})));
        assert!(said.contains("first device"), "{what}: {said}");
        assert_eq!(a.status()["governance"]["mode"], "unfounded", "{what}");
    }
    let named = a.call("pool_found", "", &a.token, json!({"act": act_naming(first_of(&own(&a)))}));
    assert_eq!(named["ok"], true, "the same charter that names this device founds the pool: {named}");
}

/// A certificate with the key of the node of ``dev`` that is not the node's own.
fn reissued_for(dev: &Dev) -> Vec<u8> {
    let node = dev.node.lock().unwrap();
    let der = reissued(&node.key);
    assert_ne!(der, node.cert.der);
    assert_eq!(poolhouse_node::cert::board_fingerprint(&der).unwrap(), poolhouse_node::cert::board_fingerprint(&node.cert.der).unwrap(), "the same key");
    der
}

#[test]
fn a_charter_copied_to_another_device_makes_no_pool_and_no_member_there() {
    let (a, b) = (device(), device());
    enrol_phone_key(&a, ANN);
    enrol_phone_key(&b, ANN);
    let act = act_naming(first_of(&own(&a)));
    let founded = a.ok("pool_found", "", &a.token, json!({"act": act}));
    let before = b.pool();
    let said = refusal(&b.call("pool_found", "", &b.token, json!({"act": act})));
    assert!(said.contains("another computer"), "{said}");
    assert_eq!(b.pool(), before, "the copy made no pool");
    assert_ne!(b.pool(), founded["pool"].as_str().unwrap());
    assert_eq!(b.status()["governance"]["mode"], "unfounded");
    assert!(b.node.lock().unwrap().members.get(&a.fp()).is_none(), "and made no member of the device it names");
    let again = refusal(&a.call("pool_found", "", &a.token, json!({"act": act})));
    assert!(again.contains("already runs a pool"), "a replay on the device that founded it founds no second pool: {again}");
    assert_eq!(a.pool(), founded["pool"].as_str().unwrap());
}

#[test]
fn an_agents_own_software_key_founds_no_pool_whatever_the_charter_names() {
    let a = device();
    let agent = Counting::of(0x55);
    a.node.lock().unwrap().set_signer(agent.clone());
    let made = a.call("pool_create", "", &a.token, json!({"title": "home", "policy": "secure", "project": ""}));
    assert!(refusal(&made).contains("presence"), "{made}");
    assert_eq!(agent.asked.load(Ordering::SeqCst), 0, "nobody was asked to sign");
    for (said_of_it, k) in [("weak", weak(0x55, "agent")), ("strong", strong(0x55, "agent"))] {
        let c = charter_of(&[k], Policy::Secure, Unit::Person, wall_ms(), first_of(&own(&a)));
        let said = refusal(&a.call("pool_found", "", &a.token, json!({"act": found_act(&c)})));
        assert!(said.contains("strong key") || said.contains("not a phone's key"), "a key it calls {said_of_it}: {said}");
    }
    assert_eq!(a.status()["governance"]["mode"], "unfounded", "a key the node did not enrol as a phone's founds nothing, with this device named first or not");
    assert!(a.node.lock().unwrap().members.get(&a.fp()).is_some_and(|d| d.by == "self"), "this device is the pool of one it was");
}

// -- a charter of the last version ------------------------------------------------------------------------------------------------

/// The founding act of a charter as the last version made it: version 2, no first device.
fn version_two(c: &Charter) -> Value {
    let mut act = serde_json::to_value(found_act(c)).unwrap();
    act["body"].as_object_mut().unwrap().remove("first");
    act["body"]["v"] = json!(2);
    act
}

#[test]
fn a_charter_of_version_two_founds_nothing_and_says_it_is_another_version() {
    let a = device();
    enrol_phone_key(&a, ANN);
    let c = charter_of(&[strong(ANN, "ann")], Policy::Secure, Unit::Person, wall_ms(), first_of(&own(&a)));
    let old = version_two(&c);
    for (how, params) in [("as an act", json!({"act": old})), ("as a charter", json!({"charter": old["body"]}))] {
        let said = refusal(&a.call("pool_found", "", &a.token, params));
        assert!(said.contains("another version of Poolhouse") && said.contains("make a new pool"), "{how}: {said}");
        assert_eq!(a.status()["governance"]["mode"], "unfounded", "{how}");
    }
}

#[test]
fn a_pool_of_charter_version_two_on_disk_is_set_aside_and_a_new_pool_may_be_founded() {
    let dir = tempfile::tempdir().unwrap();
    let c = charter(&[strong(A, "ann")], Policy::Secure, Unit::Person);
    let id = c.pool_id();
    GovPool::create(dir.path(), found_act(&c)).unwrap();
    let held = dir.path().join("pools").join(&id);
    let mut old: Value = serde_json::from_slice(&std::fs::read(held.join("charter.json")).unwrap()).unwrap();
    old["body"].as_object_mut().unwrap().remove("first");
    old["body"]["v"] = json!(2);
    std::fs::write(held.join("charter.json"), serde_json::to_vec(&old).unwrap()).unwrap();
    let pool = Pool::load(dir.path()).unwrap();
    assert!(pool.is_unfounded(), "a pool made under version 2 is not run");
    assert_eq!(pool.retired, format!("pools-old-format/{id}"));
    assert!(dir.path().join("pools-old-format").join(&id).join("charter.json").is_file(), "it is kept, never deleted");
}

// -- the first device in the roster ------------------------------------------------------------------------------------------------

#[test]
fn the_first_device_leaves_by_its_own_signature_is_admitted_again_and_is_removed_for_good() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let first = ident(FIRST);
    let status = |g: &GovPool| g.devices().get(&first.fingerprint()).map(|d| d.status);
    let mut leave = g.new_act("leave", json!({"fingerprint": first.fingerprint()}), wall_ms()).unwrap();
    device_sign(&mut leave, FIRST);
    assert_eq!(take(&mut g, leave), Ingest::Accepted, "the first device signs its own leave");
    assert_eq!(status(&g), Some(Status::Left));
    std::thread::sleep(std::time::Duration::from_millis(3));
    assert_eq!(put_admit(&mut g, &first, &[A]), Ingest::Accepted);
    assert_eq!(status(&g), Some(Status::Active), "an admit after the leave brings it back");
    std::thread::sleep(std::time::Duration::from_millis(3));
    put(&mut g, "remove", json!({"fingerprint": first.fingerprint(), "cut": []}), &[A]);
    assert_eq!(status(&g), Some(Status::Revoked));
    put_admit(&mut g, &first, &[A]);
    assert_eq!(status(&g), Some(Status::Revoked), "a removed device is not admitted again, the first device included");
}

#[test]
fn a_device_the_first_device_does_not_admit_is_no_member_because_of_it() {
    let (_d, mut g) = pool(&[strong(A, "ann")], Policy::Secure);
    let (laptop, first) = (ident(10), ident(FIRST));
    let mut act = admit(&g, &laptop, &[]);
    device_sign(&mut act, FIRST);
    let kept = take(&mut g, act);
    assert!(matches!(kept, Ingest::Refused(_)), "the first device is a member, not a delegate: {kept:?}");
    assert!(!is_member(&g, &laptop));
    assert!(is_member(&g, &first), "and it is still the pool's first member");
}
