mod kit;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Arc;

use kit::netkit::*;
use poolhouse_node::cert::{self, Identity};
use poolhouse_node::error::Error;
use kit::gov::person;
use kit::govnode::{found, ANN};
use poolhouse_node::peer::PeerClient;
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{ClientConfig, ClientConnection, DigitallySignedStruct, SignatureScheme};
use serde_json::json;

fn identity(seed: u8) -> Identity {
    let dir = tempfile::tempdir().unwrap();
    cert::load_or_create(dir.path(), &kit::key(seed)).unwrap()
}

// -- joining -----------------------------------------------------------------

#[test]
fn two_nodes_join_with_a_code_and_converge() {
    let (a, b) = (device(), device());
    let (_, at) = a.session("demo", "x");
    let (_, bt) = b.session("demo", "y");
    a.say("demo", &at, "from a");
    b.say("demo", &bt, "from b");
    a.join_with_code(&b);
    assert_eq!(a.pool(), b.pool());
    assert!(a.is_member(&b.fp()) && b.is_member(&a.fp()));
    assert_eq!(a.member(&b.fp()).unwrap()["by"], "join-code");
    b.ok("sync_now", "", &b.token, json!({}));
    for (dev, t) in [(&a, &at), (&b, &bt)] {
        let mut texts = dev.texts("demo", t);
        texts.sort();
        assert_eq!(texts, vec!["from a", "from b"]);
    }
    a.say("demo", &at, "later from a");
    a.ok("sync_now", "", &a.token, json!({}));
    assert!(b.texts("demo", &bt).contains(&"later from a".to_string()), "the member records the address it was reached at");
}

#[test]
fn only_boards_both_devices_hold_are_synced() {
    let (a, b) = (device(), device());
    let (_, at) = a.session("secret", "x");
    a.say("secret", &at, "private to a");
    a.join_with_code(&b);
    b.ok("sync_now", "", &b.token, json!({}));
    assert!(!b.node.lock().unwrap().boards.contains_key("secret"));
    assert!(b.node.lock().unwrap().boards.contains_key("demo"));
}

#[test]
fn a_wrong_passphrase_fails_and_three_wrong_tries_close_the_window() {
    let (a, b) = (device(), device());
    a.found_if_unfounded();
    let code = a.ok("add_device", "", &a.token, json!({"passphrase": "correct horse"}))["code"].as_str().unwrap().to_string();
    assert_eq!(code, "correct horse");
    let start = |pass: &str| b.call("join_pool", "", &b.token, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": pass}));
    for _ in 0..3 {
        let r = start("wrong guess");
        assert_eq!(r["ok"], false, "{r}");
        assert_eq!(r["error"]["code"], "denied");
    }
    assert!(!a.is_member(&b.fp()) && !b.is_member(&a.fp()));
    assert_ne!(a.pool(), b.pool());
    let r = start("correct horse");
    assert_eq!(r["ok"], false, "the window is closed after three wrong tries: {r}");
    a.ok("add_device", "", &a.token, json!({"passphrase": "correct horse"}));
    assert_eq!(start("correct horse")["ok"], true, "a fresh window takes the right code");
    assert!(a.is_member(&b.fp()));
}

#[test]
fn joining_needs_an_open_window_and_a_device_alone_in_its_pool() {
    let (a, b, c) = (device(), device(), device());
    let r = b.call("join_pool", "", &b.token, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": "anything at all"}));
    assert_eq!(r["error"]["code"], "denied", "no window is open: {r}");
    a.join_with_code(&b);
    let code = a.ok("add_device", "", &a.token, json!({}))["code"].as_str().unwrap().to_string();
    let r = b.call("join_pool", "", &b.token, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": code}));
    assert_eq!(r["error"]["code"], "denied", "b already has a member: {r}");
    let _ = c;
}

#[test]
fn join_policy_open_enrols_a_device_that_names_the_fingerprint() {
    let (secure, open, b) = (device(), device(), device());
    found(&secure, person(ANN, "ann"), "secure");
    found(&open, person(ANN, "ann"), "open");
    let (_, ot) = open.session("demo", "x");
    open.say("demo", &ot, "from the open pool");
    let ask = |host: &Dev| b.call("join_pool", "", &b.token, json!({"host": "127.0.0.1", "port": host.net.port, "fingerprint": host.fp()}));
    assert_eq!(ask(&secure)["error"]["code"], "denied", "policy secure: a device is not taken without a code");
    assert!(!secure.is_member(&b.fp()));
    assert_eq!(ask(&open)["ok"], true);
    assert_eq!(open.pool(), b.pool());
    assert_eq!(open.member(&b.fp()).unwrap()["by"], "open");
    assert!(open.is_member(&b.fp()) && b.is_member(&open.fp()));
    kit::govnode::grant(&b, "demo", &b.token, &["sync_now"]);
    b.ok("sync_now", "", &b.token, json!({}));
    assert!(b.texts("demo", &b.token).contains(&"from the open pool".to_string()));
    assert_eq!(b.status()["policy"], "open", "the policy is one attribute of the pool, and it never changes in place");
}

// -- refusals ----------------------------------------------------------------

#[test]
fn a_non_member_is_refused_everything_but_hello() {
    let (a, stranger) = (device(), identity(9));
    let mut c = PeerClient::connect(&stranger, a.addr(), Some(&a.fp())).unwrap();
    let hello = c.call(&json!({"op": "hello"})).unwrap();
    assert_eq!((hello["member"].clone(), hello["boards"].clone()), (json!(false), json!([])), "a stranger learns no boards");
    for req in [json!({"op": "boards"}), json!({"op": "members", "pool": a.pool(), "rows": []}), json!({"op": "pull", "board": "demo", "origin": "0".repeat(32), "vector": {}})] {
        assert!(matches!(c.call(&req), Err(Error::Denied(_))), "{req}");
    }
    assert!(matches!(c.call(&json!({"op": "join_open"})), Err(Error::Denied(_))), "policy secure takes nobody");
    assert!(!a.is_member(&stranger.fingerprint()));
}

#[test]
fn a_caller_with_no_certificate_cannot_complete_a_request() {
    let a = device();
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider.clone()).with_protocol_versions(&[&rustls::version::TLS13]).unwrap()
        .dangerous().with_custom_certificate_verifier(Arc::new(AcceptAll(provider))).with_no_client_auth();
    let mut tcp = TcpStream::connect(a.addr()).unwrap();
    let mut conn = ClientConnection::new(Arc::new(config), ServerName::try_from("poolhouse-node").unwrap()).unwrap();
    let mut tls = rustls::Stream::new(&mut conn, &mut tcp);
    let frame = br#"{"op":"hello"}"#;
    let mut out = (frame.len() as u32).to_be_bytes().to_vec();
    out.extend_from_slice(frame);
    let sent = tls.write_all(&out).and_then(|_| tls.flush());
    let mut reply = [0u8; 16];
    let got = sent.and_then(|_| tls.read(&mut reply));
    assert!(got.is_err() || got.unwrap() == 0, "the server answered a caller with no certificate");
}

#[derive(Debug)]
struct AcceptAll(Arc<rustls::crypto::CryptoProvider>);

impl ServerCertVerifier for AcceptAll {
    fn verify_server_cert(&self, _: &CertificateDer<'_>, _: &[CertificateDer<'_>], _: &ServerName<'_>, _: &[u8], _: UnixTime) -> Result<ServerCertVerified, rustls::Error> {
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(&self, m: &[u8], c: &CertificateDer<'_>, d: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(m, c, d, &self.0.signature_verification_algorithms)
    }

    fn verify_tls13_signature(&self, m: &[u8], c: &CertificateDer<'_>, d: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(m, c, d, &self.0.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.0.signature_verification_algorithms.supported_schemes()
    }
}

#[test]
fn a_tls_12_client_is_refused_even_with_nothing_else_wrong() {
    let a = device();
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let config = ClientConfig::builder_with_provider(provider.clone()).with_protocol_versions(&[&rustls::version::TLS12]).unwrap()
        .dangerous().with_custom_certificate_verifier(Arc::new(AcceptAll(provider))).with_no_client_auth();
    let mut tcp = TcpStream::connect(a.addr()).unwrap();
    let mut conn = ClientConnection::new(Arc::new(config), ServerName::try_from("poolhouse-node").unwrap()).unwrap();
    let mut failed = false;
    while conn.is_handshaking() {
        if conn.complete_io(&mut tcp).is_err() {
            failed = true;
            break;
        }
    }
    assert!(failed, "the handshake completed on TLS 1.2");
}

#[test]
fn a_certificate_other_than_the_pinned_one_is_refused() {
    let (a, b) = (device(), device());
    let me = identity(11);
    assert!(PeerClient::connect(&me, a.addr(), Some(&a.fp())).is_ok());
    assert!(PeerClient::connect(&me, a.addr(), Some(&b.fp())).is_err(), "another device's pin");
    let mut tampered = a.fp().into_bytes();
    tampered[10] = if tampered[10] == b'0' { b'1' } else { b'0' };
    assert!(PeerClient::connect(&me, a.addr(), Some(&String::from_utf8(tampered).unwrap())).is_err(), "a pin with a digit changed");
}

#[test]
fn a_removed_device_is_refused_at_its_next_request_on_a_live_session() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let mut live = PeerClient::connect(&b.net.me, a.addr(), Some(&a.fp())).unwrap();
    assert!(live.call(&json!({"op": "boards"})).is_ok());
    a.ok("member_remove", "", &a.token, json!({"fingerprint": b.fp()}));
    let refused = live.call(&json!({"op": "boards"}));
    assert!(matches!(refused, Err(Error::Denied(_))), "{refused:?}");
    assert!(PeerClient::connect(&b.net.me, a.addr(), Some(&a.fp())).and_then(|mut c| c.call(&json!({"op": "boards"}))).is_err(), "and at a new connection");
    assert_eq!(a.member(&b.fp()).unwrap()["status"], "revoked");
    let again = a.call("add_device", "", &a.token, json!({"passphrase": "another code"}));
    assert_eq!(again["ok"], true);
    let c = b.call("join_pool", "", &b.token, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": "another code"}));
    assert_eq!(c["ok"], false, "a revoked certificate does not come back: {c}");
}

#[test]
fn a_removal_is_a_signed_act_that_reaches_a_member_with_its_next_exchange() {
    let (a, b, c) = (device(), device(), device());
    a.join_with_code(&b);
    a.join_with_code(&c);
    b.ok("sync_now", "", &b.token, json!({}));
    a.ok("member_remove", "", &a.token, json!({"fingerprint": c.fp()}));
    b.ok("sync_now", "", &b.token, json!({}));
    assert_eq!(a.member(&c.fp()).unwrap()["status"], "revoked");
    assert_eq!(b.member(&c.fp()).unwrap()["status"], "revoked", "the removal is an act of the pool's admin: b learns it by its next exchange");
    let refused = PeerClient::connect(&c.net.me, a.addr(), Some(&a.fp())).and_then(|mut l| l.call(&json!({"op": "boards"})));
    assert!(refused.is_err(), "the device a removed is refused at a");
}

#[test]
fn pool_status_says_who_is_connected_and_when() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    let before = a.member(&b.fp()).unwrap();
    assert_eq!(before["connected"], false);
    assert_eq!(before["addr"], format!("127.0.0.1:{}", b.net.port));
    let mut live = PeerClient::connect(&b.net.me, a.addr(), Some(&a.fp())).unwrap();
    live.call(&json!({"op": "boards"})).unwrap();
    assert_eq!(a.member(&b.fp()).unwrap()["connected"], true);
    drop(live);
    eventually("the session to be seen closing", 5, || a.member(&b.fp()).unwrap()["connected"] == false);
    assert_eq!(a.status()["listen"], format!("127.0.0.1:{}", a.net.port));
}
