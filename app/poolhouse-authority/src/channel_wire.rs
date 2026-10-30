//! Bounded framing for the one native authority connection. Decoding data is
//! never caller admission, installation proof, or permission to perform work.
use sha2::{Digest, Sha256};

const HEADER: &[u8; 5] = b"PHAC\x02";
const DOMAIN_OPEN: &[u8] = b"poolhouse.authority.channel.open.v2\0";
const DOMAIN_TRANSCRIPT: &[u8] = b"poolhouse.authority.channel.transcript.v2\0";
const OPEN_LEN: usize = 5 + 1 + 32;
const BOUND_LEN: usize = 5 + 1 + 9 * 32 + 3 * 8 + 3 * 4;
const CONFIRM_LEN: usize = 5 + 1 + 32;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Binding {
    pub node_nonce: [u8;32], pub service_nonce: [u8;32], pub open_digest: [u8;32],
    pub installation: [u8;32], pub policy: [u8;32],
    pub node_identity: [u8;32], pub service_identity: [u8;32],
    pub node_incarnation: [u8;32], pub service_incarnation: [u8;32],
    pub installation_generation: u64, pub policy_generation: u64, pub cutover_generation: u64,
    // The service may run in the OS default audit session (zero). Each value
    // still requires an independent observation of its own held connection.
    pub owner_uid: u32, pub node_asid: u32, pub service_asid: u32,
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
                    || b.owner_uid == 0 || b.owner_uid == u32::MAX || b.node_asid == 0 || b.node_asid == u32::MAX || b.service_asid == u32::MAX { return Err(InvalidFrame); }
                out.push(2);
                for value in [b.node_nonce,b.service_nonce,b.open_digest,b.installation,b.policy,b.node_identity,
                    b.service_identity,b.node_incarnation,b.service_incarnation] { out.extend_from_slice(&value); }
                for value in [b.installation_generation,b.policy_generation,b.cutover_generation] { out.extend_from_slice(&value.to_be_bytes()); }
                out.extend_from_slice(&b.owner_uid.to_be_bytes()); out.extend_from_slice(&b.node_asid.to_be_bytes()); out.extend_from_slice(&b.service_asid.to_be_bytes());
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
                node_asid:u32::from_be_bytes(fixed(bytes,&mut i)?), service_asid:u32::from_be_bytes(fixed(bytes,&mut i)?),
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

/// The connection owner supplies this comparison data only after independently
/// checking its protected Store and operating-system session. This type is
/// data, not proof that those checks happened.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Expected {
    pub installation: [u8;32], pub policy: [u8;32],
    pub node_identity: [u8;32], pub service_identity: [u8;32],
    pub node_incarnation: [u8;32], pub service_incarnation: [u8;32],
    pub installation_generation: u64, pub policy_generation: u64, pub cutover_generation: u64,
    pub owner_uid: u32, pub node_asid: u32, pub service_asid: u32,
}

impl Expected {
    fn matches(&self, b: &Binding) -> bool {
        self.installation == b.installation && self.policy == b.policy && self.node_identity == b.node_identity
            && self.service_identity == b.service_identity && self.node_incarnation == b.node_incarnation
            && self.service_incarnation == b.service_incarnation && self.installation_generation == b.installation_generation
            && self.policy_generation == b.policy_generation && self.cutover_generation == b.cutover_generation
            && self.owner_uid == b.owner_uid && self.node_asid == b.node_asid && self.service_asid == b.service_asid
    }
}

/// Each instance belongs to one retained connection. Consuming it prevents a
/// second confirmation; the channel owner must also invalidate it on disconnect.
pub struct NodeHandshake { open: Vec<u8>, expected: Expected }
impl NodeHandshake {
    pub fn start(nonce: [u8;32], expected: Expected) -> Result<(Self,Vec<u8>), InvalidFrame> {
        let open = Frame::Open(nonce).encode()?;
        Ok((Self { open:open.clone(), expected },open))
    }
    pub fn accept(self, bound: &[u8]) -> Result<(Vec<u8>,[u8;32]), InvalidFrame> {
        let Frame::Bound(value) = Frame::decode(bound)? else { return Err(InvalidFrame) };
        if !self.expected.matches(&value) { return Err(InvalidFrame); }
        let digest = transcript(&self.open,bound)?;
        Ok((Frame::Confirm(digest).encode()?,digest))
    }
}

pub struct ServiceHandshake { open: Vec<u8>, bound: Vec<u8> }
impl ServiceHandshake {
    pub fn start(open: &[u8], binding: Binding) -> Result<(Self,Vec<u8>), InvalidFrame> {
        let bound = Frame::Bound(binding).encode()?;
        transcript(open,&bound)?;
        Ok((Self { open:open.to_vec(), bound:bound.clone() },bound))
    }
    pub fn accept(self, confirm: &[u8]) -> Result<[u8;32], InvalidFrame> {
        let Frame::Confirm(value) = Frame::decode(confirm)? else { return Err(InvalidFrame) };
        let expected = transcript(&self.open,&self.bound)?;
        if value != expected { return Err(InvalidFrame); }
        Ok(expected)
    }
}

#[cfg(target_os = "macos")]
#[allow(unsafe_code)]
#[no_mangle]
pub extern "C" fn ph_authority_frame_limit() -> usize { BOUND_LEN }

/// Private C transport ABI. The pointer must address `len` immutable bytes
/// owned by the serial transport for this call; IPC values are copied first.
/// This checks framing only and never issues an authenticated capability.
#[cfg(target_os = "macos")]
#[allow(unsafe_code)]
#[no_mangle]
pub unsafe extern "C" fn ph_authority_frame_kind(bytes: *const u8, len: usize) -> u8 {
    if bytes.is_null() || len > BOUND_LEN || len < 6 { return 0; }
    let input = unsafe { std::slice::from_raw_parts(bytes,len) };
    match Frame::decode(input) {
        Ok(Frame::Open(_)) => 1,
        Ok(Frame::Bound(_)) => 2,
        Ok(Frame::Confirm(_)) => 3,
        Err(_) => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[allow(unsafe_code)]
    fn exact_transcript_rejects_splice_and_malformed_frames() {
        let open = Frame::Open([1;32]).encode().unwrap();
        #[cfg(target_os = "macos")]
        unsafe {
            assert_eq!(ph_authority_frame_kind(open.as_ptr(),open.len()),1);
            assert_eq!(ph_authority_frame_kind(std::ptr::null(),open.len()),0);
            assert_eq!(ph_authority_frame_kind(open.as_ptr(),usize::MAX),0);
        }
        assert_eq!(open_digest(&open).unwrap(),[0xde,0x5d,0xf3,0xbe,0xc8,0x1e,0x38,0xb6,0x25,0x11,0xc4,0xde,0xe6,0x84,0xdd,0xdb,0x02,0x6f,0x96,0xfc,0x59,0xa6,0x80,0x5e,0x2b,0xd4,0x44,0xc7,0xdf,0x65,0x6e,0xbd]);
        let binding = Binding { node_nonce:[1;32],service_nonce:[2;32],open_digest:open_digest(&open).unwrap(),
            installation:[3;32],policy:[4;32],node_identity:[5;32],service_identity:[6;32],
            node_incarnation:[7;32],service_incarnation:[8;32],installation_generation:1,policy_generation:2,
            cutover_generation:1,owner_uid:501,node_asid:100,service_asid:0 };
        let expected = Expected { installation:binding.installation,policy:binding.policy,node_identity:binding.node_identity,
            service_identity:binding.service_identity,node_incarnation:binding.node_incarnation,service_incarnation:binding.service_incarnation,
            installation_generation:binding.installation_generation,policy_generation:binding.policy_generation,
            cutover_generation:binding.cutover_generation,owner_uid:binding.owner_uid,node_asid:binding.node_asid,service_asid:binding.service_asid };
        let (node, actual_open) = NodeHandshake::start([1;32],expected.clone()).unwrap();
        assert_eq!(actual_open,open);
        let (service,bound) = ServiceHandshake::start(&open,binding.clone()).unwrap();
        assert_eq!(Frame::decode(&bound),Ok(Frame::Bound(binding.clone())));
        let confirm = Frame::Confirm(transcript(&open,&bound).unwrap()).encode().unwrap();
        assert_eq!(Frame::decode(&confirm).unwrap(),Frame::Confirm(transcript(&open,&bound).unwrap()));
        assert_eq!(node.accept(&bound).unwrap().0,confirm);
        assert_eq!(service.accept(&confirm).unwrap(),transcript(&open,&bound).unwrap());
        let mut wrong = expected.clone(); wrong.policy[0] ^= 1;
        assert!(NodeHandshake::start([1;32],wrong).unwrap().0.accept(&bound).is_err());
        let mut other_binding = binding.clone(); other_binding.service_nonce = [10;32];
        let other_service = ServiceHandshake::start(&open,other_binding).unwrap().0;
        assert!(other_service.accept(&confirm).is_err());
        let other = Frame::Open([9;32]).encode().unwrap();
        assert!(transcript(&other,&bound).is_err());
        for index in 0..bound.len() { assert!(Frame::decode(&bound[..index]).is_err()); }
        let mut trailing = bound.clone(); trailing.push(0); assert!(Frame::decode(&trailing).is_err());
        let mut version = bound.clone(); version[4] = 1; assert!(Frame::decode(&version).is_err());
        let mut kind = bound.clone(); kind[5] = 4; assert!(Frame::decode(&kind).is_err());
        assert!(Frame::Open([0;32]).encode().is_err());
        let mut wrong_session = expected.clone(); wrong_session.service_asid = 1;
        assert!(NodeHandshake::start([1;32],wrong_session).unwrap().0.accept(&bound).is_err());
        let mut invalid_session = binding.clone(); invalid_session.service_asid = u32::MAX;
        assert!(Frame::Bound(invalid_session).encode().is_err());
        let mut no_cutover = binding; no_cutover.cutover_generation = 0;
        assert!(Frame::Bound(no_cutover).encode().is_err());
    }
}
