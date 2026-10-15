import ctypes
import hashlib
import importlib.util
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "app" / "native-host"
BASE = {
    "Helper/PoolhouseAuthority": ("helper", 0o755),
    "GUI/PoolhouseApproval.app/Contents/MacOS/PoolhouseApproval": ("gui", 0o755),
    "GUI/PoolhouseApproval.app/Contents/Info.plist": ("gui", 0o644),
    "GUI/PoolhouseApproval.app/Contents/_CodeSignature/CodeResources": ("gui", 0o644),
    "Services/app.poolhouse.authority.plist": ("service", 0o644),
}


def _inventory(root, entries):
    lines = ["poolhouse-native-candidate-v1\n"]
    for name, (role, mode) in sorted(entries.items()):
        value = (root / name).read_bytes()
        lines.append(f"{role}\t{name}\t{len(value)}\t{mode:04o}\t{hashlib.sha256(value).hexdigest()}\n")
    return "".join(lines)


@pytest.fixture(scope="module")
def native_host_tools(tmp_path_factory):
    if sys.platform != "darwin":
        pytest.skip("Native macOS code validation")
    temporary = tmp_path_factory.mktemp("native-host-tools")
    env = os.environ.copy()
    env.update(HOME=str(temporary), TMPDIR=str(temporary))
    spec = importlib.util.spec_from_file_location("native_host_build", SOURCE / "build.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    binary, protocol, library = temporary / "candidate", temporary / "protocol", temporary / "capture.dylib"
    builder.compile_sources(binary, temporary, env=env)
    builder.compile_sources(protocol, temporary, testing=True, env=env)
    builder.compile_sources(library, temporary, library=True, env=env)
    assert struct.unpack("<4I", binary.read_bytes()[:16]) == (0xFEEDFACF, 0x100000C, 0, 2)
    assert b"ph_host_test_policy" not in library.read_bytes()
    subprocess.run([str(protocol), "protocol"], check=True, env=env, capture_output=True, timeout=10)
    return binary, library


def _tree(tmp_path):
    root = tmp_path / "candidate-tree"
    root.mkdir()
    entries = BASE.copy()
    for name, (_, mode) in entries.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"inert-public-candidate")
        path.chmod(mode)
    inventory = tmp_path / "inventory.txt"
    inventory.write_text(_inventory(root, entries))
    return root, entries, inventory


def _run(binary, temporary, source, manifest, expected=0):
    env = os.environ.copy()
    env.update(HOME=str(temporary), TMPDIR=str(temporary))
    done = subprocess.run([str(binary), "inspect", str(source), str(manifest)], env=env,
                          capture_output=True, text=True, timeout=10)
    assert done.returncode == expected, done.stdout + done.stderr
    if expected == 0:
        assert done.stdout.startswith("Untrusted captured candidate; no installation or authority.\n")
    return done


def test_native_host_capture(tmp_path, native_host_tools):
    binary, library = native_host_tools
    root, entries, inventory = _tree(tmp_path)
    original = inventory.read_text()
    _run(binary, tmp_path, root, inventory)
    capture = ctypes.CDLL(str(library))
    capture.ph_host_protected_tree.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    capture.ph_host_protected_tree.restype = ctypes.c_bool
    names = "".join(name + "\n" for name in sorted(entries)).encode()
    assert not capture.ph_host_protected_tree(os.fsencode(root), names)
    helper = root / "Helper/PoolhouseAuthority"
    helper.write_bytes(b"changed-same-size-data")
    _run(binary, tmp_path, root, inventory, 1)
    helper.write_bytes(b"inert-public-candidate")
    helper.chmod(0o777)
    _run(binary, tmp_path, root, inventory, 1)
    helper.chmod(0o755)
    extra = root / "extra"
    extra.write_bytes(b"extra")
    _run(binary, tmp_path, root, inventory, 1)
    extra.unlink()
    outside = tmp_path / "outside"
    helper.rename(outside)
    helper.symlink_to(outside)
    _run(binary, tmp_path, root, inventory, 1)
    helper.unlink()
    outside.rename(helper)
    os.link(helper, tmp_path / "hardlink")
    _run(binary, tmp_path, root, inventory, 1)
    (tmp_path / "hardlink").unlink()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    _run(binary, tmp_path, alias, inventory, 1)
    inventory.write_text(original + original.splitlines(keepends=True)[1])
    _run(binary, tmp_path, root, inventory, 1)
    inventory.write_text(original.replace("\t22\t", "\t022\t"))
    _run(binary, tmp_path, root, inventory, 1)


def test_native_host_caps(tmp_path, native_host_tools):
    binary, _ = native_host_tools
    root, entries, inventory = _tree(tmp_path)
    for index in range(59):
        name = f"GUI/PoolhouseApproval.app/Contents/Resources/file{index:02d}"
        entries[name] = ("gui", 0o644)
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"")
    inventory.write_text(_inventory(root, entries))
    _run(binary, tmp_path, root, inventory)
    name = "GUI/PoolhouseApproval.app/Contents/Resources/overflow"
    entries[name] = ("gui", 0o644)
    (root / name).write_bytes(b"")
    inventory.write_text(_inventory(root, entries))
    _run(binary, tmp_path, root, inventory, 1)
    (root / name).unlink()
    entries.pop(name)
    helper = root / "Helper/PoolhouseAuthority"
    helper.write_bytes(b"x" * (64 * 1024 * 1024 - 4 * 22))
    inventory.write_text(_inventory(root, entries))
    _run(binary, tmp_path, root, inventory)
    with helper.open("ab") as output:
        output.write(b"x")
    inventory.write_text(_inventory(root, entries))
    _run(binary, tmp_path, root, inventory, 1)
    inventory.write_bytes(b"x" * 16385)
    _run(binary, tmp_path, root, inventory, 1)
