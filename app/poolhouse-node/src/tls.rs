//! TLS 1.3 and nothing else, with a pinned self-signed certificate per device on both sides.
//!
//! No certificate authority and no first-use trust. A client trusts the one certificate it was
//! told to (`expect`), or, to join, any certificate that the exchange afterwards binds to a
//! code. A server asks every client for a certificate and refuses the handshake of one the pool
//! has revoked; an unknown one completes the handshake and may only join with a code or join under `open`, which the
//! request check enforces on every request.

use std::io::{ErrorKind, Read, Write};
use std::net::{Shutdown, TcpStream};
use std::sync::Arc;
use std::time::Duration;

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{ring, verify_tls13_signature, CryptoProvider};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::server::danger::{ClientCertVerified, ClientCertVerifier};
use rustls::{ClientConfig, ClientConnection, DigitallySignedStruct, DistinguishedName, ServerConfig, ServerConnection, SignatureScheme, StreamOwned};

use crate::cert::{board_fingerprint, cert_fingerprint, Identity};
use crate::error::{Error, Result};
use crate::gate::Timed;
use crate::membership::Standing;

/// How long a client's handshake may take from first byte to last: what the phone endpoint gives a handshake and its request.
pub const HANDSHAKE_FOR: Duration = Duration::from_secs(10);
/// How long a refused connection is drained for the other end to read the alert and close, after a handshake that may have taken
/// up to `HANDSHAKE_FOR`: the 5 s a dial is given to connect (`peer::PeerClient`).
const ALERT_LINGER: Duration = Duration::from_secs(5);
/// The most bytes read from a refused connection: far over the `hello` a device sends first.
const MOST_DRAINED: usize = 64 * 1024;

/// What the pool says of a certificate fingerprint, asked at every handshake.
pub type StandingFn = Arc<dyn Fn(&str) -> Standing + Send + Sync>;

fn provider() -> Arc<CryptoProvider> {
    Arc::new(ring::default_provider())
}

fn refuse(why: &str) -> rustls::Error {
    rustls::Error::General(why.into())
}

fn tls_error(e: rustls::Error) -> Error {
    Error::Invalid(format!("tls: {e}"))
}

#[derive(Debug)]
struct Members {
    standing: StandingFnDebug,
    algs: rustls::crypto::WebPkiSupportedAlgorithms,
}

struct StandingFnDebug(StandingFn);

impl std::fmt::Debug for StandingFnDebug {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("standing")
    }
}

impl ClientCertVerifier for Members {
    fn root_hint_subjects(&self) -> &[DistinguishedName] {
        &[]
    }

    fn verify_client_cert(&self, end_entity: &CertificateDer<'_>, intermediates: &[CertificateDer<'_>], _now: UnixTime) -> std::result::Result<ClientCertVerified, rustls::Error> {
        if !intermediates.is_empty() || board_fingerprint(end_entity).is_err() {
            return Err(refuse("a device presents one Ed25519 certificate"));
        }
        match (self.standing.0)(&cert_fingerprint(end_entity)) {
            Standing::Revoked => Err(refuse("this device was put out of the pool")),
            _ => Ok(ClientCertVerified::assertion()),
        }
    }

    fn verify_tls12_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct) -> std::result::Result<HandshakeSignatureValid, rustls::Error> {
        Err(refuse("TLS 1.2 is not spoken"))
    }

    fn verify_tls13_signature(&self, message: &[u8], cert: &CertificateDer<'_>, dss: &DigitallySignedStruct) -> std::result::Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls13_signature(message, cert, dss, &self.algs)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.algs.supported_schemes()
    }
}

#[derive(Debug)]
struct Pin {
    expect: Option<String>,
    algs: rustls::crypto::WebPkiSupportedAlgorithms,
}

impl ServerCertVerifier for Pin {
    fn verify_server_cert(&self, end_entity: &CertificateDer<'_>, intermediates: &[CertificateDer<'_>], _name: &ServerName<'_>, _ocsp: &[u8], _now: UnixTime) -> std::result::Result<ServerCertVerified, rustls::Error> {
        if !intermediates.is_empty() || board_fingerprint(end_entity).is_err() {
            return Err(refuse("a device presents one Ed25519 certificate"));
        }
        match &self.expect {
            Some(pin) if *pin != cert_fingerprint(end_entity) => Err(refuse("the certificate is not the one pinned")),
            _ => Ok(ServerCertVerified::assertion()),
        }
    }

    fn verify_tls12_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct) -> std::result::Result<HandshakeSignatureValid, rustls::Error> {
        Err(refuse("TLS 1.2 is not spoken"))
    }

    fn verify_tls13_signature(&self, message: &[u8], cert: &CertificateDer<'_>, dss: &DigitallySignedStruct) -> std::result::Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls13_signature(message, cert, dss, &self.algs)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.algs.supported_schemes()
    }
}

/// The server side: TLS 1.3 only, the client's certificate required and checked against ``standing``.
pub fn server_config(me: &Identity, standing: StandingFn) -> Result<Arc<ServerConfig>> {
    let p = provider();
    let verifier = Members { standing: StandingFnDebug(standing), algs: p.signature_verification_algorithms };
    let config = ServerConfig::builder_with_provider(p)
        .with_protocol_versions(&[&rustls::version::TLS13]).map_err(tls_error)?
        .with_client_cert_verifier(Arc::new(verifier))
        .with_single_cert(me.chain(), me.private_key()).map_err(tls_error)?;
    Ok(Arc::new(config))
}

/// The client side: TLS 1.3 only, showing ``me``, accepting only the certificate with
/// fingerprint ``expect`` (any, when joining with a code: the exchange then authenticates it).
pub fn client_config(me: &Identity, expect: Option<&str>) -> Result<Arc<ClientConfig>> {
    let p = provider();
    let pin = Pin { expect: expect.map(str::to_string), algs: p.signature_verification_algorithms };
    let config = ClientConfig::builder_with_provider(p)
        .with_protocol_versions(&[&rustls::version::TLS13]).map_err(tls_error)?
        .dangerous().with_custom_certificate_verifier(Arc::new(pin))
        .with_client_auth_cert(me.chain(), me.private_key()).map_err(tls_error)?;
    Ok(Arc::new(config))
}

/// A finished handshake's stream and the certificate the other end showed.
pub struct Secured<C> {
    pub stream: StreamOwned<C, Timed>,
    pub peer: Vec<u8>,
}

impl<C> Read for Secured<C> where StreamOwned<C, Timed>: Read {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        self.stream.read(buf)
    }
}

impl<C> Write for Secured<C> where StreamOwned<C, Timed>: Write {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.stream.write(buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.stream.flush()
    }
}

fn io_tls(e: std::io::Error) -> Error {
    Error::Denied(format!("the handshake failed: {e}"))
}

/// A handshake that did not finish: why, and the connection when an alert went down it that the other end has yet to read
/// (`close_after_alert`).
pub struct Unfinished {
    pub why: Error,
    pub alerted: Option<Arc<TcpStream>>,
}

impl From<Error> for Unfinished {
    fn from(why: Error) -> Unfinished {
        Unfinished { why, alerted: None }
    }
}

/// Close a connection whose handshake was refused so that the alert is read: say no more is coming, then take what the other end
/// sends until it closes, for `ALERT_LINGER` and `MOST_DRAINED` bytes at most. A socket closed with bytes unread, or sent bytes
/// after it closed, is reset, and a reset can discard the alert before the other end reads it (Windows does).
pub fn close_after_alert(tcp: Arc<TcpStream>) {
    if tcp.shutdown(Shutdown::Write).is_err() {
        return;
    }
    let mut tcp = Timed::within(tcp, ALERT_LINGER);
    let (mut rest, mut left) = ([0u8; 4096], MOST_DRAINED);
    while left > 0 {
        match tcp.read(&mut rest) {
            Ok(n) if n > 0 => left = left.saturating_sub(n),
            _ => break,
        }
    }
}

/// Accept a client: run the handshake within `HANDSHAKE_FOR` and return the stream, still under that deadline, and the client's
/// certificate.
pub fn accept(config: Arc<ServerConfig>, tcp: Arc<TcpStream>) -> std::result::Result<Secured<ServerConnection>, Unfinished> {
    let mut conn = ServerConnection::new(config).map_err(tls_error)?;
    let mut tcp = Timed::within(tcp, HANDSHAKE_FOR);
    while conn.is_handshaking() {
        if let Err(e) = conn.complete_io(&mut tcp) {
            // A refusal of the protocol's own sends an alert; a peer that was too slow or went away has none to read.
            let alerted = (e.kind() == ErrorKind::InvalidData).then(|| tcp.into_inner());
            return Err(Unfinished { why: io_tls(e), alerted });
        }
    }
    let peer = conn.peer_certificates().and_then(|c| c.first()).map(|c| c.to_vec()).ok_or_else(|| Error::Denied("no certificate was shown".into()))?;
    Ok(Secured { stream: StreamOwned::new(conn, tcp), peer })
}

#[cfg(test)]
mod linger_tests {
    use super::*;
    use std::net::TcpListener;
    use std::time::Instant;

    fn connected() -> (TcpStream, TcpStream) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let client = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        (client, listener.accept().unwrap().0)
    }

    #[test]
    fn a_byte_that_arrives_just_before_the_deadline_does_not_move_it() {
        let (mut client, server) = connected();
        let sender = std::thread::spawn(move || {
            std::thread::sleep(ALERT_LINGER - Duration::from_millis(500));
            client.write_all(b"x").unwrap();
            client
        });
        let began = Instant::now();
        close_after_alert(Arc::new(server));
        let took = began.elapsed();
        drop(sender.join().unwrap());
        assert!(took < ALERT_LINGER + Duration::from_secs(2), "{took:?}");
    }

    #[test]
    fn a_peer_that_keeps_sending_is_drained_of_so_much_and_closed() {
        let (mut client, server) = connected();
        let sender = std::thread::spawn(move || {
            let (block, until) = ([0u8; 4096], Instant::now() + ALERT_LINGER + Duration::from_secs(1));
            while Instant::now() < until && client.write_all(&block).is_ok() {}
        });
        let began = Instant::now();
        close_after_alert(Arc::new(server));
        assert!(began.elapsed() < Duration::from_secs(2), "{:?}", began.elapsed());
        sender.join().unwrap();
    }
}

/// Connect: run the handshake and return the stream and the server's certificate.
pub fn connect(config: Arc<ClientConfig>, mut tcp: TcpStream) -> Result<Secured<ClientConnection>> {
    let name = ServerName::try_from("poolhouse-node").map_err(|e| Error::Invalid(e.to_string()))?;
    let mut conn = ClientConnection::new(config, name).map_err(tls_error)?;
    while conn.is_handshaking() {
        conn.complete_io(&mut tcp).map_err(io_tls)?;
    }
    let peer = conn.peer_certificates().and_then(|c| c.first()).map(|c| c.to_vec()).ok_or_else(|| Error::Denied("no certificate was shown".into()))?;
    Ok(Secured { stream: StreamOwned::new(conn, Timed::open(tcp)), peer })
}
