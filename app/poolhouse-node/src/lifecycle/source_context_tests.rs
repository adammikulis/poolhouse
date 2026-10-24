use super::source_context::{decode, DecodeError};
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    profile: String,
    source_commit: String,
    vectors: Vec<Vector>,
}
#[derive(Deserialize)]
struct Vector {
    name: String,
    source_launch_hex: String,
    context_hex: String,
    source_uuid_hex: String,
    actor_serial: u64,
}
fn vectors() -> Vec<Vector> {
    let fixture: Fixture = serde_json::from_str(include_str!(
        "../../tests/fixtures/native-source-context-vectors.json"
    ))
    .unwrap();
    assert_eq!(fixture.profile, "CDXCTX01");
    assert_eq!(
        fixture.source_commit,
        "a50cb4ae3ed4665f18bbd6c3c2eaf8fd2eb76fd1"
    );
    assert_eq!(fixture.vectors.len(), 5);
    fixture.vectors
}
fn bytes(value: &str) -> Vec<u8> {
    assert_eq!(value.len() % 2, 0);
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|part| u8::from_str_radix(std::str::from_utf8(part).unwrap(), 16).unwrap())
        .collect()
}
fn launch(vector: &Vector) -> [u8; 32] {
    bytes(&vector.source_launch_hex).try_into().unwrap()
}
fn native_offsets(raw: &[u8]) -> Vec<usize> {
    if raw[72] == 1 {
        vec![8, 40, 73]
    } else {
        vec![8, 40]
    }
}
fn diagnostic_start(raw: &[u8]) -> usize {
    if raw[72] == 1 {
        105
    } else {
        73
    }
}
fn serial(raw: &mut [u8], start: usize, value: u64) {
    raw[start + 16..start + 24].copy_from_slice(&value.to_be_bytes());
}

#[test]
fn real_source_codec_vectors_preserve_raw_tuple_and_all_four_lengths() {
    let mut lengths = Vec::new();
    for vector in vectors() {
        let raw = bytes(&vector.context_hex);
        let decoded = decode(&raw, &launch(&vector)).unwrap();
        assert_eq!(decoded.raw_bytes(), raw);
        assert_eq!(
            decoded.actor().source_uuid().as_slice(),
            bytes(&vector.source_uuid_hex)
        );
        assert_eq!(decoded.actor().serial().get(), vector.actor_serial);
        assert_eq!(decoded.actor().raw_bytes().as_slice(), &raw[8..40]);
        assert_eq!(decoded.root().raw_bytes().as_slice(), &raw[40..72]);
        assert_eq!(decoded.parent().is_some(), raw[72] == 1);
        lengths.push(raw.len());
    }
    lengths.sort_unstable();
    lengths.dedup();
    assert_eq!(lengths, vec![106, 122, 138, 154]);
}

#[test]
fn identical_history_labels_do_not_recover_prior_actor_or_native_parent() {
    let vectors = vectors();
    let first = bytes(
        &vectors
            .iter()
            .find(|v| v.name == "root_with_diagnostic_parent")
            .unwrap()
            .context_hex,
    );
    let reopened = bytes(
        &vectors
            .iter()
            .find(|v| v.name == "fresh_root_same_history")
            .unwrap()
            .context_hex,
    );
    let expected = launch(&vectors[0]);
    let first = decode(&first, &expected).unwrap();
    let reopened = decode(&reopened, &expected).unwrap();
    assert_eq!(first.diagnostic_labels(), reopened.diagnostic_labels());
    assert_ne!(first.actor(), reopened.actor());
    assert_eq!(first.actor(), first.root());
    assert_eq!(reopened.actor(), reopened.root());
    assert!(reopened.parent().is_none());
    assert!(reopened.diagnostic_labels().parent.is_some());
}

#[test]
fn every_truncation_is_refused() {
    for vector in vectors() {
        let raw = bytes(&vector.context_hex);
        for end in 0..raw.len() {
            assert!(
                decode(&raw[..end], &launch(&vector)).is_err(),
                "{} cut {end}",
                vector.name
            );
        }
    }
}

#[test]
fn prefix_changes_and_all_trailing_bytes_are_refused() {
    for vector in vectors() {
        let original = bytes(&vector.context_hex);
        for offset in 0..8 {
            let mut raw = original.clone();
            raw[offset] ^= 1;
            assert_eq!(decode(&raw, &launch(&vector)), Err(DecodeError::Prefix));
        }
        for value in 0..=255 {
            let mut raw = original.clone();
            raw.push(value);
            assert_eq!(decode(&raw, &launch(&vector)), Err(DecodeError::Trailing));
        }
    }
}

#[test]
fn both_optional_flags_are_strict_binary() {
    for vector in vectors() {
        let original = bytes(&vector.context_hex);
        let label_tag = diagnostic_start(&original) + 32;
        for offset in [72, label_tag] {
            for value in 2..=255 {
                let mut raw = original.clone();
                raw[offset] = value;
                assert_eq!(decode(&raw, &launch(&vector)), Err(DecodeError::Flag));
            }
        }
    }
}

#[test]
fn every_reserved_byte_in_occurrences_and_expected_launch_is_zero() {
    for vector in vectors() {
        let original = bytes(&vector.context_hex);
        for start in native_offsets(&original) {
            for offset in start + 24..start + 32 {
                let mut raw = original.clone();
                raw[offset] = 1;
                assert_eq!(decode(&raw, &launch(&vector)), Err(DecodeError::Reserved));
            }
        }
        for offset in 16..32 {
            let mut expected = launch(&vector);
            expected[offset] = 1;
            assert_eq!(decode(&original, &expected), Err(DecodeError::Reserved));
        }
    }
}

#[test]
fn source_epoch_and_uuid_profile_are_checked_for_all_actual_ids() {
    for vector in vectors() {
        let original = bytes(&vector.context_hex);
        for start in native_offsets(&original) {
            let mut raw = original.clone();
            raw[start] ^= 1;
            assert_eq!(
                decode(&raw, &launch(&vector)),
                Err(DecodeError::SourceEpoch)
            );
        }
        let mut expected = launch(&vector);
        expected[0] ^= 1;
        assert_eq!(decode(&original, &expected), Err(DecodeError::SourceEpoch));
        for (offset, value) in [(6, 0x10), (6, 0x70), (8, 0x00), (8, 0xc0)] {
            let mut raw = original.clone();
            let mut expected = launch(&vector);
            expected[offset] = value;
            for start in native_offsets(&original) {
                raw[start + offset] = value;
            }
            assert_eq!(decode(&raw, &expected), Err(DecodeError::SourceEpoch));
        }
    }
}

#[test]
fn serials_are_nonzero_and_big_endian_without_reminting() {
    for vector in vectors() {
        let original = bytes(&vector.context_hex);
        for start in native_offsets(&original) {
            let mut raw = original.clone();
            serial(&mut raw, start, 0);
            assert_eq!(decode(&raw, &launch(&vector)), Err(DecodeError::Serial));
        }
        let mut raw = original.clone();
        serial(&mut raw, 8, u64::MAX);
        if raw[72] == 0 {
            serial(&mut raw, 40, u64::MAX);
        }
        // Hostile but structurally valid data stays untrusted, including the largest serial.
        assert_eq!(
            decode(&raw, &launch(&vector))
                .unwrap()
                .actor()
                .serial()
                .get(),
            u64::MAX
        );
    }
}

#[test]
fn impossible_birth_relationship_shapes_are_refused() {
    for vector in vectors() {
        let original = bytes(&vector.context_hex);
        if original[72] == 0 {
            let mut raw = original.clone();
            serial(&mut raw, 40, vector.actor_serial + 1);
            assert_eq!(decode(&raw, &launch(&vector)), Err(DecodeError::Structure));
        } else {
            let mut raw = original.clone();
            serial(&mut raw, 73, vector.actor_serial);
            assert_eq!(decode(&raw, &launch(&vector)), Err(DecodeError::Structure));
            let mut raw = original.clone();
            serial(&mut raw, 40, vector.actor_serial);
            assert_eq!(decode(&raw, &launch(&vector)), Err(DecodeError::Structure));
        }
    }
}

#[test]
fn diagnostic_uuid_bits_and_relations_are_never_native_authority() {
    for vector in vectors() {
        let mut raw = bytes(&vector.context_hex);
        let start = diagnostic_start(&raw);
        raw[start..start + 32].fill(0xff);
        if raw[start + 32] == 1 {
            raw[start + 33..start + 49].fill(0);
        }
        let decoded = decode(&raw, &launch(&vector)).unwrap();
        assert_eq!(decoded.diagnostic_labels().actor, [0xff; 16]);
        assert_eq!(decoded.diagnostic_labels().root, [0xff; 16]);
        assert_eq!(decoded.raw_bytes(), raw);
    }
}
