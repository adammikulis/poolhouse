//! Lossless original-message DATA decoder. No exporter, authenticated producer,
//! protected resource reservation, admission, dispatch right or execution issuer.
//! Expected compatibility and scratch/work inputs are data, never provenance.
use super::source_context;
use chrono::{DateTime, Utc};

#[derive(Clone, Copy)]
struct Case { tag: u16, item: usize, kind: u16 }
#[derive(Clone, Copy)]
enum Type {
    Unit, String, Uuid, Bool, I64, Path, Timestamp, Number,
    Record(&'static [usize]), Union(&'static [Case]), Optional(usize), List(usize), Map(usize),
}
#[derive(Clone, Copy)]
enum EnvelopeField { Version, SourceFeature, Platform, Launch, Occurrence, Instance, CapturedKind, ActorContext, OriginalRequest }
#[path = "../contract_native_message.rs"]
mod grammar;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum DecodeError { Truncated, Trailing, Version, Profile, Platform, Tag, Flag, Utf8, Number, Timestamp, Length, Scratch, Work, Context, Kind, Grammar }
#[derive(Clone, Copy)]
pub(super) struct ExpectedDataProfile { pub(super) launch: [u8; 32], pub(super) source_feature: u16, pub(super) platform: u8 }
/// Workspace is caller-owned memory; no allocation or authority is hidden here.
#[derive(Clone, Copy, Default)]
pub(super) enum Scratch {
    #[default] Empty,
    Task(usize), Repeat { item: usize, remaining: usize },
    Object { item: usize, remaining: usize, base_keys: usize },
    Key { start: usize, end: usize },
}
/// Borrows the COMPLETE input, including private source fields; Debug/serde are
/// deliberately absent. A future executor must still apply its finite disclosure policy.
/// No scratch references escape, so scratch can safely be reused after decoding.
pub(super) struct UntrustedInvocation<'a> {
    original: &'a [u8], body: &'a [u8], context: source_context::UntrustedActorContext<'a>,
    occurrence: [u8; 32], body_tag: u16, kind: u16, memory_consolidation: bool,
}
impl<'a> UntrustedInvocation<'a> {
    pub(super) fn original_bytes(&self) -> &'a [u8] { self.original }
    pub(super) fn body_bytes(&self) -> &'a [u8] { self.body }
    pub(super) fn context(&self) -> source_context::UntrustedActorContext<'a> { self.context }
    pub(super) fn occurrence(&self) -> &[u8; 32] { &self.occurrence }
    pub(super) fn body_tag(&self) -> u16 { self.body_tag }
    pub(super) fn kind(&self) -> u16 { self.kind }
    pub(super) fn memory_consolidation(&self) -> bool {self.memory_consolidation}
}
struct Cursor<'a> { input: &'a [u8], offset: usize, work: u64 }
impl<'a> Cursor<'a> {
    fn charge(&mut self, amount: usize) -> Result<(), DecodeError> {
        let amount = u64::try_from(amount).map_err(|_| DecodeError::Work)?;
        self.work = self.work.checked_sub(amount).ok_or(DecodeError::Work)?; Ok(())
    }
    fn take(&mut self, count: usize) -> Result<&'a [u8], DecodeError> {
        self.charge(count)?;
        let end = self.offset.checked_add(count).ok_or(DecodeError::Length)?;
        let value = self.input.get(self.offset..end).ok_or(DecodeError::Truncated)?;
        self.offset = end; Ok(value)
    }
    fn fixed<const N: usize>(&mut self) -> Result<[u8; N], DecodeError> { self.take(N)?.try_into().map_err(|_| DecodeError::Truncated) }
    fn u8(&mut self) -> Result<u8, DecodeError> { Ok(self.fixed::<1>()?[0]) }
    fn u16(&mut self) -> Result<u16, DecodeError> { Ok(u16::from_be_bytes(self.fixed()?)) }
    fn count(&mut self) -> Result<usize, DecodeError> { usize::try_from(u64::from_be_bytes(self.fixed()?)).map_err(|_| DecodeError::Length) }
    fn bytes(&mut self) -> Result<&'a [u8], DecodeError> { let n = self.count()?; self.take(n) }
    fn string(&mut self) -> Result<(usize, usize), DecodeError> {
        let bytes = self.bytes()?; self.charge(bytes.len())?;
        std::str::from_utf8(bytes).map_err(|_| DecodeError::Utf8)?;
        Ok((self.offset - bytes.len(), self.offset))
    }
    fn flag(&mut self) -> Result<bool, DecodeError> { match self.u8()? { 0 => Ok(false), 1 => Ok(true), _ => Err(DecodeError::Flag) } }
    fn repetition(&mut self, minimum: usize) -> Result<usize, DecodeError> {
        if minimum == 0 { return Err(DecodeError::Grammar); }
        let count = self.count()?;
        let bytes = count.checked_mul(minimum).ok_or(DecodeError::Length)?;
        if bytes > self.input.len() - self.offset { return Err(DecodeError::Length); }
        self.charge(count)?; Ok(count)
    }
}
struct Workspace<'a> { slots: &'a mut [Scratch], frames: usize, keys: usize }
impl Workspace<'_> {
    fn push(&mut self, value: Scratch) -> Result<(), DecodeError> {
        if self.frames >= self.slots.len() - self.keys { return Err(DecodeError::Scratch); }
        self.slots[self.frames] = value; self.frames += 1; Ok(())
    }
    fn pop(&mut self) -> Option<Scratch> {
        self.frames = self.frames.checked_sub(1)?;
        Some(std::mem::take(&mut self.slots[self.frames]))
    }
    fn key(&mut self, cursor: &mut Cursor<'_>, start: usize, end: usize, base: usize) -> Result<(), DecodeError> {
        for i in base..self.keys {
            let slot = self.slots.len() - 1 - i;
            if let Scratch::Key { start: old_start, end: old_end } = self.slots[slot] {
                cursor.charge((end - start).min(old_end - old_start).checked_add(1).ok_or(DecodeError::Work)?)?;
                if cursor.input[start..end] == cursor.input[old_start..old_end] { return Err(DecodeError::Grammar); }
            } else { return Err(DecodeError::Grammar); }
        }
        if self.frames >= self.slots.len() - self.keys { return Err(DecodeError::Scratch); }
        self.keys += 1;
        self.slots[self.slots.len() - self.keys] = Scratch::Key { start, end }; Ok(())
    }
}

fn data(cursor: &mut Cursor<'_>, scratch: &mut [Scratch], platform: u8, root:usize) -> Result<(Option<u16>, Option<u16>, bool), DecodeError> {
    let mut workspace = Workspace { slots: scratch, frames: 0, keys: 0 };
    workspace.push(Scratch::Task(root))?;
    let (mut body_tag, mut kind, mut memory_consolidation) = (None, None, false);
    while let Some(frame) = workspace.pop() {
        cursor.charge(1)?;
        match frame {
            Scratch::Task(id) => match *grammar::TYPES.get(id).ok_or(DecodeError::Grammar)? {
                Type::Unit => (), Type::Uuid => { cursor.take(16)?; }, Type::I64 => { cursor.take(8)?; },
                Type::String => { cursor.string()?; }, Type::Bool => { cursor.flag()?; },
                Type::Path => {
                    if cursor.u8()? != platform { return Err(DecodeError::Platform); }
                    let units = cursor.count()?;
                    let width = if platform == 1 { 1 } else { 2 };
                    cursor.take(units.checked_mul(width).ok_or(DecodeError::Length)?)?;
                },
                Type::Timestamp => {
                    let seconds = i64::from_be_bytes(cursor.fixed()?);
                    let nanos = u32::from_be_bytes(cursor.fixed()?);
                    let value = DateTime::<Utc>::from_timestamp(seconds, nanos).ok_or(DecodeError::Timestamp)?;
                    if (value.timestamp(), value.timestamp_subsec_nanos()) != (seconds, nanos) { return Err(DecodeError::Timestamp); }
                },
                Type::Number => { let bytes = cursor.bytes()?; number(bytes, &mut cursor.work)?; },
                Type::Optional(item) => { if cursor.flag()? { workspace.push(Scratch::Task(item))?; } },
                Type::Record(fields) => {
                    cursor.charge(fields.len())?;
                    for item in fields.iter().rev() { workspace.push(Scratch::Task(*item))?; }
                },
                Type::Union(cases) => {
                    let tag = cursor.u16()?; cursor.charge(cases.len())?;
                    if id==grammar::STOP_TARGET && tag==2 {memory_consolidation=true;}
                    let case = cases.iter().find(|case| case.tag == tag).ok_or(DecodeError::Tag)?;
                    if id == grammar::ORIGINAL_REQUEST && body_tag.replace(tag).is_some() { return Err(DecodeError::Grammar); }
                    if case.kind != 0 && kind.replace(case.kind).is_some() { return Err(DecodeError::Kind); }
                    workspace.push(Scratch::Task(case.item))?;
                },
                Type::List(item) => {
                    let remaining = cursor.repetition(*grammar::MINIMUM.get(item).ok_or(DecodeError::Grammar)?)?;
                    workspace.push(Scratch::Repeat { item, remaining })?;
                },
                Type::Map(item) => {
                    let minimum = grammar::MINIMUM.get(item).ok_or(DecodeError::Grammar)?.checked_add(8).ok_or(DecodeError::Length)?;
                    let remaining = cursor.repetition(minimum)?;
                    workspace.push(Scratch::Object { item, remaining, base_keys: workspace.keys })?;
                },
            },
            Scratch::Repeat { item, remaining } if remaining != 0 => {
                workspace.push(Scratch::Repeat { item, remaining: remaining - 1 })?;
                workspace.push(Scratch::Task(item))?;
            },
            Scratch::Object { item, remaining, base_keys } if remaining != 0 => {
                let (start, end) = cursor.string()?;
                workspace.key(cursor, start, end, base_keys)?;
                workspace.push(Scratch::Object { item, remaining: remaining - 1, base_keys })?;
                workspace.push(Scratch::Task(item))?;
            },
            Scratch::Object { base_keys, .. } => { workspace.keys = base_keys; },
            Scratch::Repeat { .. } => (), _ => return Err(DecodeError::Grammar),
        }
    }
    Ok((body_tag, kind, memory_consolidation))
}

fn body(cursor:&mut Cursor<'_>,scratch:&mut [Scratch],platform:u8)->Result<(u16,u16,bool),DecodeError> {
    let (body,kind,memory)=data(cursor,scratch,platform,grammar::ORIGINAL_REQUEST)?;
    Ok((body.ok_or(DecodeError::Kind)?,kind.ok_or(DecodeError::Kind)?,memory))
}
/// Shared exact Value fragment validation; the caller keeps the SAME remaining-work meter.
pub(super) fn value_charged(input:&[u8],platform:u8,scratch:&mut [Scratch],work:&mut u64)->Result<(),DecodeError> {
    let mut cursor=Cursor {input,offset:0,work:*work};
    let result=(|| {data(&mut cursor,scratch,platform,grammar::VALUE)?;if cursor.offset!=input.len() {return Err(DecodeError::Trailing);}Ok(())})();
    *work=cursor.work;result
}

/// Exact JSON Number token grammar. No floating conversion, token normalization,
/// serde allocations, or hidden recursion; every inspected byte spends work.
fn number(bytes: &[u8], work: &mut u64) -> Result<(), DecodeError> {
    struct Token<'a> { bytes: &'a [u8], at: usize, work: &'a mut u64 }
    impl Token<'_> {
        fn peek(&mut self) -> Result<Option<u8>, DecodeError> { *self.work = self.work.checked_sub(1).ok_or(DecodeError::Work)?; Ok(self.bytes.get(self.at).copied()) }
        fn eat(&mut self, byte: u8) -> Result<bool, DecodeError> { if self.peek()? == Some(byte) { self.at += 1; Ok(true) } else { Ok(false) } }
        fn digits(&mut self) -> Result<(), DecodeError> {
            let before = self.at;
            while self.peek()?.is_some_and(|byte| byte.is_ascii_digit()) { self.at += 1; }
            if self.at == before { Err(DecodeError::Number) } else { Ok(()) }
        }
    }
    let mut value = Token { bytes, at: 0, work };
    value.eat(b'-')?;
    if value.eat(b'0')? {
        if value.peek()?.is_some_and(|byte| byte.is_ascii_digit()) { return Err(DecodeError::Number); }
    } else { value.digits()?; }
    if value.eat(b'.')? { value.digits()?; }
    if value.eat(b'e')? || value.eat(b'E')? { if !value.eat(b'+')? { value.eat(b'-')?; } value.digits()?; }
    if value.at != bytes.len() { return Err(DecodeError::Number); } Ok(())
}

/// Structural/data compatibility ONLY. Calling this cannot authenticate a native
/// occurrence or admit an effect, even with matching expected values and hashes.
pub(super) fn decode_charged<'a>(input: &'a [u8], expected: ExpectedDataProfile, scratch: &mut [Scratch], work: &mut u64) -> Result<UntrustedInvocation<'a>, DecodeError> {
    let mut cursor = Cursor { input, offset: 0, work:*work };
    let result=(|| {
    if expected.source_feature != grammar::SOURCE_FEATURE || !matches!(expected.platform, 1 | 2) { return Err(DecodeError::Profile); }
    let (mut occurrence, mut instance, mut captured_kind, mut context_bytes, mut body_start, mut parsed) = (None, None, None, None, None, None);
    for field in grammar::ENVELOPE {
        match field {
            EnvelopeField::Version => if cursor.u16()? != grammar::VERSION { return Err(DecodeError::Version); },
            EnvelopeField::SourceFeature => if cursor.u16()? != expected.source_feature { return Err(DecodeError::Profile); },
            EnvelopeField::Platform => if cursor.u8()? != expected.platform { return Err(DecodeError::Platform); },
            EnvelopeField::Launch => if cursor.fixed::<32>()? != expected.launch { return Err(DecodeError::Context); },
            EnvelopeField::Occurrence => occurrence = Some(cursor.fixed::<32>()?),
            EnvelopeField::Instance => instance = Some(cursor.fixed::<32>()?),
            EnvelopeField::CapturedKind => captured_kind = Some(cursor.u16()?),
            EnvelopeField::ActorContext => context_bytes = Some(cursor.bytes()?),
            EnvelopeField::OriginalRequest => { body_start = Some(cursor.offset); parsed = Some(body(&mut cursor, scratch, expected.platform)?); },
        }
    }
    if cursor.offset != input.len() { return Err(DecodeError::Trailing); }
    let context_bytes = context_bytes.ok_or(DecodeError::Grammar)?;
    cursor.charge(context_bytes.len().checked_mul(4).ok_or(DecodeError::Work)?)?;
    let context = source_context::decode(context_bytes, &expected.launch).map_err(|_| DecodeError::Context)?;
    let occurrence = occurrence.ok_or(DecodeError::Grammar)?;
    if instance.as_ref() != Some(context.actor().raw_bytes()) || occurrence[..16] != expected.launch[..16] || occurrence[24..] != [0; 8] { return Err(DecodeError::Context); }
    let serial = u64::from_be_bytes(occurrence[16..24].try_into().map_err(|_| DecodeError::Context)?);
    if serial <= context.actor().serial().get() { return Err(DecodeError::Context); }
    let (body_tag, kind, memory_consolidation) = parsed.ok_or(DecodeError::Grammar)?;
    if captured_kind != Some(kind) { return Err(DecodeError::Kind); }
    Ok(UntrustedInvocation { original: input, body: &input[body_start.ok_or(DecodeError::Grammar)?..], context, occurrence, body_tag, kind, memory_consolidation })
    })();
    *work=cursor.work;result
}
pub(super) fn decode<'a>(input:&'a [u8],expected:ExpectedDataProfile,scratch:&mut [Scratch],work:u64)->Result<UntrustedInvocation<'a>,DecodeError> {decode_charged(input,expected,scratch,&mut {work})}

#[cfg(test)]
#[path = "original_message_tests.rs"]
pub(super) mod tests;
