//! CDXCTX01 source identity DATA, never authenticated producer or live ancestry proof.
//! Expected source launch bytes are a caller-supplied comparison, not verified launch admission.
use std::num::NonZeroU64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum DecodeError {
    Prefix,
    Truncated,
    Trailing,
    Flag,
    Reserved,
    SourceEpoch,
    Serial,
    Structure,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct UntrustedOccurrence {
    raw: [u8; 32],
    source_uuid: [u8; 16],
    serial: NonZeroU64,
}
impl UntrustedOccurrence {
    pub(super) fn raw_bytes(&self) -> &[u8; 32] {
        &self.raw
    }
    pub(super) fn source_uuid(&self) -> &[u8; 16] {
        &self.source_uuid
    }
    pub(super) fn serial(&self) -> NonZeroU64 {
        self.serial
    }
}

/// These 128-bit upstream labels have no native placement or admission meaning.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct DiagnosticLabels {
    pub(super) actor: [u8; 16],
    pub(super) root: [u8; 16],
    pub(super) parent: Option<[u8; 16]>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct UntrustedActorContext<'a> {
    raw: &'a [u8],
    actor: UntrustedOccurrence,
    root: UntrustedOccurrence,
    parent: Option<UntrustedOccurrence>,
    labels: DiagnosticLabels,
}
impl<'a> UntrustedActorContext<'a> {
    pub(super) fn raw_bytes(&self) -> &'a [u8] {
        self.raw
    }
    pub(super) fn actor(&self) -> UntrustedOccurrence {
        self.actor
    }
    pub(super) fn root(&self) -> UntrustedOccurrence {
        self.root
    }
    pub(super) fn parent(&self) -> Option<UntrustedOccurrence> {
        self.parent
    }
    pub(super) fn diagnostic_labels(&self) -> DiagnosticLabels {
        self.labels
    }
}

struct Cursor<'a> {
    remaining: &'a [u8],
}
impl Cursor<'_> {
    fn take<const N: usize>(&mut self) -> Result<[u8; N], DecodeError> {
        let (value, rest) = self
            .remaining
            .split_at_checked(N)
            .ok_or(DecodeError::Truncated)?;
        self.remaining = rest;
        value.try_into().map_err(|_| DecodeError::Truncated)
    }
    fn flag(&mut self) -> Result<bool, DecodeError> {
        match self.take::<1>()?[0] {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(DecodeError::Flag),
        }
    }
    fn occurrence(&mut self, expected: &[u8; 16]) -> Result<UntrustedOccurrence, DecodeError> {
        let raw = self.take::<32>()?;
        if raw[24..] != [0; 8] {
            return Err(DecodeError::Reserved);
        }
        let source_uuid: [u8; 16] = raw[..16].try_into().map_err(|_| DecodeError::Truncated)?;
        if &source_uuid != expected {
            return Err(DecodeError::SourceEpoch);
        }
        let serial_bytes = raw[16..24].try_into().map_err(|_| DecodeError::Truncated)?;
        let serial =
            NonZeroU64::new(u64::from_be_bytes(serial_bytes)).ok_or(DecodeError::Serial)?;
        Ok(UntrustedOccurrence {
            raw,
            source_uuid,
            serial,
        })
    }
}

/// Structural compatibility only. Successful decoding creates freely copyable UNTRUSTED DATA.
/// It cannot establish that a parent existed, is live, or authorized a child or operation.
pub(super) fn decode<'a>(
    input: &'a [u8],
    expected_source_launch: &[u8; 32],
) -> Result<UntrustedActorContext<'a>, DecodeError> {
    if expected_source_launch[16..] != [0; 16] {
        return Err(DecodeError::Reserved);
    }
    let expected: [u8; 16] = expected_source_launch[..16]
        .try_into()
        .map_err(|_| DecodeError::SourceEpoch)?;
    if expected[6] & 0xf0 != 0x40 || expected[8] & 0xc0 != 0x80 {
        return Err(DecodeError::SourceEpoch);
    }
    let mut cursor = Cursor { remaining: input };
    if cursor.take::<8>()? != *b"CDXCTX01" {
        return Err(DecodeError::Prefix);
    }
    let actor = cursor.occurrence(&expected)?;
    let root = cursor.occurrence(&expected)?;
    let parent = if cursor.flag()? {
        Some(cursor.occurrence(&expected)?)
    } else {
        None
    };
    let labels = DiagnosticLabels {
        actor: cursor.take::<16>()?,
        root: cursor.take::<16>()?,
        parent: if cursor.flag()? {
            Some(cursor.take::<16>()?)
        } else {
            None
        },
    };
    if !cursor.remaining.is_empty() {
        return Err(DecodeError::Trailing);
    }
    match parent {
        None if root != actor => return Err(DecodeError::Structure),
        Some(parent) if !(root.serial <= parent.serial && parent.serial < actor.serial) => {
            return Err(DecodeError::Structure)
        }
        _ => (),
    }
    Ok(UntrustedActorContext {
        raw: input,
        actor,
        root,
        parent,
        labels,
    })
}
