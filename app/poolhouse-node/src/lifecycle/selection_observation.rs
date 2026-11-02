//! Borrowed source observation DATA only. No admission, ledger access or effect rights.
use super::original_message;
#[derive(Clone, Copy)]
struct Case {
    tag: u16,
    item: usize,
}
#[derive(Clone, Copy)]
enum Type {
    Unit,
    String,
    Bool,
    I64,
    U64,
    Path,
    EventKind,
    ValueObject,
    Record(&'static [usize]),
    Union(&'static [Case]),
    Optional(usize),
    List(usize),
}
#[path = "../contract_native_selection.rs"]
mod grammar;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum DecodeError {
    Truncated,
    Trailing,
    Version,
    Platform,
    Length,
    Work,
    Scratch,
    Flag,
    Tag,
    Utf8,
    Context,
    Index,
    Phase,
    Declaration,
    Value,
}
fn original_error(error: original_message::DecodeError, fallback: DecodeError) -> DecodeError {
    match error {
        original_message::DecodeError::Work => DecodeError::Work,
        original_message::DecodeError::Scratch => DecodeError::Scratch,
        _ => fallback,
    }
}
#[derive(Clone, Copy, Default)]
pub(super) enum Scratch {
    #[default]
    Empty,
    Task(usize),
    Repeat {
        item: usize,
        remaining: usize,
    },
}
/// Contains private source declarations. No Debug, serde or authenticated constructor.
pub(super) struct UntrustedInvocationObservation<'a> {
    original: original_message::UntrustedInvocation<'a>,
    selection: &'a [u8],
    configuration: [u8; 32],
    entries: u64,
}
impl<'a> UntrustedInvocationObservation<'a> {
    pub(super) fn original(&self) -> &original_message::UntrustedInvocation<'a> {
        &self.original
    }
    pub(super) fn selection_bytes(&self) -> &'a [u8] {
        self.selection
    }
    pub(super) fn configuration(&self) -> &[u8; 32] {
        &self.configuration
    }
    pub(super) fn entries(&self) -> u64 {
        self.entries
    }
}
struct Cursor<'a, 'w> {
    input: &'a [u8],
    at: usize,
    work: &'w mut u64,
}
impl<'a> Cursor<'a, '_> {
    fn charge(&mut self, n: usize) -> Result<(), DecodeError> {
        *self.work = self
            .work
            .checked_sub(u64::try_from(n).map_err(|_| DecodeError::Work)?)
            .ok_or(DecodeError::Work)?;
        Ok(())
    }
    fn take(&mut self, n: usize) -> Result<&'a [u8], DecodeError> {
        self.charge(n)?;
        let end = self.at.checked_add(n).ok_or(DecodeError::Length)?;
        let value = self.input.get(self.at..end).ok_or(DecodeError::Truncated)?;
        self.at = end;
        Ok(value)
    }
    fn fixed<const N: usize>(&mut self) -> Result<[u8; N], DecodeError> {
        self.take(N)?.try_into().map_err(|_| DecodeError::Truncated)
    }
    fn u8(&mut self) -> Result<u8, DecodeError> {
        Ok(self.fixed::<1>()?[0])
    }
    fn u16(&mut self) -> Result<u16, DecodeError> {
        Ok(u16::from_be_bytes(self.fixed()?))
    }
    fn u64(&mut self) -> Result<u64, DecodeError> {
        Ok(u64::from_be_bytes(self.fixed()?))
    }
    fn bytes(&mut self) -> Result<&'a [u8], DecodeError> {
        let n = usize::try_from(self.u64()?).map_err(|_| DecodeError::Length)?;
        self.take(n)
    }
    fn string(&mut self) -> Result<(), DecodeError> {
        let value = self.bytes()?;
        self.charge(value.len())?;
        std::str::from_utf8(value).map_err(|_| DecodeError::Utf8)?;
        Ok(())
    }
    fn flag(&mut self) -> Result<bool, DecodeError> {
        match self.u8()? {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(DecodeError::Flag),
        }
    }
}
#[derive(Default)]
struct Declared {
    kind: Option<u16>,
    execution: Option<u16>,
    executor: bool,
}
fn declaration(
    cursor: &mut Cursor<'_, '_>,
    scratch: &mut [Scratch],
    value_scratch: &mut [original_message::Scratch],
    platform: u8,
) -> Result<Declared, DecodeError> {
    fn push(slots: &mut [Scratch], top: &mut usize, value: Scratch) -> Result<(), DecodeError> {
        *slots.get_mut(*top).ok_or(DecodeError::Scratch)? = value;
        *top = top.checked_add(1).ok_or(DecodeError::Length)?;
        Ok(())
    }
    let mut top = 0;
    let mut observed = Declared::default();
    push(scratch, &mut top, Scratch::Task(grammar::DECLARATION))?;
    while top != 0 {
        cursor.charge(1)?;
        top -= 1;
        match std::mem::take(&mut scratch[top]) {
            Scratch::Task(id) => match *grammar::TYPES.get(id).ok_or(DecodeError::Declaration)? {
                Type::Unit => (),
                Type::String => cursor.string()?,
                Type::Bool => {
                    cursor.flag()?;
                }
                Type::I64 | Type::U64 => {
                    cursor.take(8)?;
                }
                Type::Path => {
                    if cursor.u8()? != platform {
                        return Err(DecodeError::Platform);
                    }
                    let count = usize::try_from(cursor.u64()?).map_err(|_| DecodeError::Length)?;
                    cursor.take(
                        count
                            .checked_mul(if platform == 1 { 1 } else { 2 })
                            .ok_or(DecodeError::Length)?,
                    )?;
                }
                Type::EventKind => {
                    let value = cursor.u16()?;
                    if !(1..=15).contains(&value) || observed.kind.replace(value).is_some() {
                        return Err(DecodeError::Declaration);
                    }
                }
                Type::ValueObject => {
                    let bytes = cursor.bytes()?;
                    if !bytes.starts_with(&6u16.to_be_bytes()) {
                        return Err(DecodeError::Value);
                    }
                    original_message::value_charged(bytes, platform, value_scratch, cursor.work)
                        .map_err(|error| original_error(error, DecodeError::Value))?;
                }
                Type::Record(fields) => {
                    cursor.charge(fields.len())?;
                    for field in fields.iter().rev() {
                        push(scratch, &mut top, Scratch::Task(*field))?;
                    }
                }
                Type::Optional(item) => {
                    if cursor.flag()? {
                        push(scratch, &mut top, Scratch::Task(item))?;
                    }
                }
                Type::Union(cases) => {
                    let tag = cursor.u16()?;
                    cursor.charge(cases.len())?;
                    let case = cases
                        .iter()
                        .find(|case| case.tag == tag)
                        .ok_or(DecodeError::Tag)?;
                    if id == grammar::DECLARATION {
                        observed.execution = Some(tag);
                    }
                    if id == grammar::SOURCE_PATH {
                        observed.executor = tag == 2;
                    }
                    push(scratch, &mut top, Scratch::Task(case.item))?;
                }
                Type::List(item) => {
                    let count = usize::try_from(cursor.u64()?).map_err(|_| DecodeError::Length)?;
                    if count.checked_mul(8).ok_or(DecodeError::Length)?
                        > cursor.input.len() - cursor.at
                    {
                        return Err(DecodeError::Length);
                    }
                    cursor.charge(count)?;
                    push(
                        scratch,
                        &mut top,
                        Scratch::Repeat {
                            item,
                            remaining: count,
                        },
                    )?;
                }
            },
            Scratch::Repeat { item, remaining } if remaining != 0 => {
                push(
                    scratch,
                    &mut top,
                    Scratch::Repeat {
                        item,
                        remaining: remaining - 1,
                    },
                )?;
                push(scratch, &mut top, Scratch::Task(item))?;
            }
            Scratch::Repeat { .. } => (),
            Scratch::Empty => return Err(DecodeError::Scratch),
        }
    }
    Ok(observed)
}
fn selection(
    input: &[u8],
    original: &original_message::UntrustedInvocation<'_>,
    expected: original_message::ExpectedDataProfile,
    scratch: &mut [Scratch],
    value_scratch: &mut [original_message::Scratch],
    work: &mut u64,
) -> Result<([u8; 32], u64), DecodeError> {
    let mut cursor = Cursor { input, at: 0, work };
    if cursor.u16()? != grammar::VERSION {
        return Err(DecodeError::Version);
    }
    if cursor.u8()? != expected.platform {
        return Err(DecodeError::Platform);
    }
    if cursor.fixed::<32>()? != expected.launch
        || &cursor.fixed::<32>()? != original.occurrence()
        || &cursor.fixed::<32>()? != original.context().actor().raw_bytes()
    {
        return Err(DecodeError::Context);
    }
    let configuration = cursor.fixed::<32>()?;
    if configuration[..16] != expected.launch[..16] || configuration[24..] != [0; 8] {
        return Err(DecodeError::Context);
    }
    let serial = u64::from_be_bytes(
        configuration[16..24]
            .try_into()
            .map_err(|_| DecodeError::Context)?,
    );
    if serial <= original.context().actor().serial().get()
        || configuration == *original.occurrence()
    {
        return Err(DecodeError::Context);
    }
    let source_count = cursor.u64()?;
    let count = cursor.u64()?;
    let count_usize = usize::try_from(count).map_err(|_| DecodeError::Length)?;
    if count_usize.checked_mul(28).ok_or(DecodeError::Length)? > cursor.input.len() - cursor.at {
        return Err(DecodeError::Length);
    }
    cursor.charge(count_usize)?;
    let mut previous_index = None;
    for ordinal in 0..count {
        if cursor.u64()? != ordinal {
            return Err(DecodeError::Index);
        }
        let source_index = cursor.u64()?;
        if source_index >= source_count
            || previous_index.is_some_and(|previous| source_index <= previous)
        {
            return Err(DecodeError::Index);
        }
        previous_index = Some(source_index);
        let phase = cursor.u16()?;
        if !matches!(phase, grammar::REGULAR | grammar::MEMORY_CLEANUP) {
            return Err(DecodeError::Phase);
        }
        let declared = declaration(&mut cursor, scratch, value_scratch, expected.platform)?;
        let legacy = original.body_tag() == 12;
        match declared.execution {
            Some(1 | 2) if !legacy && declared.kind == Some(original.kind()) => (),
            Some(3 | 4) if legacy && declared.kind.is_none() => (),
            _ => return Err(DecodeError::Declaration),
        }
        if phase == grammar::MEMORY_CLEANUP
            && !(original.memory_consolidation() && declared.executor)
        {
            return Err(DecodeError::Phase);
        }
    }
    if cursor.at != input.len() {
        return Err(DecodeError::Trailing);
    }
    Ok((configuration, count))
}
/// Atomic framing and structural checks only. No privileged ledger is read or changed.
pub(super) fn decode<'a>(
    input: &'a [u8],
    expected: original_message::ExpectedDataProfile,
    scratch: &mut [Scratch],
    value_scratch: &mut [original_message::Scratch],
    work: u64,
) -> Result<UntrustedInvocationObservation<'a>, DecodeError> {
    let mut remaining = work;
    let mut cursor = Cursor {
        input,
        at: 0,
        work: &mut remaining,
    };
    if cursor.u16()? != grammar::INVOCATION_VERSION {
        return Err(DecodeError::Version);
    }
    let original_bytes = cursor.bytes()?;
    let selection_bytes = cursor.bytes()?;
    if cursor.at != input.len() {
        return Err(DecodeError::Trailing);
    }
    let original =
        original_message::decode_charged(original_bytes, expected, value_scratch, cursor.work)
            .map_err(|error| original_error(error, DecodeError::Context))?;
    let (configuration, entries) = selection(
        selection_bytes,
        &original,
        expected,
        scratch,
        value_scratch,
        cursor.work,
    )?;
    Ok(UntrustedInvocationObservation {
        original,
        selection: selection_bytes,
        configuration,
        entries,
    })
}
#[cfg(test)]
mod tests;
