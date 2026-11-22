"""What a launch does with a llama-server that is not the one that was pinned: re-pin it when something vouches for it,
replace it when nothing does, and never hold it or ask a person.

The binaries here are plain files and one real macOS system binary (the policy hashes bytes, reads ``BUILD.json`` and asks
`codesign`; it runs nothing), and every pin file is a real JSON file in the test's own state root. The one seam is the
fetch of a good build (`binary._fetch_good_build`), which would reach GitHub.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse.files import sha256_file
from poolhouse.serve import binary as binary_module, binary_trust, policy
from poolhouse.serve.binary import BinaryTampered, find_binary, managed_builds, vetted
from poolhouse.serve.build_paths import BuildFailed
from poolhouse.serve.llamacpp_trust import PoolPolicy
from poolhouse.serve.policy import LocalPolicy


def build(where: Path, content: bytes = b"server one", *, record: bool = True, built_at: str = "") -> Path:
    """A ``llama-server`` file in ``where``, with the ``BUILD.json`` a build writes beside it."""
    where.mkdir(parents=True, exist_ok=True)
    path = where / "llama-server"
    path.write_bytes(content)
    path.chmod(0o755)
    if record:
        (where / "BUILD.json").write_text(json.dumps({"sha256": sha256_file(path), "built_at": built_at}))
    return path


def kept(name: str, content: bytes, built_at: str) -> Path:
    """A build kept under the managed ``builds/`` directory, as `poolhouse-serve build` leaves one."""
    return build(managed_builds() / name, content, built_at=built_at).resolve()


def pins(local: LocalPolicy) -> dict:
    return json.loads(local.file.read_text())["pins"]


def no_fetch(monkeypatch) -> list:
    """Fetching a good build fails (as it does offline); what it was asked is recorded."""
    asked = []

    def failing():
        asked.append(1)
        raise BuildFailed("could not reach ggml-org/llama.cpp's GitHub releases")

    monkeypatch.setattr(binary_module, "_fetch_good_build", failing)
    return asked


def test_a_binary_is_pinned_on_first_use_and_the_pin_file_is_plain_json(tmp_path, caplog):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    with caplog.at_level(logging.WARNING):
        assert vetted(server, local) == server
    assert json.loads(local.file.read_text())["schema_version"] == 1
    assert pins(local)[str(server)] == {"sha256": sha256_file(server), "source": "first-use", "previous": ""}
    assert "TRUST ON FIRST USE" in caplog.text


def test_a_binary_that_matches_its_build_record_is_pinned_as_built(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a")
    assert vetted(server, local) == server
    assert pins(local)[str(server)]["source"] == "build"


def test_a_binary_whose_build_record_claims_a_digest_it_does_not_have_is_not_trusted_as_a_build(tmp_path, caplog):
    """BUILD.json says one digest and the file is another: it is not a Poolhouse build, so the newest earlier good
    build is used instead, and the file is not pinned."""
    local = LocalPolicy(tmp_path / "pins.json")
    older = kept("b100", b"older build", "2026-01-01")
    kept("b50", b"oldest build", "2025-01-01")
    liar = kept("b200", b"newest build", "2026-02-01")
    liar.write_bytes(b"not what BUILD.json says")
    with caplog.at_level(logging.WARNING):
        assert vetted(liar, local) == older
    assert "NOT TRUSTED" in caplog.text and "FELL BACK" in caplog.text
    assert str(liar) not in (pins(local) if local.file.exists() else {})


def test_a_pinned_binary_that_is_replaced_falls_back_to_the_previous_build_and_nothing_is_held(tmp_path, caplog):
    local = LocalPolicy(tmp_path / "pins.json")
    older = kept("b100", b"older build", "2026-01-01")
    server = build(tmp_path / "a", record=False)
    assert vetted(server, local) == server
    server.write_bytes(b"swapped by someone")
    with caplog.at_level(logging.WARNING):
        assert vetted(server, local) == older
    assert "NOT TRUSTED" in caplog.text
    server.write_bytes(b"server one")   # the original bytes are back: nothing was held, the pin still matches
    assert vetted(server, local) == server


def test_with_no_earlier_build_a_good_one_is_fetched_pinned_and_used(tmp_path, monkeypatch, caplog):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    assert vetted(server, local) == server
    pinned_before = pins(local)[str(server)]["sha256"]
    server.write_bytes(b"swapped by someone")
    fetched = build(managed_builds() / "b300", b"a fetched build", record=False)
    monkeypatch.setattr(binary_module, "_fetch_good_build", lambda: fetched)
    with caplog.at_level(logging.WARNING):
        assert vetted(server, local) == fetched
    assert "RECOVERED" in caplog.text
    assert pins(local)[str(fetched)] == {"sha256": sha256_file(fetched), "source": "recovered", "previous": ""}
    assert pins(local)[str(server)]["sha256"] == pinned_before   # the replaced binary was not pinned


def test_when_nothing_can_replace_a_changed_binary_the_refusal_names_the_recovery_that_was_tried(tmp_path, monkeypatch):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    assert vetted(server, local) == server
    server.write_bytes(b"swapped by someone")
    asked = no_fetch(monkeypatch)
    with pytest.raises(BinaryTampered) as refused:
        vetted(server, local)
    assert asked == [1]
    said = str(refused.value)
    assert "no earlier good build is kept" in said and "fetching a good one" in said and "GitHub releases" in said


def test_a_rebuilt_binary_whose_build_record_vouches_for_it_is_pinned_again(tmp_path, caplog):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a")
    assert vetted(server, local) == server
    before = sha256_file(server)
    build(tmp_path / "a", b"server two")   # `poolhouse-serve build` again: new bytes, new BUILD.json
    with caplog.at_level(logging.WARNING):
        assert vetted(server, local) == server
    assert "RE-PINNED" in caplog.text and before in caplog.text
    assert pins(local)[str(server)] == {"sha256": sha256_file(server), "source": "build", "previous": before}


def test_a_binary_a_package_manager_installed_is_pinned_again_when_it_changes(tmp_path, monkeypatch, caplog):
    cellar = tmp_path / "Cellar"
    monkeypatch.setattr(binary_trust, "PACKAGE_PREFIXES", (str(cellar),))
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(cellar / "llama.cpp" / "1" / "bin", record=False)
    server.chmod(0o555)
    assert vetted(server, local) == server
    server.chmod(0o755)
    server.write_bytes(b"brew upgrade, same path")
    server.chmod(0o555)
    with caplog.at_level(logging.WARNING):
        assert vetted(server, local) == server
    assert pins(local)[str(server)]["source"] == "package" and "RE-PINNED" in caplog.text


def test_a_binary_in_a_package_directory_that_others_can_write_is_not_the_package_managers(tmp_path, monkeypatch):
    cellar = tmp_path / "Cellar"
    monkeypatch.setattr(binary_trust, "PACKAGE_PREFIXES", (str(cellar),))
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(cellar / "llama.cpp" / "1" / "bin", record=False)
    assert vetted(server, local) == server
    server.write_bytes(b"changed")
    server.chmod(0o775)
    no_fetch(monkeypatch)
    with pytest.raises(BinaryTampered):
        vetted(server, local)


def test_a_binary_outside_every_package_directory_is_not_taken_for_one(tmp_path, monkeypatch):
    monkeypatch.setattr(binary_trust, "PACKAGE_PREFIXES", (str(tmp_path / "Cellar"),))
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "elsewhere" / "Cellar" / "llama.cpp" / "bin", record=False)
    assert vetted(server, local) == server
    server.write_bytes(b"changed")
    no_fetch(monkeypatch)
    with pytest.raises(BinaryTampered):
        vetted(server, local)


def apple_signed(where: Path) -> None:
    """``where`` holds a copy of a binary Apple signed (`/bin/ls`; code signatures are checked on macOS only)."""
    if sys.platform != "darwin":
        pytest.skip("code signatures are checked on macOS")
    shutil.copyfile("/bin/ls", where)
    where.chmod(0o755)


def test_a_binary_with_an_apple_signature_that_verifies_is_pinned_again_when_it_changes(tmp_path, caplog):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    assert vetted(server, local) == server
    apple_signed(server)   # a real binary Apple signed: its signature verifies where it is copied to
    with caplog.at_level(logging.WARNING):
        assert vetted(server, local) == server
    assert pins(local)[str(server)]["source"] == "signed" and "RE-PINNED" in caplog.text


def test_an_ad_hoc_signature_vouches_for_nothing_because_any_compiler_run_makes_one(tmp_path, monkeypatch):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    assert vetted(server, local) == server
    apple_signed(server)
    subprocess.run(["/usr/bin/codesign", "--force", "-s", "-", str(server)], check=True, capture_output=True)
    assert subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(server)], capture_output=True).returncode == 0
    no_fetch(monkeypatch)
    with pytest.raises(BinaryTampered):
        vetted(server, local)


def test_a_signed_binary_that_was_patched_afterwards_vouches_for_nothing(tmp_path, monkeypatch):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    assert vetted(server, local) == server
    apple_signed(server)
    server.write_bytes(server.read_bytes() + b"\x00")
    no_fetch(monkeypatch)
    with pytest.raises(BinaryTampered):
        vetted(server, local)


def test_a_pin_file_with_keys_this_version_does_not_know_still_reads(tmp_path, monkeypatch):
    """A file written by a newer version: unknown top-level keys, unknown fields in an entry, an entry that
    is not an entry. The digest still decides, and what it does not understand is ignored, not an error."""
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    local.file.write_text(json.dumps({
        "schema_version": 9, "from_the_future": {"x": 1},
        "pins": {str(server): {"sha256": sha256_file(server), "source": "build", "host": "h:1", "signed": True},
                 "/nowhere": "not an entry", "/also": {"no": "digest"}}}))
    assert vetted(server, local) == server
    server.write_bytes(b"changed")
    no_fetch(monkeypatch)
    with pytest.raises(BinaryTampered):
        vetted(server, local)


@pytest.mark.parametrize("damaged", ["{not json", json.dumps({"pins": ["a list"]}), json.dumps(["not", "an", "object"])])
def test_a_pin_file_that_cannot_be_read_is_moved_aside_and_rebuilt_not_a_reason_to_stop(tmp_path, caplog, damaged):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    local.file.write_text(damaged)
    with caplog.at_level(logging.WARNING):
        assert vetted(server, local) == server
    aside = [p for p in tmp_path.iterdir() if p.name.startswith("pins.json.unreadable-")]
    assert len(aside) == 1 and aside[0].read_text() == damaged
    assert "PIN FILE REBUILT" in caplog.text and str(aside[0]) in caplog.text
    assert pins(local)[str(server)]["sha256"] == sha256_file(server)
    assert vetted(server, local) == server   # and the rebuilt file is a pin file like any other


def test_an_old_held_flag_holds_nothing(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = build(tmp_path / "a", record=False)
    local.file.write_text(json.dumps({"pins": {str(server): {"sha256": sha256_file(server), "held": True}}}))
    assert vetted(server, local) == server
    local.file.write_text(json.dumps({"pins": {str(server): {"held": True}}}))
    assert vetted(server, local) == server   # an entry with no digest is no pin: the binary is pinned as it is


def test_the_environment_passes_through_the_local_policy(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    env = {"PATH": "/bin", "HOME": "/h"}
    assert local.scrub_env(env) == env and local.scrub_env(env) is not env


def test_the_managed_build_is_vetted_by_the_policy_it_is_given(tmp_path, monkeypatch):
    current = binary_module.managed_current()
    server = build(current)
    local = LocalPolicy()
    assert find_binary(policy=local) == server.resolve()
    older = kept("b100", b"older build", "2026-01-01")
    server.write_bytes(b"swapped")
    assert find_binary(policy=local) == older


def test_a_pinned_managed_build_that_is_gone_is_replaced_not_walked_past_to_another_binary(tmp_path, monkeypatch):
    current = binary_module.managed_current()
    server = build(current)
    local = LocalPolicy()
    assert find_binary(policy=local) == server.resolve()
    older = kept("b100", b"older build", "2026-01-01")
    server.unlink()   # moved aside, say
    assert find_binary(policy=local) == older


def test_with_no_policy_given_the_pool_decides(monkeypatch):
    assert isinstance(policy.default(), PoolPolicy)


def test_in_strict_mode_every_route_to_the_binary_is_vetted_and_by_default_only_current_is(tmp_path, monkeypatch):
    server = build(tmp_path / "elsewhere")
    server.write_bytes(b"not what BUILD.json says")
    older = kept("b100", b"older build", "2026-01-01")
    monkeypatch.setenv("LLAMA_CPP_SERVER", str(server))
    assert find_binary() == server.resolve()    # the pool's default: an explicit route is the caller's own business
    assert find_binary(policy=LocalPolicy(tmp_path / "pins.json"), strict=True) == older
    assert find_binary(explicit=server, policy=LocalPolicy(tmp_path / "pins.json")) == server.resolve()
    assert find_binary(explicit=server, policy=LocalPolicy(tmp_path / "pins.json"), strict=True) == older


def test_the_pool_policy_pins_in_the_sentinel_and_vets_the_same_way(tmp_path, caplog):
    pool = PoolPolicy()
    older = kept("b100", b"older build", "2026-01-01")
    server = build(tmp_path / "a")
    assert vetted(server, pool) == server
    assert pool.pinned(server) == sha256_file(server)
    build(tmp_path / "a", b"server two")   # rebuilt, BUILD.json rewritten: vouched for
    with caplog.at_level(logging.WARNING):
        assert vetted(server, pool) == server
    assert pool.pinned(server) == sha256_file(server) and "RE-PINNED" in caplog.text
    server.write_bytes(b"swapped by someone")   # nothing vouches for this one
    assert vetted(server, pool) == older
    assert server.is_file()   # and nothing was moved aside


def test_the_fetch_that_replaces_a_binary_nothing_vouches_for_installs_checks_and_switches_to_a_release(tmp_path, monkeypatch, caplog):
    """The whole chain but the download: `build_from_release` is replaced by a copy of a release already on disk (a canned
    plumbing server, which answers ``--help`` as a build does); the install, the ``--help`` and architecture check,
    the switch of `current` and the pin are the real code."""
    from poolhouse.serve import build_recover
    from poolhouse.serve.build_paths import builds_dir, current_link
    from poolhouse.testing.canned import canned_binary

    def release(args):
        assert args.force   # a copy of the release that is already in builds/ is the one that was rejected
        dest = builds_dir() / "b9999"
        dest.mkdir(parents=True)
        canned_binary(dest, name="llama-server")
        (dest / "BUILD.json").write_text(json.dumps({"commit": "b9999", "source": "release"}))
        return dest, "b9999"

    monkeypatch.setattr(build_recover, "build_from_release", release)
    local = LocalPolicy()
    current = binary_module.managed_current()
    server = build(current)
    assert find_binary(policy=local) == server.resolve()
    server.write_bytes(b"patched in place")
    with caplog.at_level(logging.WARNING):
        got = find_binary(policy=local, strict=True)
    fetched = (builds_dir() / "b9999" / "llama-server").resolve()
    assert got == fetched and current.resolve() == fetched.parent
    assert pins(local)[str(fetched)]["source"] == "recovered" and "RECOVERED" in caplog.text
    assert find_binary(policy=local) == fetched and current_link().resolve() == fetched.parent   # and later calls find it


def test_a_binary_path_that_looks_like_an_option_or_a_command_is_one_argument_of_codesign(tmp_path):
    """The path goes to `codesign` as one argv element, never through a shell: a directory named like an option or
    a command substitution verifies nothing, runs nothing and leaves no marker file."""
    if sys.platform != "darwin":
        pytest.skip("code signatures are checked on macOS")
    marker = tmp_path / "ran"
    where = tmp_path / f"--force -s evil; touch {marker}; $(touch {marker}) `touch {marker}`\nnext"
    server = build(where, record=False)
    assert binary_trust.signed(server) is False
    apple_signed(server)
    assert binary_trust.signed(server) is True   # the same strange path verifies when the signature is real
    assert not marker.exists()
