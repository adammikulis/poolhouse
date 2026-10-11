//! The push service of a node: it posts only the sealed body, over TLS to a certificate it trusts, and never to an address inside the
//! network a phone's token could name.

use std::io::{Read, Write};
use std::net::{IpAddr, TcpListener};
use std::sync::mpsc;
use std::sync::Arc;

use poolhouse_node::phone::push::{post_with, public};
use rcgen::{BasicConstraints, CertificateParams, IsCa, Issuer, KeyPair};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::{RootCertStore, ServerConfig, ServerConnection, StreamOwned};

/// A TLS server on loopback with a certificate for `localhost` signed by a CA of its own; it answers one request with ``status``
/// and sends what it received down the channel. The CA's certificate is what a client trusts.
fn service(status: &str) -> (u16, Vec<u8>, mpsc::Receiver<Vec<u8>>) {
    let ca_key = KeyPair::generate().unwrap();
    let mut ca_params = CertificateParams::new(Vec::<String>::new()).unwrap();
    ca_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    let ca = ca_params.self_signed(&ca_key).unwrap();
    let issuer = Issuer::new(ca_params, &ca_key);
    let leaf_key = KeyPair::generate().unwrap();
    let leaf = CertificateParams::new(vec!["localhost".to_string()]).unwrap().signed_by(&leaf_key, &issuer).unwrap();
    let config = Arc::new(ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_protocol_versions(&[&rustls::version::TLS13]).unwrap().with_no_client_auth()
        .with_single_cert(vec![CertificateDer::from(leaf.der().to_vec())], PrivateKeyDer::from(PrivatePkcs8KeyDer::from(leaf_key.serialize_der()))).unwrap());
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = mpsc::channel();
    let reply = format!("HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    std::thread::spawn(move || {
        let Ok((mut tcp, _)) = listener.accept() else { return };
        let mut conn = ServerConnection::new(config).unwrap();
        while conn.is_handshaking() {
            if conn.complete_io(&mut tcp).is_err() {
                return;
            }
        }
        let mut tls = StreamOwned::new(conn, tcp);
        let mut got = Vec::new();
        let mut chunk = [0u8; 4096];
        while let Ok(n) = tls.read(&mut chunk) {
            got.extend_from_slice(&chunk[..n]);
            if n == 0 || got.windows(4).position(|w| w == b"\r\n\r\n").is_some_and(|end| got.len() >= end + 4 + body_length(&got)) {
                break;
            }
        }
        let _ = tls.write_all(reply.as_bytes());
        let _ = tx.send(got);
    });
    (port, ca.der().to_vec(), rx)
}

fn body_length(request: &[u8]) -> usize {
    String::from_utf8_lossy(request).lines().find_map(|l| l.strip_prefix("Content-Length: ")).and_then(|n| n.trim().parse().ok()).unwrap_or(0)
}

fn trusting(ca: &[u8]) -> Arc<RootCertStore> {
    let mut roots = RootCertStore::empty();
    roots.add(CertificateDer::from(ca.to_vec())).unwrap();
    Arc::new(roots)
}

#[test]
fn a_sealed_body_is_posted_over_tls_to_a_host_a_person_listed_and_nothing_else_is_sent() {
    let (port, ca, received) = service("201 Created");
    let body = vec![7u8, 8, 9, 10, 11];
    post_with(&format!("https://localhost:{port}/UPabc"), &body, trusting(&ca), &["localhost".to_string()]).unwrap();
    let got = received.recv_timeout(std::time::Duration::from_secs(5)).unwrap();
    let text = String::from_utf8_lossy(&got).to_string();
    assert!(text.starts_with("POST /UPabc HTTP/1.1\r\nHost: localhost\r\n"), "{text}");
    assert!(text.contains("Content-Encoding: aes128gcm") && text.contains("Urgency: high") && text.contains("TTL: 86400"));
    assert!(!text.to_ascii_lowercase().contains("authorization") && !text.contains("cookie"), "no credential goes with a push");
    assert_eq!(&got[got.len() - body.len()..], &body[..], "the body is the sealed bytes and nothing is added");
}

#[test]
fn a_push_never_goes_to_an_address_inside_the_network_unless_a_person_listed_the_host() {
    let (port, ca, _rx) = service("201 Created");
    let refused = post_with(&format!("https://localhost:{port}/x"), b"x", trusting(&ca), &[]).unwrap_err().to_string();
    assert!(refused.contains("a push goes only to a public address"), "{refused}");
    for inside in ["127.0.0.1", "10.1.2.3", "192.168.0.7", "172.16.0.9", "169.254.1.1", "100.64.0.1", "0.0.0.0", "224.0.0.1"] {
        assert!(!public(inside.parse::<IpAddr>().unwrap()), "{inside}");
    }
    for inside in ["::1", "fd00::1", "fe80::1", "::ffff:10.0.0.1"] {
        assert!(!public(inside.parse::<IpAddr>().unwrap()), "{inside}");
    }
    for outside in ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"] {
        assert!(public(outside.parse::<IpAddr>().unwrap()), "{outside}");
    }
    for bad in ["http://localhost/x", "https://user@localhost/x", "ftp://x/y", "https://", "https://localhost:0/x"] {
        assert!(post_with(bad, b"x", Arc::new(RootCertStore::empty()), &["localhost".to_string()]).is_err(), "{bad}");
    }
}

#[test]
fn a_service_with_a_certificate_nobody_trusts_gets_nothing_and_a_refusal_is_an_error() {
    let (port, _, received) = service("201 Created");
    let other = KeyPair::generate().unwrap();
    let mut other_params = CertificateParams::new(Vec::<String>::new()).unwrap();
    other_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    let other_ca = other_params.self_signed(&other).unwrap();
    assert!(post_with(&format!("https://localhost:{port}/x"), b"secret", trusting(other_ca.der()), &["localhost".to_string()]).is_err());
    assert!(received.recv_timeout(std::time::Duration::from_millis(500)).map_or(true, |got| !String::from_utf8_lossy(&got).contains("secret")), "nothing was sent before the certificate was trusted");
    let (port, ca, _rx) = service("410 Gone");
    let gone = post_with(&format!("https://localhost:{port}/x"), b"x", trusting(&ca), &["localhost".to_string()]).unwrap_err().to_string();
    assert!(gone.contains("did not take the message"), "{gone}");
}
