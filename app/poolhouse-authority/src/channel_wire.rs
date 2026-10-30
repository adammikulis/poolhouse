//! Bounded framing for the one native authority connection. Decoding data is
//! never caller admission, installation proof, or permission to perform work.
use sha2::{Digest, Sha256};

const HEADER: &[u8; 5] = b"PHAC\x01";
const DOMAIN_OPEN: &[u8] = b"poolhouse.authority.channel.open.v1\0";
const DOMAIN_TRANSCRIPT: &[u8] = b"poolhouse.authority.channel.transcript.v1\0";
const OPEN_LEN: usize = 5 + 1 + 32;
const BOUND_LEN: usize = 5 + 1 + 9 * 32 + 3 * 8 + 2 * 4;
const CONFIRM_LEN: usize = 5 + 1 + 32;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Binding {
    pub node_nonce: [u8;32], pub service_nonce: [u8;32], pub open_digest: [u8;32],
    pub installation: [u8;32], pub policy: [u8;32],
    pub node_identity: [u8;32], pub service_identity: [u8;32],
    pub node_incarnation: [u8;32], pub service_incarnation: [u8;32],
    pub installation_generation: u64, pub policy_generation: u64, pub cutover_generation: u64,
    pub owner_uid: u32, pub owner_asid: u32,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Frame { Open([u8;32]), Bound(Binding), Confirm([u8;32]) }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct InvalidFrame;

fn digest(domain: &[u8], parts: &[&[u8]]) -> [u8;32] {
    let mut hash = Sha256::new(); hash.update(domain);
    for part in parts { hash.update(part); }
    hash.finalize().into()
}

impl Frame {
    pub fn encode(&self) -> Result<Vec<u8>, InvalidFrame> {
        let mut out = HEADER.to_vec();
        match self {
            Self::Open(nonce) if *nonce != [0;32] => { out.push(1); out.extend_from_slice(nonce); }
            Self::Confirm(value) if *value != [0;32] => { out.push(3); out.extend_from_slice(value); }
            Self::Bound(b) => {
                if [b.node_nonce,b.service_nonce,b.open_digest,b.installation,b.policy,b.node_identity,
                    b.service_identity,b.node_incarnation,b.service_incarnation].contains(&[0;32])
                    || b.node_nonce == b.service_nonce || b.installation_generation == 0 || b.policy_generation == 0 || b.cutover_generation == 0
                    || b.owner_uid == 0 || b.owner_uid == u32::MAX || b.owner_asid == 0 || b.owner_asid == u32::MAX { return Err(InvalidFrame); }
                out.push(2);
                for value in [b.node_nonce,b.service_nonce,b.open_digest,b.installation,b.policy,b.node_identity,
                    b.service_identity,b.node_incarnation,b.service_incarnation] { out.extend_from_slice(&value); }
                for value in [b.installation_generation,b.policy_generation,b.cutover_generation] { out.extend_from_slice(&value.to_be_bytes()); }
                out.extend_from_slice(&b.owner_uid.to_be_bytes()); out.extend_from_slice(&b.owner_asid.to_be_bytes());
            }
            _ => return Err(InvalidFrame),
        }
        Ok(out)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, InvalidFrame> {
        if bytes.len() < 6 || &bytes[..5] != HEADER { return Err(InvalidFrame); }
        fn fixed<const N: usize>(bytes: &[u8], offset: &mut usize) -> Result<[u8;N], InvalidFrame> {
            let end = offset.checked_add(N).ok_or(InvalidFrame)?;
            let value = bytes.get(*offset..end).ok_or(InvalidFrame)?.try_into().map_err(|_| InvalidFrame)?;
            *offset = end; Ok(value)
        }
        let mut i = 6;
        let frame = match bytes[5] {
            1 if bytes.len() == OPEN_LEN => Self::Open(fixed(bytes,&mut i)?),
            3 if bytes.len() == CONFIRM_LEN => Self::Confirm(fixed(bytes,&mut i)?),
            2 if bytes.len() == BOUND_LEN => Self::Bound(Binding {
                node_nonce:fixed(bytes,&mut i)?, service_nonce:fixed(bytes,&mut i)?, open_digest:fixed(bytes,&mut i)?,
                installation:fixed(bytes,&mut i)?, policy:fixed(bytes,&mut i)?, node_identity:fixed(bytes,&mut i)?,
                service_identity:fixed(bytes,&mut i)?, node_incarnation:fixed(bytes,&mut i)?, service_incarnation:fixed(bytes,&mut i)?,
                installation_generation:u64::from_be_bytes(fixed(bytes,&mut i)?), policy_generation:u64::from_be_bytes(fixed(bytes,&mut i)?),
                cutover_generation:u64::from_be_bytes(fixed(bytes,&mut i)?), owner_uid:u32::from_be_bytes(fixed(bytes,&mut i)?),
                owner_asid:u32::from_be_bytes(fixed(bytes,&mut i)?),
            }),
            _ => return Err(InvalidFrame),
        };
        if i != bytes.len() || frame.encode()?.as_slice() != bytes { return Err(InvalidFrame); }
        Ok(frame)
    }
}

/// Build the transcript data only after both endpoints independently verify
/// the protected binding and retain the same actual connection.
pub fn transcript(open: &[u8], bound: &[u8]) -> Result<[u8;32], InvalidFrame> {
    let Frame::Open(nonce) = Frame::decode(open)? else { return Err(InvalidFrame) };
    let Frame::Bound(binding) = Frame::decode(bound)? else { return Err(InvalidFrame) };
    if binding.node_nonce != nonce || binding.open_digest != digest(DOMAIN_OPEN,&[open]) { return Err(InvalidFrame); }
    Ok(digest(DOMAIN_TRANSCRIPT,&[open,bound]))
}

/// A digest is framing data. It does not establish that this Open was received
/// from an authenticated process or on a current connection.
pub fn open_digest(open: &[u8]) -> Result<[u8;32], InvalidFrame> {
    if !matches!(Frame::decode(open)?,Frame::Open(_)) { return Err(InvalidFrame); }
    Ok(digest(DOMAIN_OPEN,&[open]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_transcript_rejects_splice_and_malformed_frames() {
        let open = Frame::Open([1;32]).encode().unwrap();
        assert_eq!(open_digest(&open).unwrap(),[0xf5,0x0b,0x1e,0x8b,0xc9,0x38,0x27,0x62,0x44,0x26,0xb6,0xc6,0x11,0xc7,0x92,0x15,
            0xc9,0xe7,0x7e,0xfe,0xbe,0x30,0xd2,0xba,0xb2,0xbf,0xc2,0xd0,0x5f,0xa3,0x52,0x30]);
        let binding = Binding { node_nonce:[1;32],service_nonce:[2;32],open_digest:open_digest(&open).unwrap(),
            installation:[3;32],policy:[4;32],node_identity:[5;32],service_identity:[6;32],
            node_incarnation:[7;32],service_incarnation:[8;32],installation_generation:1,policy_generation:2,
            cutover_generation:1,owner_uid:501,owner_asid:100 };
        let bound = Frame::Bound(binding.clone()).encode().unwrap();
        assert_eq!(Frame::decode(&bound),Ok(Frame::Bound(binding.clone())));
        let confirm = Frame::Confirm(transcript(&open,&bound).unwrap()).encode().unwrap();
        assert_eq!(Frame::decode(&confirm).unwrap(),Frame::Confirm(transcript(&open,&bound).unwrap()));
        let other = Frame::Open([9;32]).encode().unwrap();
        assert!(transcript(&other,&bound).is_err());
        for index in 0..bound.len() { assert!(Frame::decode(&bound[..index]).is_err()); }
        let mut trailing = bound.clone(); trailing.push(0); assert!(Frame::decode(&trailing).is_err());
        let mut version = bound.clone(); version[4] = 2; assert!(Frame::decode(&version).is_err());
        let mut kind = bound.clone(); kind[5] = 4; assert!(Frame::decode(&kind).is_err());
        assert!(Frame::Open([0;32]).encode().is_err());
        let mut no_cutover = binding; no_cutover.cutover_generation = 0;
        assert!(Frame::Bound(no_cutover).encode().is_err());
    }
}
