"""Installation previews are bounded data, never installation authority."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("native_install_plan", Path(__file__).resolve().parents[1] / "app/native-installer/plan.py")
PLAN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLAN
SPEC.loader.exec_module(PLAN)


def preview():
    artifact = {"executable_sha256": "a" * 64, "resources_sha256": "b" * 64, "cdhash": "c" * 40}
    return {"version": 1, "source": "1" * 64, "build": "2" * 64, "policy": "3" * 64, "authority": "4" * 64,
            "owner_uid": 501, "helper": artifact.copy(), "gui": artifact.copy(), "destinations": list(PLAN.DESTINATIONS),
            "required_flags": list(PLAN.REQUIRED_FLAGS), "forbidden_entitlements": list(PLAN.FORBIDDEN_ENTITLEMENTS)}


def parse(value):
    return PLAN.Plan.parse(json.dumps(value).encode())


def test_canonical_preview_roundtrip_and_digest_cover_every_binding():
    original = parse(preview())
    assert PLAN.Plan.parse(original.canonical()) == original
    assert original.canonical() == parse(dict(reversed(list(preview().items())))).canonical()
    for key in ("source", "build", "policy", "authority", "owner_uid"):
        changed = preview()
        changed[key] = 502 if key == "owner_uid" else "e" * 64
        assert parse(changed).digest() != original.digest()
    for role in ("helper", "gui"):
        for key in ("executable_sha256", "resources_sha256", "cdhash"):
            changed = preview()
            changed[role][key] = "e" * (40 if key == "cdhash" else 64)
            assert parse(changed).digest() != original.digest()


@pytest.mark.parametrize("key,value", [
    ("version", True), ("version", 1.0), ("owner_uid", True), ("owner_uid", 0),
    ("owner_uid", 2**32-1), ("source", "A"*64), ("authority", "0"*64),
    ("destinations", ["/tmp/authority"]), ("required_flags", ["runtime"]),
    ("forbidden_entitlements", []), ("namespace", "chosen-by-caller"),
    ("command", "install"), ("endpoint", "user-service"), ("reset", True),
])
def test_modified_policy_or_authority_controls_are_refused(key, value):
    changed = preview()
    changed[key] = value
    with pytest.raises(ValueError, match="preview is invalid"):
        parse(changed)


def test_duplicate_unknown_missing_and_oversized_fields_are_refused():
    data = json.dumps(preview()).encode()
    for hostile in (b'{"version":1,' + data[1:], data.replace(b'"cdhash":', b'"extra":1,"cdhash":', 1),
                    b" " * (PLAN.MAX_BYTES + 1), b"[" * 1200, b"\xff"):
        with pytest.raises(ValueError):
            PLAN.Plan.parse(hostile)
    missing = preview()
    del missing["policy"]
    with pytest.raises(ValueError):
        parse(missing)


def test_rehashed_caller_edit_is_still_only_a_different_preview():
    trusted_measurement = parse(preview())
    changed = preview()
    changed["helper"]["executable_sha256"] = "d" * 64
    caller_plan = parse(changed)
    assert len(caller_plan.digest()) == 64
    assert caller_plan != trusted_measurement
    assert not hasattr(caller_plan, "install")
    assert not hasattr(caller_plan, "authorize")
