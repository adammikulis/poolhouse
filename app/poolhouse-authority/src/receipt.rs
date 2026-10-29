//! Fixed signed phone-bootstrap receipt bytes shared by the service and node.
//!
//! A valid signature only proves the bytes were signed by the supplied key. The
//! caller must obtain that key from protected installation state and revalidate
//! the current offer, candidate, authority and governance before committing.

use ed25519_dalek::{Signature, VerifyingKey};

const DOMAIN: &[u8] = b"poolhouse/native-authority/receipt/v3\0";
type Hash = [u8; 32];

#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Listing { Unlisted, Active, Pending }

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Scope {
    Unfounded,
    Governed { anchor: Hash, head: Hash, listing: Listing },
}

/// One immutable phone-bootstrap decision; all hashes are raw SHA-256 bytes.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PhoneBootstrapReceipt {
    pub authority: Hash,
    pub installation: Hash,
    pub installation_generation: u64,
    pub request: Hash,
    pub nonce: Hash,
    pub transcript: Hash,
    pub node: Hash,
    pub tls: Hash,
    pub phone_key: Hash,
    pub offer: Hash,
    /// SHA-256 of the exact 32-byte enrollment token held in protected custody.
    pub token_hash: Hash,
    pub offer_expires: u64,
    pub scope: Scope,
    pub authority_head: Hash,
    pub authority_generation: u64,
    /// Protected envelope after durable prepare and before the final commit CAS.
    pub prepared_envelope_head: Hash,
    pub prepared_envelope_generation: u64,
    pub committed_at: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReceiptError { Invalid, Signature }

impl PhoneBootstrapReceipt {
    fn validate(&self) -> Result<(), ReceiptError> {
        if [self.authority, self.installation, self.request, self.nonce, self.transcript,
            self.node, self.tls, self.phone_key, self.offer, self.token_hash, self.prepared_envelope_head].iter().any(|value| *value == [0; 32])
            || self.installation_generation == 0 || self.prepared_envelope_generation == 0 || self.committed_at == 0 || self.committed_at >= self.offer_expires {
            return Err(ReceiptError::Invalid);
        }
        if let Scope::Governed { anchor, head, .. } = &self.scope {
            if *anchor == [0; 32] || *head == [0; 32] { return Err(ReceiptError::Invalid); }
        }
        Ok(())
    }

    /// Canonical domain-separated bytes, suitable for an independently held signer.
    pub fn encode(&self) -> Result<Vec<u8>, ReceiptError> {
        self.validate()?;
        let mut bytes = Vec::with_capacity(512);
        bytes.extend_from_slice(DOMAIN);
        bytes.extend_from_slice(&self.authority);
        bytes.extend_from_slice(&self.installation);
        bytes.extend_from_slice(&self.installation_generation.to_be_bytes());
        bytes.push(1); // phone bootstrap, never another authority operation
        for value in [self.request, self.nonce, self.transcript, self.node, self.tls, self.phone_key, self.offer] {
            bytes.extend_from_slice(&value);
        }
        bytes.extend_from_slice(&self.offer_expires.to_be_bytes());
        match self.scope {
            Scope::Unfounded => bytes.push(0),
            Scope::Governed { anchor, head, listing } => {
                bytes.push(1);
                bytes.extend_from_slice(&anchor);
                bytes.extend_from_slice(&head);
                bytes.push(match listing { Listing::Unlisted => 0, Listing::Active => 1, Listing::Pending => 2 });
            }
        }
        bytes.extend_from_slice(&self.authority_head);
        bytes.extend_from_slice(&self.authority_generation.to_be_bytes());
        bytes.extend_from_slice(&self.prepared_envelope_head);
        bytes.extend_from_slice(&self.prepared_envelope_generation.to_be_bytes());
        bytes.extend_from_slice(&self.committed_at.to_be_bytes());
        bytes.extend_from_slice(&self.token_hash);
        Ok(bytes)
    }

    /// Parse exactly one canonical receipt. Unknown variants and trailing bytes refuse.
    pub fn decode(bytes: &[u8]) -> Result<Self, ReceiptError> {
        let mut input = Reader { bytes, at: 0 };
        if input.take(DOMAIN.len())? != DOMAIN { return Err(ReceiptError::Invalid); }
        let authority = input.hash()?;
        let installation = input.hash()?;
        let installation_generation = input.number()?;
        if input.byte()? != 1 { return Err(ReceiptError::Invalid); }
        let (request, nonce, transcript, node, tls, phone_key, offer) =
            (input.hash()?, input.hash()?, input.hash()?, input.hash()?, input.hash()?, input.hash()?, input.hash()?);
        let offer_expires = input.number()?;
        let scope = match input.byte()? {
            0 => Scope::Unfounded,
            1 => {
                let (anchor, head) = (input.hash()?, input.hash()?);
                let listing = match input.byte()? { 0 => Listing::Unlisted, 1 => Listing::Active, 2 => Listing::Pending, _ => return Err(ReceiptError::Invalid) };
                Scope::Governed { anchor, head, listing }
            }
            _ => return Err(ReceiptError::Invalid),
        };
        let (authority_head, authority_generation, prepared_envelope_head, prepared_envelope_generation, committed_at, token_hash) =
            (input.hash()?, input.number()?, input.hash()?, input.number()?, input.number()?, input.hash()?);
        if input.at != bytes.len() { return Err(ReceiptError::Invalid); }
        let receipt = Self { authority, installation, installation_generation, request, nonce, transcript, node, tls,
                             phone_key, offer, token_hash, offer_expires, scope, authority_head, authority_generation,
                             prepared_envelope_head, prepared_envelope_generation, committed_at };
        receipt.validate()?;
        Ok(receipt)
    }

    /// Check Ed25519 bytes against the supplied public key; this is not an authorization decision.
    pub fn verify(bytes: &[u8], signature: &[u8], public: &[u8; 32]) -> Result<Self, ReceiptError> {
        let receipt = Self::decode(bytes)?;
        let key = VerifyingKey::from_bytes(public).map_err(|_| ReceiptError::Signature)?;
        let signature = Signature::from_slice(signature).map_err(|_| ReceiptError::Signature)?;
        key.verify_strict(bytes, &signature).map_err(|_| ReceiptError::Signature)?;
        Ok(receipt)
    }
}

struct Reader<'a> { bytes: &'a [u8], at: usize }
impl<'a> Reader<'a> {
    fn take(&mut self, count: usize) -> Result<&'a [u8], ReceiptError> {
        let end = self.at.checked_add(count).ok_or(ReceiptError::Invalid)?;
        let value = self.bytes.get(self.at..end).ok_or(ReceiptError::Invalid)?;
        self.at = end;
        Ok(value)
    }
    fn byte(&mut self) -> Result<u8, ReceiptError> { Ok(self.take(1)?[0]) }
    fn hash(&mut self) -> Result<Hash, ReceiptError> { self.take(32)?.try_into().map_err(|_| ReceiptError::Invalid) }
    fn number(&mut self) -> Result<u64, ReceiptError> { Ok(u64::from_be_bytes(self.take(8)?.try_into().map_err(|_| ReceiptError::Invalid)?)) }
}
