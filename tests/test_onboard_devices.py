"""The machines this one trusts file requests from: a private file, revocation by name or fingerprint prefix."""

import json

import pytest
from onboard_support import Recorder

from poolhouse.fleet.onboard.devices import Devices

FP1, FP2 = "1" * 64, "2" * 64


def book(tmp_path):
    rec = Recorder()
    devices = Devices(tmp_path / "devices.json", bus=rec.bus)
    for fp, address in ((FP1, "10.0.0.5"), (FP2, "10.0.0.6")):
        devices.add_accepter(fp, "kitchen-pi", address, secret="c2VjcmV0", mine=True)
    return devices, rec


def test_revoke_by_fingerprint_prefix_and_ambiguity(tmp_path):
    devices, rec = book(tmp_path)
    with pytest.raises(ValueError):
        devices.revoke("kitchen-pi")                # two devices share that name
    with pytest.raises(KeyError):
        devices.revoke("ffff")
    assert devices.revoke("1111").fingerprint == FP1
    assert [d.status for d in devices.all()] == ["revoked", "active"]
    assert devices.revoked(FP1) and not devices.revoked(FP2)
    assert rec.kinds() == ["onboard.revoked"]


def test_a_removed_device_is_trusted_again_only_after_the_owner_allows_it(tmp_path):
    devices, _ = book(tmp_path)
    devices.revoke("1111")
    assert devices.allow_again(FP1)
    assert [d.fingerprint for d in devices.all()] == [FP2]
    assert not devices.allow_again(FP1)


def test_text_from_another_machine_becomes_one_short_printable_line(tmp_path):
    devices = Devices(tmp_path / "devices.json", bus=Recorder().bus)
    evil = "pi\n\x1b[31mACCEPT\x1b[0m‮gnp.exe" + "A" * 300
    name = devices.add_accepter(FP1, evil, "10.0.0.5", secret="c2VjcmV0").name
    assert "\n" not in name and "\x1b" not in name and "‮" not in name and len(name) <= 64


def test_the_file_is_private_and_carries_a_schema_version(tmp_path):
    book(tmp_path)
    path = tmp_path / "devices.json"
    assert json.loads(path.read_text())["schema_version"] == 1
    assert path.stat().st_mode & 0o077 == 0
