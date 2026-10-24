"""Reference-only transcript vectors; no native signer or authority is simulated."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from node_contract.load import load as load_contract  # noqa: E402
from node_contract.transcripts import (  # noqa: E402
    PROFILES,
    ReferenceTranscripts,
    _count,
    _frame,
    _scalar,
    load,
)

SOURCE = ROOT / "contracts/node"
VECTORS = json.loads((SOURCE / "native_boundaries/transcript-vectors.json").read_text())["vectors"]


def raw(value):
    return json.dumps(value, ensure_ascii=True).encode()


@pytest.fixture(scope="module")
def codec():
    return ReferenceTranscripts(SOURCE)


def test_independently_derived_complete_fixed_vectors(codec):
    for vector in VECTORS:
        purpose, value = vector["purpose"], vector["value"]
        assert codec.transcript_bytes(purpose, raw(value)).hex() == vector["transcript_hex"]
        assert codec.digest_bytes(purpose, raw(value)).hex() == vector["digest_hex"]
        assert codec.verify_digest(purpose, raw(value)) is None
        assert raw(value) == raw(vector["value"]), "verification must not repair input"


def test_exact_framing_unicode_and_overflow():
    assert _frame([None, False, True, "é", {"b": "", "a": "x"}]).hex() == (
        "41000000054e46545300000002c3a94f000000025300000001615300000001785300000001625300000000")
    assert _frame("é") != _frame("e\u0301")
    assert _frame(None) != _frame({}) != _frame([])
    assert _count(0xffffffff) == b"\xff" * 4
    for value in (-1, 2**32):
        with pytest.raises(ValueError):
            _count(value)
    with pytest.raises(UnicodeError):
        _frame("\ud800")


def paths(value, prefix=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield (*prefix, key)
            yield from paths(item, (*prefix, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield (*prefix, index)
            yield from paths(item, (*prefix, index))


def at(value, path):
    for key in path:
        value = value[key]
    return value


def test_every_immutable_vector_field_is_required_and_digest_bound(codec):
    count = 0
    for vector in VECTORS:
        purpose, value = vector["purpose"], vector["value"]
        for path in paths(value):
            changed = copy.deepcopy(value)
            parent = at(changed, path[:-1])
            original = parent[path[-1]]
            # Delete record members, including deep empty/tag/optional members;
            # mutate array entries without changing shape to exercise digest binding.
            if isinstance(parent, dict):
                del parent[path[-1]]
            else:
                parent[path[-1]] = None if original is not None else {}
            with pytest.raises(ValueError):
                codec.verify_digest(purpose, raw(changed))
            count += 1
        for path in paths(value):
            original = at(value, path)
            if not isinstance(original, str) or path == (PROFILES[purpose][1],):
                continue
            changed = copy.deepcopy(value)
            at(changed, path[:-1])[path[-1]] = original[:-1] + ("2" if original[-1:] != "2" else "3")
            with pytest.raises(ValueError):
                codec.verify_digest(purpose, raw(changed))
    assert count > 200


def test_root_self_hash_excluded_only_at_root_and_nested_digest_checked(codec):
    for vector in VECTORS:
        value = copy.deepcopy(vector["value"])
        value[PROFILES[vector["purpose"]][1]] = "ff" * 32
        assert codec.digest_bytes(vector["purpose"], raw(value)).hex() == vector["digest_hex"]
        with pytest.raises(ValueError, match="digest mismatch"):
            codec.verify_digest(vector["purpose"], raw(value))
    value = copy.deepcopy(VECTORS[1]["value"])
    value["scope"]["value"]["digest"] = "ff" * 32
    with pytest.raises(ValueError, match="digest mismatch"):
        codec.digest_bytes("local-authority-scoped-v1", raw(value))
    value = copy.deepcopy(VECTORS[1]["value"])
    value["scope"]["value"]["presentation"] += " altered"
    # Even a matching enclosing digest cannot repair the wrong nested approval.
    with pytest.raises(ValueError, match="digest mismatch"):
        codec.transcript_bytes("local-authority-scoped-v1", raw(value))


@pytest.mark.parametrize("fragment", [b'{"kind":"genesis","kind":"governed","value":{}}',
                                      b'{"kind":"genesis","value":{},"extra":null}',
                                      b'{"kind":"genesis"}', b'null', b'[]', b'1'])
def test_deep_union_unknown_duplicate_missing_and_wrong_types_refused(codec, fragment):
    vector = VECTORS[0]
    value = copy.deepcopy(vector["value"])
    value["context"] = "REPLACE_CONTEXT"
    encoded = raw(value).replace(b'"REPLACE_CONTEXT"', fragment)
    with pytest.raises(ValueError):
        codec.verify_digest(vector["purpose"], encoded)


def test_wrong_version_purpose_scope_and_numeric_representation(codec):
    value = copy.deepcopy(VECTORS[2]["value"])
    for version in ("0", "2", "01", 1, True):
        value["profile_version"] = version
        with pytest.raises(ValueError):
            codec.digest_bytes("local-authority-scoped-v1", raw(value))
    for purpose in ("native-authority-v1", "approval-immutable-v2", "poolhouse.approval.immutable-operation.v1\0"):
        with pytest.raises(ValueError):
            codec.digest_bytes(purpose, raw(VECTORS[0]["value"]))
    value = copy.deepcopy(VECTORS[2]["value"])
    value["scope"]["kind"] = "pool_approval"
    with pytest.raises(ValueError):
        codec.digest_bytes("local-authority-scoped-v1", raw(value))
    for encoded in (b'{"x":NaN}', b'{"x":1.0}', b'"\\ud800"', b'\xff'):
        with pytest.raises(ValueError):
            codec.digest_bytes("approval-immutable-v1", encoded)


def test_source_equivalent_private_scalar_boundaries():
    _, rows, _ = load_contract(SOURCE)
    definitions = {row["name"]: row for row in rows}
    sid = next(row for row in rows if row.get("encoding") == "sid-hex")
    audit = next(row for row in rows if row.get("encoding") == "audit-token-le32")
    for value in ("0100000000000000", "010100000000000512000000", "010f000000000005" + "12000000" * 15):
        assert _scalar(sid, value)
    for value in ("0200000000000000", "0110000000000005" + "12000000" * 16,
                  "0101000000000005", "010000000000000512000000", "000100000000000512000000",
                  "01010000000000051200000A", "01000000000000000", True):
        assert not _scalar(sid, value)
    assert _scalar(audit, "0403020111223344" * 4)
    for value in ("00" * 31, "00" * 33, "AA" * 32, "0" * 63, 32):
        assert not _scalar(audit, value)
    # Audit bytes are opaque little-endian words: framing preserves, never reverses them.
    assert _frame("04030201") != _frame("01020304")
    for value in ("01", "-0", "18446744073709551616", 1, True):
        assert not _scalar(definitions["U64"], value)


def test_reachable_source_drift_and_domain_substitution_refused(tmp_path):
    import shutil
    shutil.copytree(SOURCE, tmp_path / "node")
    root = tmp_path / "node"
    _, rows, _ = load_contract(SOURCE)
    source = root / "native_boundaries/transcripts.toml"
    original = source.read_text()
    source.write_text(original.replace("poolhouse.approval.immutable-operation.v1", "poolhouse.native-authority.operation.v1"))
    with pytest.raises(ValueError):
        load(root, rows)
    source.write_text(original)
    rows = copy.deepcopy(rows)
    approval = next(row for row in rows if row["name"] == "ApprovalImmutableOperation")
    approval["fields"] = approval["fields"][:-1]
    with pytest.raises(ValueError, match="schema changed"):
        load(root, rows)


def sample(definitions, name):
    """Structural untrusted fixture only; no signature or authorized identity."""
    row = definitions[name]
    kind = row["kind"]
    if kind == "record":
        return {field["name"]: sample(definitions, field["type"]) for field in row["fields"]}
    if kind == "union":
        variant = row["variants"][0]
        return {row["tag"]: variant["tag"], "value": sample(definitions, variant["type"])}
    if kind == "optional":
        return sample(definitions, row["item"])
    if kind == "list":
        return [sample(definitions, row["item"]) for _ in range(max(1, row.get("min_items", 0)))]
    encoding = row["encoding"]
    if encoding == "bool":
        return False
    if encoding in {"u32", "u64", "i32"}:
        return str(max(1, row.get("minimum", 0)))
    if encoding == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    if encoding in {"hex32", "audit-token-le32"}:
        return "12" * 32
    if encoding == "sid-hex":
        return "010100000000000512000000"
    if encoding == "bytes":
        return "12" * max(1, row["min_bytes"])
    if encoding == "finite-f64-bits":
        return "3ff0000000000000"
    return "x" * max(1, row["min_bytes"])


def test_all_reachable_union_and_optional_shapes_are_closed(codec):
    _, rows, _ = load_contract(SOURCE)
    definitions = {row["name"]: row for row in rows}
    covered = set()

    def check(purpose, root):
        # Computing fixture digests is not an independent vector; fixed vectors above are.
        if purpose == "local-authority-scoped-v1" and root["scope"]["kind"] == "pool_approval":
            nested = root["scope"]["value"]
            nested["digest"] = codec.digest_bytes("approval-immutable-v1", raw(nested)).hex()
        root[PROFILES[purpose][1]] = codec.digest_bytes(purpose, raw(root)).hex()
        codec.verify_digest(purpose, raw(root))

    def visit(purpose, root, name, path=()):
        row = definitions[name]
        value = at(root, path)
        if row["kind"] == "union":
            for variant in row["variants"]:
                changed = copy.deepcopy(root)
                replacement = {row["tag"]: variant["tag"], "value": sample(definitions, variant["type"])}
                if path:
                    at(changed, path[:-1])[path[-1]] = replacement
                else:
                    changed = replacement
                check(purpose, changed)
                covered.add((name, variant["tag"]))
                hostile = copy.deepcopy(changed)
                at(hostile, path)["unknown"] = None
                with pytest.raises(ValueError):
                    codec.digest_bytes(purpose, raw(hostile))
                visit(purpose, changed, variant["type"], (*path, "value"))
        elif row["kind"] == "record":
            for field in row["fields"]:
                visit(purpose, root, field["type"], (*path, field["name"]))
        elif row["kind"] == "list":
            for index, _ in enumerate(value):
                visit(purpose, root, row["item"], (*path, index))
        elif row["kind"] == "optional":
            changed = copy.deepcopy(root)
            at(changed, path[:-1])[path[-1]] = None
            check(purpose, changed)
            visit(purpose, root, row["item"], path)

    from node_contract.transcripts import closure
    for purpose, (name, _, _) in PROFILES.items():
        root = sample(definitions, name)
        check(purpose, root)
        visit(purpose, root, name)
        expected = {(row["name"], variant["tag"]) for row in closure(rows, name)
                    for variant in row.get("variants", [])}
        assert expected <= covered
