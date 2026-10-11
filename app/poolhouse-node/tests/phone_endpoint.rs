//! The endpoint a phone talks to, over a real TLS 1.3 socket on loopback: it shows the one certificate a code pins, speaks JSON, takes
//! a phone's addition and decision, and refuses what is malformed.

mod kit;

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::{Arc, Mutex};

use kit::phone::{ask, FakePhone};
use kit::*;
use poolhouse_node::fsutil::sha256_hex;
use poolhouse_node::phone::enrol::make_offer;
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{ring, verify_tls13_signature};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{ClientConfig, ClientConnection, DigitallySignedStruct, SignatureScheme, StreamOwned};
use serde_json::{json, Value};

#[derive(Debug)]
struct Pin(String);

impl ServerCertVerifier for Pin {
    fn verify_server_cert(&self, end: &CertificateDer<'_>, _: &[CertificateDer<'_>], _: &ServerName<'_>, _: &[u8], _: UnixTime) -> Result<ServerCertVerified, rustls::Error> {
        if sha256_hex(end.as_ref()) == self.0 { Ok(ServerCertVerified::assertion()) } else { Err(rustls::Error::General("not the pinned certificate".into())) }
    }

    fn verify_tls12_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        Err(rustls::Error::General("no tls 1.2".into()))
    }

    fn verify_tls13_signature(&self, message: &[u8], cert: &CertificateDer<'_>, dss: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls13_signature(message, cert, dss, &ring::default_provider().signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        ring::default_provider().signature_verification_algorithms.supported_schemes()
    }
}

fn client(pin: &str, tls12: bool) -> Arc<ClientConfig> {
    let versions: Vec<&'static rustls::SupportedProtocolVersion> = if tls12 { vec![&rustls::version::TLS12] } else { vec![&rustls::version::TLS13] };
    Arc::new(ClientConfig::builder_with_provider(Arc::new(ring::default_provider())).with_protocol_versions(&versions).unwrap()
        .dangerous().with_custom_certificate_verifier(Arc::new(Pin(pin.into()))).with_no_client_auth())
}

/// One request over TLS to ``addr``: the status and the JSON of the reply.
fn call(addr: SocketAddr, config: Arc<ClientConfig>, request: &str, body: &[u8]) -> Result<(u16, Value), String> {
    let mut tcp = TcpStream::connect(addr).map_err(|e| e.to_string())?;
    tcp.set_read_timeout(Some(std::time::Duration::from_secs(10))).unwrap();
    let mut conn = ClientConnection::new(config, ServerName::try_from("poolhouse-phone").unwrap()).map_err(|e| e.to_string())?;
    while conn.is_handshaking() {
        conn.complete_io(&mut tcp).map_err(|e| e.to_string())?;
    }
    let mut tls = StreamOwned::new(conn, tcp);
    write!(tls, "{request}Content-Length: {}\r\n\r\n", body.len()).map_err(|e| e.to_string())?;
    tls.write_all(body).map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    let _ = tls.read_to_end(&mut raw);
    let text = String::from_utf8_lossy(&raw).to_string();
    let status = text.split(' ').nth(1).and_then(|s| s.parse().ok()).ok_or_else(|| format!("no status in {text:?}"))?;
    let json = text.split_once("\r\n\r\n").map_or(Value::Null, |(_, b)| serde_json::from_str(b).unwrap_or(Value::Null));
    Ok((status, json))
}

fn post(op: &str, token: &str) -> String {
    let auth = if token.is_empty() { String::new() } else { format!("Authorization: Bearer {token}\r\n") };
    format!("POST /phone/v1/{op} HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n{auth}")
}

#[test]
fn a_phone_is_added_and_decides_over_tls_against_the_one_pinned_certificate() {
    let dir = short_dir();
    let node = Arc::new(Mutex::new(node(dir.path())));
    let server = poolhouse_node::phone::http::start(node.clone(), "127.0.0.1:0".parse().unwrap()).unwrap();
    let addr: SocketAddr = format!("127.0.0.1:{}", server.port).parse().unwrap();
    let (pin, device) = {
        let n = node.lock().unwrap();
        (n.phone_cert.fingerprint(), n.cert.fingerprint())
    };

    let (status, hello) = call(addr, client(&pin, false), "GET /phone/v1/hello HTTP/1.1\r\nHost: x\r\n", b"").unwrap();
    assert_eq!((status, hello["result"]["device"].as_str()), (200, Some(device.as_str())), "{hello}");
    assert!(call(addr, client(&sha256_hex(b"another certificate"), false), "GET /phone/v1/hello HTTP/1.1\r\nHost: x\r\n", b"").is_err(), "a phone pins one certificate and nothing else");
    assert!(call(addr, client(&pin, true), "GET /phone/v1/hello HTTP/1.1\r\nHost: x\r\n", b"").is_err(), "TLS 1.2 is not spoken");

    let secret = make_offer(dir.path(), kit::phone::now()).unwrap();
    let mut phone = FakePhone::new("Pixel");
    let body = phone.add_body(&device, &secret).to_string();
    let (status, added) = call(addr, client(&pin, false), &post("add", ""), body.as_bytes()).unwrap();
    assert_eq!((status, added["result"]["state"].as_str()), (200, Some("active")), "{added}");
    phone.token = added["result"]["token"].as_str().unwrap().into();

    let (status, refused) = call(addr, client(&pin, false), &post("list", "not-a-token"), b"{}").unwrap();
    assert_eq!((status, refused["error"]["code"].as_str()), (401, Some("denied")), "{refused}");

    let (_, token) = session(&mut node.lock().unwrap(), "demo", "agent-1");
    let made = ok(req(&mut node.lock().unwrap(), "approval_request", "demo", &token, json!({"asks": [ask("add_device", "*")]})));
    let (_, listed) = call(addr, client(&pin, false), &post("list", &phone.token), b"{}").unwrap();
    let seen = listed["result"]["approvals"][0].clone();
    assert_eq!(seen["id"], made["id"]);
    let mut decision = phone.decision(&seen, "approve");
    decision["pick"] = json!(node.lock().unwrap().approvals.get(made["id"].as_str().unwrap()).unwrap().pick_code);
    let (_, done) = call(addr, client(&pin, false), &post("decide", ""), decision.to_string().as_bytes()).unwrap();
    assert_eq!(done["result"]["state"], "approved", "{done}");
}

#[test]
fn a_host_that_opens_connections_and_says_nothing_cannot_hold_every_place() {
    let dir = short_dir();
    let node = Arc::new(Mutex::new(node(dir.path())));
    let server = poolhouse_node::phone::http::start(node.clone(), "127.0.0.1:0".parse().unwrap()).unwrap();
    let addr: SocketAddr = format!("127.0.0.1:{}", server.port).parse().unwrap();
    let pin = node.lock().unwrap().phone_cert.fingerprint();
    let hello = "GET /phone/v1/hello HTTP/1.1\r\nHost: x\r\n";
    let silent: Vec<TcpStream> = (0..6).map(|_| TcpStream::connect(addr).unwrap()).collect();
    std::thread::sleep(std::time::Duration::from_millis(300));
    assert!(call(addr, client(&pin, false), hello, b"").is_err(), "a seventh connection from one address is not served while six sit silent");
    drop(silent);
    let served = (0..50).any(|_| {
        std::thread::sleep(std::time::Duration::from_millis(100));
        call(addr, client(&pin, false), hello, b"").is_ok()
    });
    assert!(served, "the places come back when the silent connections close");
}

#[test]
fn what_is_malformed_or_too_large_is_refused_without_reaching_the_node() {
    let dir = short_dir();
    let node = Arc::new(Mutex::new(node(dir.path())));
    let server = poolhouse_node::phone::http::start(node.clone(), "127.0.0.1:0".parse().unwrap()).unwrap();
    let addr: SocketAddr = format!("127.0.0.1:{}", server.port).parse().unwrap();
    let pin = node.lock().unwrap().phone_cert.fingerprint();
    let (status, _) = call(addr, client(&pin, false), "GET / HTTP/1.1\r\nHost: x\r\n", b"").unwrap();
    assert_eq!(status, 404);
    let (status, _) = call(addr, client(&pin, false), &post("add", ""), b"[1]").unwrap();
    assert_eq!(status, 200, "a body that is not an object is an error reply, not a crash");
    // The node answers 400 and closes before the rest of the body is sent, so a client that is still writing may see the close instead.
    let refused = call(addr, client(&pin, false), &post("add", ""), &vec![b'a'; 70 * 1024]);
    assert!(refused.as_ref().map_or(true, |(status, reply)| *status == 400 || reply.is_null()), "a body over 64 KiB is not read: {refused:?}");
    let (status, _) = call(addr, client(&pin, false), "POST /phone/v1/list HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n", b"0\r\n\r\n").unwrap();
    assert_eq!(status, 400, "chunked bodies are not taken");
    let (status, reply) = call(addr, client(&pin, false), &post("nonsense", "x"), b"{}").unwrap();
    assert_eq!((status, reply["error"]["code"].as_str()), (401, Some("denied")), "an unknown token learns nothing about the operations");
}
