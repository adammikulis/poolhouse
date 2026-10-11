//! A message for a phone that nobody else can read: Web Push payload encryption (RFC 8291, with the record format of RFC 8188), and
//! the delivery of the sealed body to the address the phone registered.
//!
//! The push service sees the address, the length and the time. The phone holds the only key that opens the body.

use std::io::{Read, Write};
use std::net::{IpAddr, TcpStream, ToSocketAddrs};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use ring::aead::{Aad, LessSafeKey, Nonce, UnboundKey, AES_128_GCM};
use ring::agreement::{agree_ephemeral, EphemeralPrivateKey, UnparsedPublicKey, ECDH_P256};
use ring::hkdf::{KeyType, Prk, Salt, HKDF_SHA256};
use ring::rand::{SecureRandom, SystemRandom};
use rustls::pki_types::ServerName;
use rustls::{ClientConfig, ClientConnection, RootCertStore, StreamOwned};

use crate::error::{Error, Result};

/// The most plaintext one push carries: a push service takes at least 4096 bytes, and some distributors add their own framing.
pub const MAX_PLAINTEXT: usize = 3000;
const RECORD: u32 = 4096;

struct Len(usize);

impl KeyType for Len {
    fn len(&self) -> usize {
        self.0
    }
}

fn expand(prk: &Prk, info: &[u8], out: &mut [u8]) -> Result<()> {
    prk.expand(&[info], Len(out.len())).and_then(|okm| okm.fill(out)).map_err(|_| Error::Invalid("the push key could not be derived".into()))
}

/// The content-encryption key and the nonce for ``ecdh``, the two public keys, the phone's secret ``auth`` and the record's ``salt``.
fn derive(ecdh: &[u8], as_public: &[u8], ua_public: &[u8], auth: &[u8], salt: &[u8]) -> Result<([u8; 16], [u8; 12])> {
    let early = Salt::new(HKDF_SHA256, auth).extract(ecdh);
    let mut info = b"WebPush: info\0".to_vec();
    info.extend_from_slice(ua_public);
    info.extend_from_slice(as_public);
    let mut ikm = [0u8; 32];
    expand(&early, &info, &mut ikm)?;
    let prk = Salt::new(HKDF_SHA256, salt).extract(&ikm);
    let (mut key, mut nonce) = ([0u8; 16], [0u8; 12]);
    expand(&prk, b"Content-Encoding: aes128gcm\0", &mut key)?;
    expand(&prk, b"Content-Encoding: nonce\0", &mut nonce)?;
    Ok((key, nonce))
}

/// The body of a Web Push request carrying ``plaintext``, from the sender's public key ``as_public`` and the agreed secret ``ecdh`` to
/// the phone with public key ``ua_public`` and secret ``auth``, in one record with the salt ``salt``.
pub fn encrypt_with(ecdh: &[u8], as_public: &[u8], ua_public: &[u8], auth: &[u8], salt: &[u8; 16], plaintext: &[u8]) -> Result<Vec<u8>> {
    if plaintext.len() > MAX_PLAINTEXT || as_public.len() != 65 {
        return Err(Error::Invalid("a push carries at most 3000 bytes".into()));
    }
    let (key, nonce) = derive(ecdh, as_public, ua_public, auth, salt)?;
    let mut record = plaintext.to_vec();
    record.push(2);
    let sealing = LessSafeKey::new(UnboundKey::new(&AES_128_GCM, &key).map_err(|_| Error::Invalid("the push key is not usable".into()))?);
    sealing.seal_in_place_append_tag(Nonce::assume_unique_for_key(nonce), Aad::empty(), &mut record).map_err(|_| Error::Invalid("the push could not be sealed".into()))?;
    let mut body = salt.to_vec();
    body.extend_from_slice(&RECORD.to_be_bytes());
    body.push(65);
    body.extend_from_slice(as_public);
    body.extend_from_slice(&record);
    Ok(body)
}

/// Seal ``plaintext`` to the phone with public key ``p256dh`` (an uncompressed point) and secret ``auth`` (16 bytes), under a key made for
/// this one message.
pub fn seal(plaintext: &[u8], p256dh: &[u8], auth: &[u8]) -> Result<Vec<u8>> {
    let rng = SystemRandom::new();
    let fail = || Error::Invalid("the push key could not be made".into());
    let own = EphemeralPrivateKey::generate(&ECDH_P256, &rng).map_err(|_| fail())?;
    let as_public = own.compute_public_key().map_err(|_| fail())?.as_ref().to_vec();
    let mut salt = [0u8; 16];
    rng.fill(&mut salt).map_err(|_| fail())?;
    let ecdh = agree_ephemeral(own, &UnparsedPublicKey::new(&ECDH_P256, p256dh), |s| s.to_vec()).map_err(|_| Error::Invalid("the phone's push key is not a P-256 point".into()))?;
    encrypt_with(&ecdh, &as_public, p256dh, auth, &salt, plaintext)
}

/// The plaintext of a body ``encrypt_with`` made, given the secret the phone's key and the sender's public key agree on.
pub fn decrypt_with(ecdh: &[u8], body: &[u8], ua_public: &[u8], auth: &[u8]) -> Result<Vec<u8>> {
    let bad = || Error::Invalid("that is not a push body for this phone".into());
    if body.len() < 21 + 65 + 17 || body[20] != 65 {
        return Err(bad());
    }
    let (salt, as_public, record) = (&body[..16], &body[21..86], &body[86..]);
    let (key, nonce) = derive(ecdh, as_public, ua_public, auth, salt)?;
    let opening = LessSafeKey::new(UnboundKey::new(&AES_128_GCM, &key).map_err(|_| bad())?);
    let mut buf = record.to_vec();
    let plain = opening.open_in_place(Nonce::assume_unique_for_key(nonce), Aad::empty(), &mut buf).map_err(|_| bad())?;
    match plain.iter().rposition(|b| *b != 0) {
        Some(end) if plain[end] == 2 => Ok(plain[..end].to_vec()),
        _ => Err(bad()),
    }
}

/// Where a sealed body goes. Sending never waits for the network and never fails the caller: a push that cannot be delivered is dropped,
/// and the request is still on the node.
pub trait Pusher: Send {
    fn send(&self, endpoint: &str, body: Vec<u8>);
}

/// The file in the state directory that lists the hosts inside the network a person allows pushes to (a distributor on the LAN).
pub const HOSTS_FILE: &str = "push-hosts.json";
const POST_TIMEOUT: Duration = Duration::from_secs(15);

/// The address of a push endpoint, split.
struct Target<'a> {
    host: &'a str,
    port: u16,
    path: &'a str,
}

fn parse_endpoint(url: &str) -> Result<Target<'_>> {
    let rest = url.strip_prefix("https://").ok_or_else(|| Error::Invalid("a push address is an https address".into()))?;
    let (authority, path) = rest.find(['/', '?']).map_or((rest, "/"), |i| (&rest[..i], &rest[i..]));
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) => (h, p.parse::<u16>().map_err(|_| Error::Invalid("a push address names a port from 1 to 65535".into()))?),
        None => (authority, 443),
    };
    if host.is_empty() || host.contains('@') || port == 0 || !url.bytes().all(|b| b.is_ascii_graphic()) {
        return Err(Error::Invalid("a push address is a host, an optional port and a path".into()));
    }
    Ok(Target { host, port, path })
}

/// Whether ``ip`` is a public address: a push never leaves for one inside the network unless a person listed its host.
pub fn public(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => {
            let o = v4.octets();
            !(v4.is_loopback() || v4.is_private() || v4.is_link_local() || v4.is_unspecified() || v4.is_multicast() || v4.is_broadcast() || v4.is_documentation()
                || (o[0] == 100 && (64..128).contains(&o[1])) || o[0] == 0 || o[0] >= 240)
        }
        IpAddr::V6(v6) => {
            let s = v6.segments();
            !(v6.is_loopback() || v6.is_unspecified() || v6.is_multicast() || s[0] & 0xfe00 == 0xfc00 || s[0] & 0xffc0 == 0xfe80 || v6.to_ipv4_mapped().is_some_and(|m| !public(IpAddr::V4(m))))
        }
    }
}

/// POST ``body`` to ``endpoint`` over TLS, trusting ``roots``; Ok when the push service answered 2xx. ``allowed`` lists hosts that may be
/// inside the network.
pub fn post_with(endpoint: &str, body: &[u8], roots: Arc<RootCertStore>, allowed: &[String]) -> Result<()> {
    let target = parse_endpoint(endpoint)?;
    let listed = allowed.iter().any(|h| h == target.host);
    let candidates: Vec<_> = (target.host, target.port).to_socket_addrs()?.filter(|a| listed || public(a.ip())).collect();
    if candidates.is_empty() {
        return Err(Error::Denied("a push goes only to a public address, unless a person lists its host".into()));
    }
    let tcp = candidates.iter().find_map(|a| TcpStream::connect_timeout(a, POST_TIMEOUT).ok())
        .ok_or_else(|| Error::Denied("the push service could not be reached".into()))?;
    tcp.set_read_timeout(Some(POST_TIMEOUT))?;
    tcp.set_write_timeout(Some(POST_TIMEOUT))?;
    let config = ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_protocol_versions(&[&rustls::version::TLS13]).map_err(|e| Error::Invalid(format!("tls: {e}")))?
        .with_root_certificates(roots).with_no_client_auth();
    let name = ServerName::try_from(target.host.to_string()).map_err(|_| Error::Invalid("the push host is not a name".into()))?;
    let mut conn = ClientConnection::new(Arc::new(config), name).map_err(|e| Error::Invalid(format!("tls: {e}")))?;
    let mut tcp = tcp;
    while conn.is_handshaking() {
        conn.complete_io(&mut tcp).map_err(|e| Error::Denied(format!("the push service's certificate was refused: {e}")))?;
    }
    let mut tls = StreamOwned::new(conn, tcp);
    write!(tls, "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/octet-stream\r\nContent-Encoding: aes128gcm\r\nContent-Length: {}\r\nTTL: 86400\r\nUrgency: high\r\nConnection: close\r\n\r\n",
           target.path, target.host, body.len())?;
    tls.write_all(body)?;
    tls.flush()?;
    let mut head = [0u8; 12];
    tls.read_exact(&mut head)?;
    if head.starts_with(b"HTTP/1.") && head[9] == b'2' {
        Ok(())
    } else {
        Err(Error::Denied("the push service did not take the message".into()))
    }
}

/// The real push service of a node: sealed bodies are posted from a thread of their own, to a public address over TLS, and a failure is dropped.
pub struct Web {
    queue: Mutex<std::sync::mpsc::Sender<(String, Vec<u8>)>>,
}

impl Web {
    /// Start posting for the node whose state directory is ``state``.
    pub fn start(state: &Path) -> Web {
        let (tx, rx) = std::sync::mpsc::channel::<(String, Vec<u8>)>();
        let hosts = state.join(HOSTS_FILE);
        std::thread::spawn(move || {
            let roots = Arc::new(RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() });
            for (endpoint, body) in rx {
                let allowed: Vec<String> = std::fs::read(&hosts).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
                let _ = post_with(&endpoint, &body, roots.clone(), &allowed);
            }
        });
        Web { queue: Mutex::new(tx) }
    }
}

impl Pusher for Web {
    fn send(&self, endpoint: &str, body: Vec<u8>) {
        if let Ok(q) = self.queue.lock() {
            let _ = q.send((endpoint.to_string(), body));
        }
    }
}

/// A node that pushes nothing.
pub struct NoPusher;

impl Pusher for NoPusher {
    fn send(&self, _endpoint: &str, _body: Vec<u8>) {}
}

/// A push service that keeps what it was given: every address and the ciphertext sent to it, and nothing else.
#[derive(Clone, Default)]
pub struct Recording(pub Arc<Mutex<Vec<(String, Vec<u8>)>>>);

impl Pusher for Recording {
    fn send(&self, endpoint: &str, body: Vec<u8>) {
        if let Ok(mut all) = self.0.lock() {
            all.push((endpoint.to_string(), body));
        }
    }
}

impl Recording {
    /// What the service holds so far.
    pub fn sent(&self) -> Vec<(String, Vec<u8>)> {
        self.0.lock().map(|a| a.clone()).unwrap_or_default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fsutil::unhex;

    fn h(s: &str) -> Vec<u8> {
        unhex(s).expect("hex")
    }

    #[test]
    fn the_rfc_8291_example_is_reproduced_byte_for_byte_and_opens() {
        let (ecdh, as_public, ua_public, auth) = (
            h("932acbd63208387133837b0cd995911c3441eb66000998614a592727aef6912b"),
            h("04fe33f4ab0dea71914db55823f73b54948f41306d920732dbb9a59a53286482200e597a7b7bc260ba1c227998580992e93973002f3012a28ae8f06bbb78e5ec0f"),
            h("042571b2becdfde360551aaf1ed0f4cd366c11cebe555f89bcb7b186a53339173168ece2ebe018597bd30479b86e3c8f8eced577ca59187e9246990db682008b0e"),
            h("05305932a1c7eabe13b6cec9fda48882"),
        );
        let salt: [u8; 16] = h("0c6bfaadad67958803092d454676f397").try_into().expect("16 bytes");
        let expected = h("0c6bfaadad67958803092d454676f397000010004104fe33f4ab0dea71914db55823f73b54948f41306d920732dbb9a59a53286482200e597a7b7bc260ba1c227998580992e93973002f3012a28ae8f06bbb78e5ec0ff297de5b429bba7153d3a4ae0caa091fd425f3b4b5414add8ab37a19c1bbb05cf5cb5b2a2e0562d558635641ec52812c6c8ff42e95ccb86be7cd");
        let text = b"When I grow up, I want to be a watermelon";
        assert_eq!(encrypt_with(&ecdh, &as_public, &ua_public, &auth, &salt, text).unwrap(), expected);
        assert_eq!(decrypt_with(&ecdh, &expected, &ua_public, &auth).unwrap(), text);
    }

    #[test]
    fn a_sealed_body_opens_only_with_the_phones_key_and_secret() {
        let rng = SystemRandom::new();
        let phone = EphemeralPrivateKey::generate(&ECDH_P256, &rng).unwrap();
        let ua_public = phone.compute_public_key().unwrap().as_ref().to_vec();
        let auth = [7u8; 16];
        let body = seal(b"approval waiting", &ua_public, &auth).unwrap();
        assert!(!body.windows(8).any(|w| w == b"approval"), "the push service reads nothing of the text");
        let as_public = body[21..86].to_vec();
        let ecdh = agree_ephemeral(phone, &UnparsedPublicKey::new(&ECDH_P256, &as_public), |s| s.to_vec()).unwrap();
        assert_eq!(decrypt_with(&ecdh, &body, &ua_public, &auth).unwrap(), b"approval waiting");
        assert!(decrypt_with(&ecdh, &body, &ua_public, &[8u8; 16]).is_err(), "another secret opens nothing");
        let mut torn = body.clone();
        *torn.last_mut().unwrap() ^= 1;
        assert!(decrypt_with(&ecdh, &torn, &ua_public, &auth).is_err(), "a changed byte is refused");
    }

    #[test]
    fn a_push_larger_than_the_limit_is_refused() {
        let rng = SystemRandom::new();
        let phone = EphemeralPrivateKey::generate(&ECDH_P256, &rng).unwrap();
        let ua_public = phone.compute_public_key().unwrap().as_ref().to_vec();
        assert!(seal(&vec![b'a'; MAX_PLAINTEXT + 1], &ua_public, &[0u8; 16]).is_err());
        assert!(seal(&vec![b'a'; MAX_PLAINTEXT], &ua_public, &[0u8; 16]).is_ok());
    }
}
