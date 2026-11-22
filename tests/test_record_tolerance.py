"""A manifest, pin file or hold written by a newer build reads in this one, and what is not known is never trusted.

The record is the newer build's real shape: a `host` field this snapshot's `Pin` lacked (the crash that stopped
`poolhouse-serve up`: `Pin.__init__() got an unexpected keyword argument 'host'`) and a field this build has never heard
of, with a nested object in it. Each test builds the file on disk and reads it back through the code `up` goes through.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict

import pytest

import poolhouse.tolerant as tolerant
from poolhouse import sentinel
from poolhouse.files import sha256_file
from poolhouse.sentinel.integrity import Manifest, Pin
from poolhouse.serve import binary as binary_module, binary_trust, build_report, llamacpp_trust
from poolhouse.serve.build_paths import BuildFailed
from poolhouse.serve.holding import Hold
from poolhouse.serve.policy import LocalPolicy

NEWER = {"attested_by": {"kind": "sigstore", "chain": [{"issuer": "x", "extra": {"deep": [1, 2]}}]}, "host": "huggingface.co:443"}


def newer_pin(file, kind="binary"):
    """A pin as a newer build writes it: this build's own record plus fields it does not know."""
    made = Manifest(file.parent / "scratch.json").pin(file, kind, source="build")
    return {**asdict(made), **NEWER}


def test_up_does_not_crash_on_a_pin_a_newer_build_wrote(tmp_path):
    """`vetted` is what `poolhouse-serve up` asks first; it reads every pin."""
    server = tmp_path / "llama-server"
    server.write_bytes(b"server")
    server.chmod(0o755)
    node = sentinel.default()
    node.manifest._file.save({"pins": {str(server): newer_pin(server)}})

    assert binary_module.vetted(server, llamacpp_trust.PoolPolicy()) == server
    assert node.manifest.pin_of(server).sha256 == sha256_file(server)


def test_an_unknown_field_is_named_once_in_one_warning(tmp_path, caplog):
    tolerant._NAMED.clear()
    server = tmp_path / "s"
    server.write_bytes(b"x")
    manifest = Manifest(tmp_path / "m.json")
    manifest._file.save({"pins": {str(server): newer_pin(server)}})
    with caplog.at_level(logging.WARNING, logger="poolhouse.tolerant"):
        manifest.pins()
        manifest.pins()
    said = [r.getMessage() for r in caplog.records]
    assert sum("attested_by" in m for m in said) == 1


def test_an_unknown_field_never_changes_the_comparison(tmp_path):
    """Unknown fields (even a `revoked`) are not read: the pin is what its known fields say."""
    server = tmp_path / "s"
    server.write_bytes(b"x")
    manifest = Manifest(tmp_path / "m.json")
    plain = asdict(Manifest(tmp_path / "o.json").pin(server, "binary"))
    manifest._file.save({"pins": {str(server): {**plain, **NEWER, "revoked": True}}})
    assert manifest.pin_of(server) == Pin(**{**plain, "host": NEWER["host"]})
    assert "revoked" not in manifest.pin_of(server).to_json()


@pytest.mark.parametrize("field,value", [("sha256", {"a": 1}), ("bytes", "9"), ("bytes", True), ("path", None),
                                         ("pinned_at", "now"), ("host", ["h"])])
def test_a_known_field_of_the_wrong_type_is_an_error_not_a_pin(tmp_path, field, value):
    server = tmp_path / "s"
    server.write_bytes(b"x")
    manifest = Manifest(tmp_path / "m.json")
    manifest._file.save({"pins": {str(server): {**newer_pin(server), field: value}}})
    with pytest.raises(TypeError):
        manifest.pins()


def test_a_pin_that_is_not_an_object_is_an_error(tmp_path):
    manifest = Manifest(tmp_path / "m.json")
    manifest._file.save({"pins": {"/x": ["sha256", "ab"]}})
    with pytest.raises(TypeError):
        manifest.pins()


def test_known_only_bounds_what_a_hostile_record_costs(caplog):
    tolerant._NAMED.clear()
    with caplog.at_level(logging.WARNING, logger="poolhouse.tolerant"):
        kept = tolerant.known_only({"ok": 1, **{"k" * 100_000 + str(i): i for i in range(2000)}}, {"ok"}, what="x")
    assert kept == {"ok": 1}
    assert len(tolerant._NAMED) <= 256
    assert all(len(r.getMessage()) < 300 for r in caplog.records)


def test_a_hold_with_unknown_fields_reads_and_one_that_is_not_an_object_does_not(tmp_path):
    path = tmp_path / "hold.json"
    path.write_text(json.dumps({"id": "h", "shape": "s", "model": "m", "context": 1, "parallel": 1, "pid": 7, "future": {"nested": {"x": 1}}}))
    assert Hold.read(path).pid == 7
    path.write_text(json.dumps([1, 2]))
    assert Hold.read(path) is None


def test_a_build_manifest_that_is_not_an_object_is_no_manifest(tmp_path):
    (tmp_path / "BUILD.json").write_text(json.dumps(["not", "an", "object"]))
    assert build_report.manifest_of(tmp_path) == {}
    server = tmp_path / "llama-server"
    server.write_bytes(b"x")
    assert binary_module.manifest_of(server) == {}
    assert binary_module.borrows(server) is False


def test_a_newer_build_manifest_keeps_what_this_build_reads(tmp_path):
    (tmp_path / "BUILD.json").write_text(json.dumps({"commit": "abc", "sha256": "f" * 64, **NEWER}))
    assert build_report.manifest_of(tmp_path)["commit"] == "abc"


def test_the_local_pin_file_ignores_unknown_entry_keys_and_they_grant_nothing(tmp_path):
    local = LocalPolicy(tmp_path / "pins.json")
    server = tmp_path / "llama-server"
    server.write_bytes(b"s")
    local.file.write_text(json.dumps({"schema_version": 1, "pins": {str(server): {
        "sha256": "0" * 64, "source": "build", "trusted": True, "allow_any_digest": True, **NEWER}}}))
    assert local._read()[str(server)] == {"sha256": "0" * 64, "source": "build", "previous": ""}
    # the digest is not the file's and nothing vouches for the new bytes: it is not run, whatever the unknown keys say
    def nothing_to_fetch():
        raise BuildFailed("offline")

    with pytest.raises(binary_trust.Untrusted):
        binary_trust.vet(server, local, previous=list, recover=nothing_to_fetch)


def test_a_restriction_a_newer_writer_expresses_as_held_holds_nothing_here(tmp_path):
    """Nothing is held any more: a pin written with `held` by an older or newer build is read for its digest alone."""
    local = LocalPolicy(tmp_path / "pins.json")
    server = tmp_path / "llama-server"
    server.write_bytes(b"s")
    local.file.write_text(json.dumps({"schema_version": 9, "pins": {str(server): {
        "sha256": sha256_file(server), "held": True, "reason": {"by": "x"}}}}))
    assert binary_module.vetted(server, local) == server
