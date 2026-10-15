use super::{AttestationError, Result};

#[derive(Clone, Copy, Debug)]
pub(super) struct Value<'a> {
    pub class: u8,
    pub tag: u32,
    pub constructed: bool,
    pub bytes: &'a [u8],
}

pub(super) fn take<'a>(input: &mut &'a [u8]) -> Result<Value<'a>> {
    let first = *input.first().ok_or(AttestationError::Encoding)?;
    *input = &input[1..];
    let mut tag = u32::from(first & 31);
    if tag == 31 {
        tag = 0;
        for n in 0..5 {
            let byte = *input.first().ok_or(AttestationError::Encoding)?;
            *input = &input[1..];
            if n == 0 && byte & 127 == 0 {
                return Err(AttestationError::Encoding);
            }
            tag = tag
                .checked_mul(128)
                .and_then(|v| v.checked_add(u32::from(byte & 127)))
                .ok_or(AttestationError::Encoding)?;
            if byte & 128 == 0 {
                break;
            }
            if n == 4 {
                return Err(AttestationError::Encoding);
            }
        }
        if tag < 31 {
            return Err(AttestationError::Encoding);
        }
    }
    let length = *input.first().ok_or(AttestationError::Encoding)?;
    *input = &input[1..];
    let length = if length & 128 == 0 {
        usize::from(length)
    } else {
        let count = usize::from(length & 127);
        if count == 0 || count > 3 || input.len() < count || input[0] == 0 {
            return Err(AttestationError::Encoding);
        }
        let mut value = 0usize;
        for byte in &input[..count] {
            value = value * 256 + usize::from(*byte);
        }
        *input = &input[count..];
        if value < 128 {
            return Err(AttestationError::Encoding);
        }
        value
    };
    if input.len() < length {
        return Err(AttestationError::Encoding);
    }
    let (bytes, rest) = input.split_at(length);
    *input = rest;
    Ok(Value {
        class: first >> 6,
        tag,
        constructed: first & 32 != 0,
        bytes,
    })
}

pub(super) fn one(input: &[u8], tag: u32) -> Result<Value<'_>> {
    let mut remaining = input;
    let value = take(&mut remaining)?;
    if !remaining.is_empty()
        || value.class != 0
        || value.tag != tag
        || value.constructed != matches!(tag, 16 | 17)
    {
        return Err(AttestationError::Encoding);
    }
    Ok(value)
}

pub(super) fn number(value: Value<'_>, tag: u32) -> Result<u64> {
    if value.class != 0
        || value.constructed
        || value.tag != tag
        || value.bytes.is_empty()
        || value.bytes.len() > 9
        || value.bytes[0] & 128 != 0
    {
        return Err(AttestationError::Encoding);
    }
    if value.bytes.len() > 1 && value.bytes[0] == 0 && value.bytes[1] & 128 == 0 {
        return Err(AttestationError::Encoding);
    }
    value.bytes.iter().try_fold(0u64, |n, byte| {
        n.checked_mul(256)
            .and_then(|n| n.checked_add(u64::from(*byte)))
            .ok_or(AttestationError::Encoding)
    })
}

pub(super) fn bounded(input: &[u8], depth: usize) -> Result<()> {
    if depth > 16 || input.len() > 16_384 {
        return Err(AttestationError::Bounds);
    }
    let mut remaining = input;
    let mut count = 0;
    while !remaining.is_empty() {
        count += 1;
        if count > 256 {
            return Err(AttestationError::Bounds);
        }
        let value = take(&mut remaining)?;
        if value.constructed {
            bounded(value.bytes, depth + 1)?;
        }
    }
    Ok(())
}
