//! Mechanical DATA fixtures only: no model behavior, native issuer or authenticated source.
use super::*;
fn text(out: &mut Vec<u8>, bytes: &[u8]) { out.extend_from_slice(&(bytes.len() as u64).to_be_bytes()); out.extend_from_slice(bytes); }
fn tag(out: &mut Vec<u8>, value: u16) { out.extend_from_slice(&value.to_be_bytes()); }
fn path(out: &mut Vec<u8>) { out.push(1); text(out, b"relative-or-absolute/\xff\0"); }
fn transcript(out: &mut Vec<u8>) { out.push(1); path(out); }
fn common(out: &mut Vec<u8>) {
    out.extend_from_slice(&[99; 16]); text(out, b"turn\0\n"); out.push(0); path(out); transcript(out); text(out, b"model-label");
}
fn end(out: &mut Vec<u8>) { out.extend_from_slice(&[99; 16]); text(out, b"turn"); path(out); transcript(out); }
fn value_map(out: &mut Vec<u8>, duplicate: bool) {
    out.extend_from_slice(&2u64.to_be_bytes());
    text(out, b"z"); tag(out, 4); text(out, b"-123456789012345678901234567890.000e+003");
    text(out, if duplicate { b"z" } else { b"a" }); tag(out, 3); text(out, b"control\0\r\n");
}
fn tool(out: &mut Vec<u8>, duplicate: bool) {
    text(out, b"permission"); text(out, b"tool"); out.extend_from_slice(&2u64.to_be_bytes());
    text(out, b"same-alias"); text(out, b"same-alias"); text(out, b"tool-id-or-run-suffix");
    tag(out, 6); value_map(out, duplicate);
}
fn request(which: u16, target: u16, duplicate: bool) -> Vec<u8> {
    let mut out = Vec::new(); tag(&mut out, which);
    match which {
        1..=3 => { common(&mut out); tool(&mut out, duplicate); if which == 3 { tag(&mut out, 5); out.extend_from_slice(&2u64.to_be_bytes()); tag(&mut out, 1); tag(&mut out, 2); out.push(1); } },
        4 | 5 => { common(&mut out); text(&mut out, b"trigger"); },
        6 => { common(&mut out); text(&mut out, b"permission"); text(&mut out, b"complete prompt\0\n"); },
        7 => end(&mut out),
        8 => { end(&mut out); text(&mut out, b"model"); text(&mut out, b"permission"); out.push(1); value_map(&mut out, false); },
        9 => {
            out.push(1); text(&mut out, b"retained-internal-turn"); out.extend_from_slice(&[99; 16]); path(&mut out); transcript(&mut out);
            text(&mut out, b"model"); text(&mut out, b"permission"); tag(&mut out, target);
            if target == 1 { tag(&mut out, 2); } // Historical SessionStart Resume is NOT native Resume.
            else { for value in [b"turn".as_slice(), b"agent", b"type"] { text(&mut out, value); } }
        },
        10 => {
            end(&mut out); text(&mut out, b"model"); text(&mut out, b"permission"); out.push(1); value_map(&mut out, false);
            out.push(1); out.push(1); text(&mut out, b"last assistant"); tag(&mut out, target);
            if target == 3 { text(&mut out, b"agent"); text(&mut out, b"type"); transcript(&mut out); }
        },
        11 => {
            text(&mut out, b"not-a-uuid-model-session"); text(&mut out, b"turn"); path(&mut out); transcript(&mut out); text(&mut out, b"model");
            out.push(1); text(&mut out, b"independent-agent-id"); out.push(0); tag(&mut out, target);
            if target == 2 {
                tag(&mut out, 3); out.push(1); text(&mut out, b"source-error"); out.push(1);
                for number in [-1i64, 2, 3, 4, 5, 6] { out.extend_from_slice(&number.to_be_bytes()); }
                out.push(1); text(&mut out, b"123456789012345678901234567890e+003"); // serde-skipped budget number.
            }
        },
        12 => {
            out.extend_from_slice(&[99; 16]); path(&mut out); out.push(1); text(&mut out, b"client");
            out.extend_from_slice(&59i64.to_be_bytes()); out.extend_from_slice(&1_234_567_890u32.to_be_bytes());
            tag(&mut out, 1); out.extend_from_slice(&[98; 16]); text(&mut out, b"turn"); out.extend_from_slice(&2u64.to_be_bytes());
            text(&mut out, b"first input"); text(&mut out, b"second input"); out.push(1); text(&mut out, b"last assistant");
        },
        _ => (),
    }
    out
}
fn expected() -> ExpectedDataProfile { let mut launch = [0; 32]; launch[..16].fill(0x14); launch[6] = 0x44; launch[8] = 0x84; ExpectedDataProfile { launch, source_feature: 1, platform: 1 } }
fn occurrence(serial: u64) -> [u8; 32] { let mut out = expected().launch; out[16..24].copy_from_slice(&serial.to_be_bytes()); out }
fn message(body: &[u8], kind: u16) -> Vec<u8> {
    let mut context = b"CDXCTX01".to_vec(); context.extend_from_slice(&occurrence(1)); context.extend_from_slice(&occurrence(1));
    context.push(0); context.extend_from_slice(&[71; 16]); context.extend_from_slice(&[72; 16]); context.push(0);
    let mut out = Vec::new(); tag(&mut out, 1); tag(&mut out, 1); out.push(1);
    out.extend_from_slice(&expected().launch); out.extend_from_slice(&occurrence(9)); out.extend_from_slice(&occurrence(1));
    tag(&mut out, kind); text(&mut out, &context); out.extend_from_slice(body); out
}
fn check(bytes: &[u8]) -> Result<UntrustedInvocation<'_>, DecodeError> { decode(bytes, expected(), &mut [Scratch::Empty; 256], 1_000_000) }

#[test]
fn complete_twelve_variants_derive_all_fifteen_source_kinds_and_preserve_every_byte() {
    let cases = [(1,1,1),(2,1,2),(3,1,3),(4,1,4),(5,1,5),(6,1,6),(7,1,7),(8,1,8),(9,1,9),(9,2,10),(10,1,11),(10,2,11),(10,3,12),(11,1,13),(11,2,14),(12,1,15)];
    let mut kinds = std::collections::HashSet::new();
    for (variant, target, kind) in cases {
        let body = request(variant, target, false); let bytes = message(&body, kind); let view = check(&bytes).unwrap();
        assert_eq!(view.original_bytes(), bytes); assert_eq!(view.body_bytes(), body); assert_eq!(view.kind(), kind); assert_eq!(view.body_tag(), variant);
        assert_eq!(view.occurrence(), &occurrence(9)); assert_eq!(view.context().actor().raw_bytes(), &occurrence(1));
        kinds.insert(kind);
        assert!(matches!(check(&message(&body, kind % 15 + 1)), Err(DecodeError::Kind)));
    }
    assert_eq!(kinds.len(), 15);
}
#[test]
fn every_truncation_trailing_byte_and_unknown_tag_refuse() {
    let bytes = message(&request(11, 2, false), 14);
    for end in 0..bytes.len() { assert!(check(&bytes[..end]).is_err(), "accepted prefix {end}"); }
    let mut trailing = bytes.clone(); trailing.push(0); assert!(matches!(check(&trailing), Err(DecodeError::Trailing)));
    assert!(matches!(check(&message(&request(u16::MAX, 1, false), 1)), Err(DecodeError::Tag)));
    let mut oversized = bytes; oversized[103..111].copy_from_slice(&u64::MAX.to_be_bytes()); assert!(check(&oversized).is_err());
}
#[test]
fn same_source_context_instance_and_later_occurrence_are_required_as_data_only() {
    let bytes = message(&request(7, 1, false), 7);
    for byte in [5,37,61,69,119] { let mut changed = bytes.clone(); changed[byte] ^= 1; assert!(check(&changed).is_err()); }
    for serial in [0u64,1] { let mut changed = bytes.clone(); changed[53..61].copy_from_slice(&serial.to_be_bytes()); assert!(matches!(check(&changed), Err(DecodeError::Context))); }
    let mut maximum = bytes; maximum[53..61].copy_from_slice(&u64::MAX.to_be_bytes()); assert!(check(&maximum).is_ok()); // No consecutive-ID rule or invented cap.
}
#[test]
fn scratch_work_profiles_and_duplicate_decoded_keys_refuse_without_partial_view() {
    let bytes = message(&request(1, 1, false), 1);
    assert!(matches!(decode(&bytes, expected(), &mut [], u64::MAX), Err(DecodeError::Scratch)));
    assert!(matches!(decode(&bytes, expected(), &mut [Scratch::Empty; 256], 0), Err(DecodeError::Work)));
    let mut profile = expected(); profile.source_feature = 99; assert!(matches!(decode(&bytes, profile, &mut [Scratch::Empty; 256], u64::MAX), Err(DecodeError::Profile)));
    profile = expected(); profile.platform = 99; assert!(matches!(decode(&bytes, profile, &mut [Scratch::Empty; 256], u64::MAX), Err(DecodeError::Profile)));
    assert!(check(&message(&request(1, 1, true), 1)).is_err());
}
#[test]
fn exact_number_tokens_preserve_large_values_and_reject_non_json_forms() {
    for value in [b"-0".as_slice(),b"1e+0003",b"123456789012345678901234567890.000",b"-0.000e-999999999999999"] { let mut work=u64::MAX; number(value,&mut work).unwrap(); }
    for value in [b"".as_slice(),b"+1",b"01",b"-",b".1",b"1.",b"1e",b"NaN",b"Infinity",b"1\0",b" 1",b"1\n"] { let mut work=u64::MAX; assert!(number(value,&mut work).is_err()); }
}
#[test]
fn deeply_nested_values_use_explicit_scratch_not_the_call_stack() {
    let mut body = Vec::new(); tag(&mut body, 1); common(&mut body);
    for value in [b"permission".as_slice(),b"tool"] { text(&mut body,value); }
    body.extend_from_slice(&0u64.to_be_bytes()); text(&mut body,b"id");
    for _ in 0..10_000 { tag(&mut body,5); body.extend_from_slice(&1u64.to_be_bytes()); }
    tag(&mut body,1);
    let bytes=message(&body,1);
    assert!(matches!(decode(&bytes,expected(),&mut[Scratch::Empty;64],u64::MAX),Err(DecodeError::Scratch)));
}

#[test]
fn raw_windows_units_are_inert_lossless_data_and_wrong_platform_refuses() {
    let mut body=Vec::new(); tag(&mut body,7); body.extend_from_slice(&[99;16]); text(&mut body,b"turn");
    body.push(2); body.extend_from_slice(&4u64.to_be_bytes());
    for unit in [0x0043u16,0x003a,0xd800,0] { body.extend_from_slice(&unit.to_be_bytes()); }
    body.push(0);
    let mut bytes=message(&body,7); bytes[4]=2;
    let mut profile=expected(); profile.platform=2;
    let view=decode(&bytes,profile,&mut[Scratch::Empty;256],1_000_000).unwrap();
    assert_eq!(view.body_bytes(),body); assert!(matches!(check(&bytes),Err(DecodeError::Platform)));
}
#[test]
fn source_strings_flags_and_timestamp_semantics_refuse_only_invalid_encoding() {
    let body=request(1,1,false);
    let mut invalid_utf8=body.clone(); invalid_utf8[26]=0xff;
    assert!(matches!(check(&message(&invalid_utf8,1)),Err(DecodeError::Utf8)));
    let mut invalid_flag=body; invalid_flag[32]=2;
    assert!(matches!(check(&message(&invalid_flag,1)),Err(DecodeError::Flag)));
    let body=request(12,1,false);
    let marker=[59i64.to_be_bytes().as_slice(),1_234_567_890u32.to_be_bytes().as_slice()].concat();
    let at=body.windows(12).position(|window|window==marker).unwrap();
    for (seconds,nanos,valid) in [(-1i64,1_000_000_000u32,true),(59,1_999_999_999,true),(0,999_999_999,true),(0,1_000_000_000,false),(59,2_000_000_000,false),(i64::MIN,0,false),(i64::MAX,0,false)] {
        let mut changed=body.clone(); changed[at..at+8].copy_from_slice(&seconds.to_be_bytes()); changed[at+8..at+12].copy_from_slice(&nanos.to_be_bytes());
        assert_eq!(check(&message(&changed,15)).is_ok(),valid);
    }
}
