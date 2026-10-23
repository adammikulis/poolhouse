"""Prepare an UNTRUSTED macOS producer build; never install, start or admit it."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import selectors
import shutil
import stat
import struct
import subprocess
import time

NOFOLLOW = os.O_NOFOLLOW | os.O_CLOEXEC
TARGETS = {"arm64": ("aarch64-apple-darwin", 0x100000C), "x86_64": ("x86_64-apple-darwin", 0x1000007)}
RECEIPT = "poolhouse-untrusted-build.json"


def _path(value: Path) -> Path:
    path = Path(value).absolute()
    if ".." in path.parts:
        raise ValueError("parent traversal is unsupported")
    return path


@contextlib.contextmanager
def _directory(path: Path):
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
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
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("package manifest must be an object")
    variant = manifest.get("variant")
    if variant not in ("codex", "codex-app-server"):
        raise ValueError("unsupported producer variant")
    target, cpu = TARGETS[platform.machine()]
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
        if len(bytes.fromhex(data["header"])) < 16 or struct.unpack("<4I", bytes.fromhex(data["header"])[:16])[:2] != (0xFEEDFACF, cpu):
            raise ValueError("unsupported Mach-O architecture")
        if struct.unpack("<4I", bytes.fromhex(data["header"])[:16])[3] != 2 or not data["identity"][3] & 0o100:
            raise ValueError("package executable is not a native executable")
    return manifest


def _tool(arguments):
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C"}
    proc = subprocess.Popen(arguments, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    selector, output = selectors.DefaultSelector(), {"stdout": bytearray(), "stderr": bytearray()}
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
        if proc.wait(timeout=max(.01, deadline - time.monotonic())):
            raise ValueError("macOS build tool refused candidate")
        return {name: bytes(value) for name, value in output.items()}
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
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


def prepare(package: Path, output: Path) -> dict:
    """Copy a canonical package into an absent output and return its UNTRUSTED build receipt."""
    if platform.system() != "Darwin" or platform.machine() not in TARGETS:
        raise ValueError("supported native macOS architecture required")
    package, output = _path(package), _path(output)
    if package == output or package in output.parents or output in package.parents:
        raise ValueError("source and output overlap")
    with _directory(output.parent):
        if os.path.lexists(output):
            raise ValueError("output already exists")
    original = _scan(package)
    manifest = _manifest(package, original)
    producer = manifest["entrypoint"]
    original_entitlements = _entitlements(package / producer)
    if _scan(package) != original:
        raise ValueError("source changed before copying")
    output.mkdir(mode=0o700)
    owned = output.stat()
    try:
        _tool(["/bin/chmod", "-N", str(output)])
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
        signature = _sign(output / producer)
        candidate = _scan(output)
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
            current = output.lstat()
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
            shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    try:
        print(json.dumps(prepare(arguments.package, arguments.output)))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        parser.exit(1, "Cannot prepare this untrusted macOS build candidate.\n")


if __name__ == "__main__":
    main()
