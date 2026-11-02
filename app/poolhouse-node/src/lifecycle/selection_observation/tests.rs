//! Mechanical source declarations only. No models, authenticated source or effect issuer.
use super::super::original_message::tests as original_fixture;
use super::*;
fn tag(out: &mut Vec<u8>, value: u16) {
    out.extend_from_slice(&value.to_be_bytes());
}
fn count(out: &mut Vec<u8>, value: u64) {
    out.extend_from_slice(&value.to_be_bytes());
}
fn bytes(out: &mut Vec<u8>, value: &[u8]) {
    count(out, value.len() as u64);
    out.extend_from_slice(value);
}
fn text(out: &mut Vec<u8>, value: &str) {
    bytes(out, value.as_bytes());
}
fn path(out: &mut Vec<u8>) {
    out.push(1);
    bytes(out, b"/tmp/declared\xff");
}
fn id(serial: u64) -> [u8; 32] {
    let mut value = original_fixture::expected().launch;
    value[16..24].copy_from_slice(&serial.to_be_bytes());
    value
}
fn declaration(execution: u16, kind: u16, executor: bool) -> Vec<u8> {
    let mut out = vec![];
    tag(&mut out, execution);
    if execution <= 2 {
        out.push(0);
        out.push(0);
        tag(&mut out, kind);
        out.push(0);
        count(&mut out, 5);
        out.push(0);
        count(&mut out, 4000);
        if executor {
            tag(&mut out, 2);
            text(&mut out, "plugin-label");
            text(&mut out, "environment");
            out.push(0);
            out.push(0);
            text(&mut out, "file:///plugin/manifest");
            text(&mut out, "hooks[0]");
        } else {
            tag(&mut out, 1);
            path(&mut out);
        }
        tag(&mut out, 1);
        out.extend_from_slice(&0i64.to_be_bytes());
    }
    match execution {
        1 => {
            text(&mut out, "configured command declaration");
            out.push(0);
            tag(&mut out, 1);
        }
        2 => {
            text(&mut out, "server-label");
            text(&mut out, "tool-label");
            let mut object = 6u16.to_be_bytes().to_vec();
            count(&mut object, 0);
            bytes(&mut out, &object);
        }
        3 => {
            text(&mut out, "notify-label");
            count(&mut out, 1);
            text(&mut out, "configured-argv");
            tag(&mut out, 1);
        }
        4 => text(&mut out, "opaque-callback-label"),
        _ => (),
    }
    out
}
fn selection(declarations: &[(u16, u16, bool)], snapshot: u64) -> Vec<u8> {
    let mut out = vec![];
    tag(&mut out, 1);
    out.push(1);
    out.extend_from_slice(&original_fixture::expected().launch);
    out.extend_from_slice(&id(9));
    out.extend_from_slice(&id(1));
    out.extend_from_slice(&id(snapshot));
    count(&mut out, declarations.len() as u64);
    count(&mut out, declarations.len() as u64);
    for (ordinal, (execution, kind, cleanup)) in declarations.iter().enumerate() {
        count(&mut out, ordinal as u64);
        count(&mut out, ordinal as u64);
        tag(&mut out, if *cleanup { 2 } else { 1 });
        out.extend_from_slice(&declaration(*execution, *kind, *cleanup));
    }
    out
}
fn invocation(original: &[u8], selection: &[u8]) -> Vec<u8> {
    let mut out = vec![];
    tag(&mut out, 2);
    bytes(&mut out, original);
    bytes(&mut out, selection);
    out
}
fn original(which: u16, target: u16, kind: u16) -> Vec<u8> {
    original_fixture::message(&original_fixture::request(which, target, false), kind)
}
fn check(input: &[u8]) -> Result<UntrustedInvocationObservation<'_>, DecodeError> {
    decode(
        input,
        original_fixture::expected(),
        &mut [Scratch::Empty; 128],
        &mut [original_message::Scratch::Empty; 256],
        1_000_000,
    )
}
#[test]
fn closed_declarations_are_unmeasured_and_original_v1_bytes_stay_exact() {
    for (execution, which, kind) in [(1, 1, 1), (2, 1, 1), (3, 12, 15), (4, 12, 15)] {
        let original = original(which, 1, kind);
        let selected = selection(&[(execution, kind, false)], 10);
        let framed = invocation(&original, &selected);
        let value = check(&framed).unwrap();
        assert_eq!(value.original().original_bytes(), original);
        assert_eq!(value.selection_bytes(), selected);
        assert_eq!(value.configuration(), &id(10));
        assert_eq!(value.entries(), 1);
    }
    let empty = selection(&[], 2);
    assert_eq!(
        check(&invocation(&original(1, 1, 1), &empty))
            .unwrap()
            .entries(),
        0
    );
}
#[test]
fn same_snapshot_may_select_different_entries_for_different_originals() {
    let first = check(&invocation(
        &original(1, 1, 1),
        &selection(&[(1, 1, false)], 10),
    ))
    .is_ok();
    let second = check(&invocation(
        &original(7, 1, 7),
        &selection(&[(2, 7, false), (1, 7, false)], 10),
    ))
    .is_ok();
    assert!(first && second);
}
#[test]
fn source_context_ordinal_index_and_phase_mismatches_refuse() {
    let original = original(1, 1, 1);
    let selected = selection(&[(1, 1, false)], 10);
    for position in [3, 35, 67, 99] {
        let mut wrong = selected.clone();
        wrong[position] ^= 1;
        assert!(check(&invocation(&original, &wrong)).is_err());
    }
    for (start, value) in [(147, 1u64), (155, 1u64)] {
        let mut wrong = selected.clone();
        wrong[start..start + 8].copy_from_slice(&value.to_be_bytes());
        assert!(check(&invocation(&original, &wrong)).is_err());
    }
    assert!(check(&invocation(&original, &selection(&[(1, 1, true)], 10))).is_err());
    assert!(check(&invocation(&original, &selection(&[(1, 1, false)], 9))).is_err());
    assert!(check(&invocation(&original, &selection(&[(1, 1, false)], 1))).is_err());
}
#[test]
fn memory_cleanup_needs_actual_stop_target_and_executor_declaration() {
    assert!(check(&invocation(
        &original(10, 2, 11),
        &selection(&[(2, 11, true)], 10)
    ))
    .is_ok());
    assert!(check(&invocation(
        &original(10, 1, 11),
        &selection(&[(2, 11, true)], 10)
    ))
    .is_err());
    let mut selected = selection(&[(2, 11, false)], 10);
    selected[163..165].copy_from_slice(&2u16.to_be_bytes());
    assert!(check(&invocation(&original(10, 2, 11), &selected)).is_err());
}
#[test]
fn no_measured_execution_or_mixed_callback_promotion_is_accepted() {
    let original = original(1, 1, 1);
    for execution in [0, 5, 99, u16::MAX] {
        assert!(check(&invocation(
            &original,
            &selection(&[(execution, 1, false)], 10)
        ))
        .is_err());
    }
    assert!(check(&invocation(&original, &selection(&[(4, 1, false)], 10))).is_err());
    assert!(check(&invocation(
        &self::original(12, 1, 15),
        &selection(&[(1, 15, false)], 10)
    ))
    .is_err());
}
#[test]
fn every_truncation_nested_trailing_byte_and_unknown_version_refuse() {
    let original = original(1, 1, 1);
    let selected = selection(&[(2, 1, false)], 10);
    let input = invocation(&original, &selected);
    for end in 0..input.len() {
        assert!(check(&input[..end]).is_err(), "end {end}");
    }
    for root in [true, false] {
        let mut bad = if root {
            input.clone()
        } else {
            selected.clone()
        };
        bad.push(0);
        assert!(check(&if root {
            bad
        } else {
            invocation(&original, &bad)
        })
        .is_err());
    }
    let mut bad_original = original;
    bad_original.push(0);
    assert!(check(&invocation(&bad_original, &selected)).is_err());
    let mut bad = input;
    bad[..2].copy_from_slice(&1u16.to_be_bytes());
    assert!(matches!(check(&bad), Err(DecodeError::Version)));
}
#[test]
fn overflow_scratch_and_one_shared_work_budget_refuse_without_state_access() {
    let original = original(1, 1, 1);
    let selected = selection(&[(2, 1, false)], 10);
    let input = invocation(&original, &selected);
    let mut overflow = selected.clone();
    overflow[139..147].copy_from_slice(&u64::MAX.to_be_bytes());
    assert!(check(&invocation(&original, &overflow)).is_err());
    assert!(decode(
        &input,
        original_fixture::expected(),
        &mut [],
        &mut [],
        1_000_000
    )
    .is_err());
    let mut remaining = 1_000_000;
    let mut cursor = Cursor {
        input: &input,
        at: 0,
        work: &mut remaining,
    };
    assert_eq!(cursor.u16().unwrap(), 2);
    let original_bytes = cursor.bytes().unwrap();
    let selection_bytes = cursor.bytes().unwrap();
    let mut values = [original_message::Scratch::Empty; 256];
    let decoded = original_message::decode_charged(
        original_bytes,
        original_fixture::expected(),
        &mut values,
        cursor.work,
    )
    .unwrap();
    super::selection(
        selection_bytes,
        &decoded,
        original_fixture::expected(),
        &mut [Scratch::Empty; 128],
        &mut values,
        cursor.work,
    )
    .unwrap();
    let spent = 1_000_000 - remaining;
    assert!(decode(
        &input,
        original_fixture::expected(),
        &mut [Scratch::Empty; 128],
        &mut values,
        spent
    )
    .is_ok());
    assert!(decode(
        &input,
        original_fixture::expected(),
        &mut [Scratch::Empty; 128],
        &mut values,
        spent - 1
    )
    .is_err());
    for work in [0, 1, 128] {
        assert!(decode(
            &input,
            original_fixture::expected(),
            &mut [Scratch::Empty; 128],
            &mut values,
            work
        )
        .is_err());
    }
}

#[test]
fn source_order_is_strict_while_phase_order_follows_configuration() {
    let original = original(1, 1, 1);
    let selected = selection(&[(1, 1, false), (1, 1, false)], 10);
    assert!(check(&invocation(&original, &selected)).is_ok());
    // The same declaration at distinct source indices remains distinct.
    let mut repeated = selected.clone();
    let second = 147 + 18 + declaration(1, 1, false).len();
    repeated[second + 8..second + 16].copy_from_slice(&0u64.to_be_bytes());
    assert!(check(&invocation(&original, &repeated)).is_err());
    let mut decreasing = selected;
    decreasing[155..163].copy_from_slice(&1u64.to_be_bytes());
    decreasing[second + 8..second + 16].copy_from_slice(&0u64.to_be_bytes());
    assert!(check(&invocation(&original, &decreasing)).is_err());
    // Catalog order describes configuration; execution later separates the phases.
    let cleanup_first = selection(&[(2, 11, true), (1, 11, false)], 10);
    assert!(check(&invocation(&self::original(10, 2, 11), &cleanup_first)).is_ok());
    let interleaved = selection(&[(1, 11, false), (2, 11, true), (1, 11, false)], 10);
    assert!(check(&invocation(&self::original(10, 2, 11), &interleaved)).is_ok());
}

// Phase assignment never groups or changes the observed source configuration order.
