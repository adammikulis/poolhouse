"""A pin store written by a newer build reads in this one: a field this `Pin` does not have is dropped, not a crash.

The failure this keeps out: `Manifest.pins()` built ``Pin(**stored)``, so one pin carrying a field a later build
added (``host`` was one) made every launch of the machine's llama-server die in `binary.vetted`.
"""

from __future__ import annotations

from dataclasses import asdict

from poolhouse.sentinel.integrity import Manifest, Pin


def test_a_pin_with_a_field_this_build_does_not_know_still_reads_and_still_pins_the_digest(tmp_path):
    manifest = Manifest(tmp_path / "manifest.json")
    file = tmp_path / "llama-server"
    file.write_bytes(b"server")
    made = manifest.pin(file, "binary", source="build")
    stored = {**asdict(made), "added_by_a_later_build": "x", "another": 3}
    manifest._file.save({"pins": {made.path: stored, "/gone": {**stored, "path": "/gone", "sha256": "ab" * 32}}})

    read = manifest.pins()

    assert isinstance(read[made.path], Pin) and read[made.path] == made
    assert read["/gone"].sha256 == "ab" * 32
    assert manifest.pin_of(file) == made


def test_a_pin_missing_a_required_field_is_still_an_error_not_a_guess(tmp_path):
    """Dropping what is unknown is not inventing what is absent: a pin without its digest does not read."""
    import pytest

    manifest = Manifest(tmp_path / "manifest.json")
    manifest._file.save({"pins": {"/x": {"path": "/x", "kind": "binary"}}})
    with pytest.raises(TypeError):
        manifest.pins()
