"""Prepare an UNTRUSTED macOS producer build; never install, start or admit it."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import functools
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import runpy
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time

NOFOLLOW = os.O_NOFOLLOW | os.O_CLOEXEC
TARGETS = {"arm64": ("aarch64-apple-darwin", 0x100000C, 0), "x86_64": ("x86_64-apple-darwin", 0x1000007, 3)}
RECEIPT = "poolhouse-untrusted-build.json"
ROOT = Path(__file__).resolve().parents[3]
unique_object = runpy.run_path(str(ROOT / "src/poolhouse/json_objects.py"))["unique_object"]


@functools.lru_cache(maxsize=1)
def _acl_guard():
    namespace = Path("/private/tmp")
    info = namespace.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or not info.st_mode & stat.S_ISVTX:
        raise ValueError("native validation staging namespace is unsupported")
    temporary = tempfile.TemporaryDirectory(prefix="poolhouse-codex-acl-", dir=namespace)
    directory = Path(temporary.name)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW)
    try:
        staged = os.fstat(descriptor)
        if staged.st_uid != os.getuid() or stat.S_IMODE(staged.st_mode) != 0o700:
            raise ValueError("native validation staging is not private")
        _tool(["/bin/chmod", "-N", str(directory)])
        wrapper, library = directory / "guard.c", directory / "guard.dylib"
        wrapper.write_text('#include "Capture.c"\nbool ph_candidate_acl(int fd) { return clean_acl(fd); }\n')
        _tool(["/usr/bin/clang", "-dynamiclib", "-I", str(ROOT / "app/native-host"), str(wrapper), "-o", str(library)])
        library.chmod(0o700)
        named = directory.lstat()
        if (named.st_dev, named.st_ino, named.st_uid, stat.S_IMODE(named.st_mode)) != (staged.st_dev, staged.st_ino, os.getuid(), 0o700):
            raise ValueError("native validation staging identity changed")
        with os.fdopen(os.open(library, os.O_RDONLY | NOFOLLOW), "rb") as held:
            before = os.fstat(held.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o700:
                raise ValueError("native validator is not a private single file")
            if _fingerprint(library.lstat()) != _fingerprint(before):
                raise ValueError("native validator name changed")
            guard = ctypes.CDLL(str(library))
            if _fingerprint(library.lstat()) != _fingerprint(before) or _fingerprint(os.fstat(held.fileno())) != _fingerprint(before):
                raise ValueError("native validator changed while loading")
        guard.ph_candidate_acl.argtypes = [ctypes.c_int]
        guard.ph_candidate_acl.restype = ctypes.c_bool
        guard.temporary = temporary
        return guard
    finally:
        os.close(descriptor)


def _clean_acl(descriptor):
    if not _acl_guard().ph_candidate_acl(descriptor):
        raise ValueError("ACL grants mutation or cannot be verified")


def _path(value: Path) -> Path:
    path = Path(value).absolute()
    if ".." in path.parts:
        raise ValueError("parent traversal is unsupported")
    return path


@contextlib.contextmanager
def _directory(path: Path):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW)
    try:
        _clean_acl(descriptor)
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            _clean_acl(descriptor)
            info = os.fstat(descriptor)
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
                raise ValueError("directory ancestry is writable by another owner")
        yield descriptor
    finally:
        os.close(descriptor)


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid, info.st_nlink,
            info.st_mtime_ns, info.st_ctime_ns)


def _read(descriptor, destination=None):
    _clean_acl(descriptor)
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid() or before.st_mode & 0o022:
        raise ValueError("payload must be an owned, singly linked regular file")
    digest, header = hashlib.sha256(), b""
    while block := os.read(descriptor, 1024 * 1024):
        header = (header + block[:32])[:32]
        digest.update(block)
        if destination is not None:
            destination.write(block)
    if _fingerprint(before) != _fingerprint(os.fstat(descriptor)):
        raise ValueError("source changed while reading")
    return {"sha256": digest.hexdigest(), "identity": _fingerprint(before), "header": header.hex()}


def _scan(root: Path):
    entries = {}

    def visit(descriptor, prefix):
        _clean_acl(descriptor)
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("package directories must be owned and private from other writers")
        entries[prefix] = {"directory": _fingerprint(info)}
        for name in sorted(os.listdir(descriptor)):
            relative = f"{prefix}/{name}" if prefix else name
            allowed = {"bin", "codex-path", "codex-resources", "codex-package.json", "bin/codex", "bin/codex-app-server", "bin/codex-code-mode-host", "codex-path/rg"}
            if relative not in allowed:
                raise ValueError("unsupported package member")
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=descriptor)
                try:
                    visit(child, relative)
                finally:
                    os.close(child)
            else:
                child = os.open(name, os.O_RDONLY | os.O_NONBLOCK | NOFOLLOW, dir_fd=descriptor)
                try:
                    entries[relative] = _read(child)
                finally:
                    os.close(child)
    with _directory(root) as descriptor:
        visit(descriptor, "")
    return entries


def _open_file(root, relative):
    with _directory(root / Path(relative).parent) as parent:
        return os.open(Path(relative).name, os.O_RDONLY | os.O_NONBLOCK | NOFOLLOW, dir_fd=parent)


def _manifest(root, entries):
    if "codex-package.json" not in entries or "sha256" not in entries["codex-package.json"]:
        raise ValueError("canonical package manifest missing")
    if entries["codex-package.json"]["identity"][2] > 16384:
        raise ValueError("package manifest is too large")
    descriptor = _open_file(root, "codex-package.json")
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(16385)
    if hashlib.sha256(raw).hexdigest() != entries["codex-package.json"]["sha256"]:
        raise ValueError("manifest changed")
    manifest = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(manifest, dict):
        raise ValueError("package manifest must be an object")
    variant = manifest.get("variant")
    if variant not in ("codex", "codex-app-server"):
        raise ValueError("unsupported producer variant")
    target, cpu, subtype = TARGETS[platform.machine()]
    expected = {"layoutVersion": 1, "version": manifest.get("version"), "target": target,
                "variant": variant, "entrypoint": f"bin/{variant}",
                "resourcesDir": "codex-resources", "pathDir": "codex-path"}
    if manifest != expected or type(manifest["layoutVersion"]) is not int or not isinstance(manifest["version"], str):
        raise ValueError("unsupported canonical package manifest")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", manifest["version"]):
        raise ValueError("unsupported package version")
    payloads = {"codex-package.json", f"bin/{variant}", "bin/codex-code-mode-host", "codex-path/rg"}
    directories = {name for name, data in entries.items() if "directory" in data}
    if set(entries) - directories != payloads or directories not in ({"", "bin", "codex-path"}, {"", "bin", "codex-path", "codex-resources"}):
        raise ValueError("unsupported package layout")
    for name in payloads - {"codex-package.json"}:
        data = entries[name]
        header = bytes.fromhex(data["header"])
        if len(header) < 16 or struct.unpack("<4I", header[:16]) != (0xFEEDFACF, cpu, subtype, 2):
            raise ValueError("unsupported canonical Mach-O architecture")
        if not data["identity"][3] & 0o100:
            raise ValueError("package executable is not a native executable")

    return manifest


def _only_unreaped_leader(group):
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
    library.proc_listpids.restype = ctypes.c_int
    members = (ctypes.c_int * 128)()
    count = library.proc_listpids(2, group, members, ctypes.sizeof(members))
    if count <= 0 or count >= ctypes.sizeof(members) or count % ctypes.sizeof(ctypes.c_int):
        return False
    return set(members[:count // ctypes.sizeof(ctypes.c_int)]) <= {0, group}


def _tool(arguments):
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C"}
    proc = subprocess.Popen(arguments, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector, output = selectors.DefaultSelector(), {"stdout": bytearray(), "stderr": bytearray()}
    completed = None
    try:
        for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        deadline = time.monotonic() + 15
        while selector.get_map():
            if time.monotonic() > deadline:
                raise ValueError("macOS build tool did not finish")
            for key, _ in selector.select(.05):
                block = os.read(key.fd, 4096)
                if not block:
                    selector.unregister(key.fileobj)
                else:
                    output[key.data].extend(block)
                    if len(output[key.data]) > 32768:
                        raise ValueError("macOS build tool output exceeded its bound")
        completed = None
        while completed is None and time.monotonic() < deadline:
            completed = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            if completed is None:
                time.sleep(.01)
        if completed is None or completed.si_code != os.CLD_EXITED or completed.si_status != 0:
            raise ValueError("macOS build tool refused candidate")
        return {name: bytes(value) for name, value in output.items()}
    finally:
        # WNOWAIT above keeps the leader PID reserved until its owned group is closed.
        try:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # Darwin rejects signaling a group containing only its reserved zombie leader.
                if completed is None or not _only_unreaped_leader(proc.pid):
                    raise
        finally:
            try:
                proc.wait(timeout=2)
            finally:
                selector.close()
                proc.stdout.close()
                proc.stderr.close()


def _entitlements(binary):
    result = _tool(["/usr/bin/codesign", "-d", "--entitlements", ":-", str(binary)])
    data = result["stdout"].strip()
    if not data:
        return "absent document"
    if plistlib.loads(data) != {}:
        raise ValueError("producer entitlement policy requires separate review")
    return "parsed empty dictionary"


def _sign(binary):
    _tool(["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--timestamp=none", str(binary)])
    _tool(["/usr/bin/codesign", "--verify", "--strict", str(binary)])
    details = _tool(["/usr/bin/codesign", "-d", "--verbose=4", str(binary)])
    flags = re.search(rb"flags=(0x[0-9a-fA-F]+)", details["stderr"])
    if flags is None or int(flags.group(1), 16) & 0x10002 != 0x10002:
        raise ValueError("ad-hoc hardened runtime signature missing")
    return {"flags": flags.group(1).decode(), "entitlements": _entitlements(binary)}


def _same_directory(path, descriptor):
    with _directory(path) as named:
        current, captured = os.fstat(named), os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (captured.st_dev, captured.st_ino):
            raise ValueError("captured directory lineage changed")


def prepare(package: Path, output: Path) -> dict:
    """Copy a canonical package into an absent output and return its UNTRUSTED build receipt."""
    if platform.system() != "Darwin" or platform.machine() not in TARGETS:
        raise ValueError("supported native macOS architecture required")
    package, output = _path(package), _path(output)
    if package == output or package in output.parents or output in package.parents:
        raise ValueError("source and output overlap")
    with _directory(output.parent) as parent_fd, _directory(package) as source_fd:
        if os.path.lexists(output):
            raise ValueError("output already exists")
        return _prepare(package, output, parent_fd, source_fd)


def _prepare(package, output, parent_fd, source_fd):
    original = _scan(package)
    manifest = _manifest(package, original)
    producer = manifest["entrypoint"]
    original_entitlements = _entitlements(package / producer)
    if _scan(package) != original:
        raise ValueError("source changed before copying")
    os.mkdir(output.name, mode=0o700, dir_fd=parent_fd)
    owned = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
    stack = contextlib.ExitStack()
    try:
        _same_directory(output.parent, parent_fd)
        _tool(["/bin/chmod", "-N", str(output)])
        output_fd = stack.enter_context(_directory(output))
        for name, data in sorted(original.items()):
            if not name:
                continue
            destination = output / name
            if "directory" in data:
                destination.mkdir(mode=0o700)
                continue
            descriptor = _open_file(package, name)
            try:
                with destination.open("xb") as stream:
                    destination.chmod(0o700 if name != "codex-package.json" else 0o600)
                    copied = _read(descriptor, stream)
                if copied != data:
                    raise ValueError("source changed during copy")
            finally:
                os.close(descriptor)
        _same_directory(package, source_fd)
        _same_directory(output.parent, parent_fd)
        _same_directory(output, output_fd)
        with os.fdopen(_open_file(output, producer), "rb") as held:
            _clean_acl(held.fileno())
            captured = _read(held.fileno())
            named = os.stat(output / producer, follow_symlinks=False)
            if captured["sha256"] != original[producer]["sha256"] or (named.st_dev, named.st_ino) != captured["identity"][:2]:
                raise ValueError("copied producer changed before signing")
            # codesign atomically replaces this file; its captured parent/root must stay bound.
            signature = _sign(output / producer)
        _same_directory(package, source_fd)
        _same_directory(output.parent, parent_fd)
        _same_directory(output, output_fd)
        candidate = _scan(output)
        if _manifest(output, candidate) != manifest:
            raise ValueError("candidate package metadata changed")
        before = {name: data["sha256"] for name, data in original.items() if "sha256" in data}
        after = {name: data["sha256"] for name, data in candidate.items() if "sha256" in data}
        if set(candidate) != set(original) or set(before) != set(after) or any(after[name] != digest for name, digest in before.items() if name != producer):
            raise ValueError("nonproducer payload changed")
        if _scan(package) != original:
            raise ValueError("source changed while preparing candidate")
        receipt = {"status": "UNTRUSTED build candidate", "authentic_upstream": False, "admission": False, "protected_custody": False,
                   "scope": "No installation, startup, trust or H/K/P acceptance", "manifest": manifest,
                   "original_sha256": before, "candidate_sha256": after,
                   "original_entitlements": original_entitlements, "signature": signature}
        with (output / RECEIPT).open("x") as stream:
            os.chmod(stream.fileno(), 0o600)
            json.dump(receipt, stream, indent=2)
        return receipt
    except BaseException:
        try:
            current = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
            shutil.rmtree(output.name, dir_fd=parent_fd)
        raise
    finally:
        stack.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    sys.path.insert(0, str(ROOT / "scripts"))
    import testslots

    try:
        with testslots.lease(1, label="macOS Codex candidate preparation"):
            print(json.dumps(prepare(arguments.package, arguments.output)))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        parser.exit(1, "Cannot prepare this untrusted macOS build candidate.\n")


if __name__ == "__main__":
    main()
