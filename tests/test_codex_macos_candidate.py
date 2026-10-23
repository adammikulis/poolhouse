"""Native, inert package plumbing only: these payloads implement no model behavior."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "patches/codex/macos/prepare_candidate.py"


@pytest.fixture
def builder():
    if sys.platform != "darwin":
        pytest.skip("native macOS code signing")
    spec = importlib.util.spec_from_file_location("codex_macos_candidate", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inert(tmp_path_factory):
    root = tmp_path_factory.mktemp("codex-inert-native").resolve()
    source, executable = root / "inert.c", root / "inert"
    source.write_text("/* Inert package plumbing; no model behavior. */\nint main(void) { return 0; }\n")
    subprocess.run(["/usr/bin/clang", str(source), "-o", str(executable)], check=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(root), "TMPDIR": str(root)}, capture_output=True)
    return executable.read_bytes()


@pytest.fixture
def package(tmp_path, inert):
    root = (tmp_path / "canonical").resolve()
    for directory in ("bin", "codex-path", "codex-resources"):
        (root / directory).mkdir(parents=True)
    for name in ("bin/codex", "bin/codex-code-mode-host", "codex-path/rg"):
        path = root / name
        path.write_bytes(inert)
        path.chmod(0o755)
    target = "aarch64-apple-darwin" if os.uname().machine == "arm64" else "x86_64-apple-darwin"
    (root / "codex-package.json").write_text(json.dumps({"layoutVersion": 1, "version": "0.164.0",
        "target": target, "variant": "codex", "entrypoint": "bin/codex",
        "resourcesDir": "codex-resources", "pathDir": "codex-path"}))
    return root


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("resources", [True, False])
@pytest.mark.parametrize("variant", ["codex", "codex-app-server"])
def test_prepare_real_native_candidate(builder, package, tmp_path, resources, variant):
    if not resources:
        (package / "codex-resources").rmdir()
    manifest = json.loads((package / "codex-package.json").read_text())
    if variant != "codex":
        (package / "bin/codex").rename(package / "bin" / variant)
        manifest.update(variant=variant, entrypoint="bin/" + variant)
        (package / "codex-package.json").write_text(json.dumps(manifest))
    before = hashes(package)
    output = tmp_path.resolve() / "candidate"
    receipt = builder.prepare(package, output)
    assert hashes(package) == before
    assert receipt == json.loads((output / builder.RECEIPT).read_text())
    assert receipt["status"] == "UNTRUSTED build candidate"
    assert receipt["authentic_upstream"] is False and receipt["admission"] is False
    assert receipt["protected_custody"] is False
    assert int(receipt["signature"]["flags"], 16) & 0x10002 == 0x10002
    assert receipt["original_sha256"] == before
    for name, digest in before.items():
        assert receipt["candidate_sha256"][name] == hashlib.sha256((output / name).read_bytes()).hexdigest()
        if name != manifest["entrypoint"]:
            assert receipt["candidate_sha256"][name] == digest
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_nlink == 1 and not p.stat().st_mode & 0o077 for p in output.rglob("*") if p.is_file())


@pytest.mark.parametrize("attack", ["leaf-symlink", "directory-symlink", "root-symlink", "hardlink", "extra", "fifo", "manifest-escape", "wrong-architecture", "writable"])
def test_hostile_package_refused_before_output(builder, package, tmp_path, attack):
    source = package
    payload = package / "bin/codex"
    if attack == "leaf-symlink":
        elsewhere = tmp_path / "elsewhere"
        payload.rename(elsewhere)
        payload.symlink_to(elsewhere)
    elif attack == "directory-symlink":
        moved = tmp_path / "moved"
        (package / "bin").rename(moved)
        (package / "bin").symlink_to(moved, target_is_directory=True)
    elif attack == "root-symlink":
        source = tmp_path / "alias"
        source.symlink_to(package, target_is_directory=True)
    elif attack == "hardlink":
        os.link(payload, tmp_path / "alias")
    elif attack == "extra":
        (package / "extra").write_bytes(b"untrusted extra")
    elif attack == "fifo":
        payload.unlink()
        os.mkfifo(payload)
    elif attack in ("manifest-escape", "wrong-architecture"):
        path = package / "codex-package.json"
        manifest = json.loads(path.read_text())
        manifest["entrypoint" if attack == "manifest-escape" else "target"] = "../../outside" if attack == "manifest-escape" else "unsupported"
        path.write_text(json.dumps(manifest))
    else:
        payload.chmod(0o777)
    output = tmp_path.resolve() / "candidate"
    with pytest.raises((OSError, ValueError)):
        builder.prepare(source, output)
    assert not output.exists()


@pytest.mark.parametrize("attack", ["exists", "inside-source", "output-parent-symlink"])
def test_output_aliases_refused(builder, package, tmp_path, attack):
    output = tmp_path.resolve() / "candidate"
    if attack == "exists":
        output.mkdir()
    elif attack == "inside-source":
        output = package / "candidate"
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        output = alias / "candidate"
    before = hashes(package)
    with pytest.raises((OSError, ValueError)):
        builder.prepare(package, output)
    assert hashes(package) == before


def test_signing_failure_cleans_only_new_output(builder, package, tmp_path, monkeypatch):
    before = hashes(package)
    def refuse(_binary):
        raise ValueError("synthetic signing refusal")
    monkeypatch.setattr(builder, "_sign", refuse)
    output = tmp_path.resolve() / "candidate"
    with pytest.raises(ValueError, match="signing refusal"):
        builder.prepare(package, output)
    assert not output.exists() and hashes(package) == before


def test_concurrent_source_change_refuses_receipt(builder, package, tmp_path, monkeypatch):
    sign = builder._sign
    def mutate(binary):
        result = sign(binary)
        (package / "codex-path/rg").write_bytes(b"changed by another writer")
        return result
    monkeypatch.setattr(builder, "_sign", mutate)
    output = tmp_path.resolve() / "candidate"
    with pytest.raises(ValueError, match="source changed"):
        builder.prepare(package, output)
    assert not output.exists()


def test_nonproducer_change_refuses_receipt(builder, package, tmp_path, monkeypatch):
    sign = builder._sign
    def mutate(binary):
        result = sign(binary)
        (binary.parent / "codex-code-mode-host").write_bytes(b"changed nonproducer")
        return result
    monkeypatch.setattr(builder, "_sign", mutate)
    output = tmp_path.resolve() / "candidate"
    with pytest.raises(ValueError, match="nonproducer payload changed"):
        builder.prepare(package, output)
    assert not output.exists()


def test_existing_entitlements_require_review(builder, package, tmp_path):
    entitlement = tmp_path / "entitlements.plist"
    entitlement.write_text('<?xml version="1.0"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict><key>com.apple.security.cs.allow-jit</key><true/></dict></plist>')
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--entitlements", str(entitlement), str(package / "bin/codex")],
                   check=True, capture_output=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    before = hashes(package)
    with pytest.raises(ValueError, match="entitlement policy"):
        builder.prepare(package, tmp_path.resolve() / "candidate")
    assert hashes(package) == before


def test_codesign_environment_is_fixed(builder, package, tmp_path, monkeypatch):
    real = builder.subprocess.Popen
    seen = []
    def guarded(arguments, **kwargs):
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "LANG": "C"}
        assert arguments[0] in ("/usr/bin/codesign", "/bin/chmod")
        seen.append(arguments)
        return real(arguments, **kwargs)
    monkeypatch.setenv("CODESIGN_ALLOCATE", str(tmp_path / "foreign-tool"))
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", str(tmp_path / "foreign-library"))
    monkeypatch.setattr(builder.subprocess, "Popen", guarded)
    builder.prepare(package, tmp_path.resolve() / "candidate")
    signing = [args for args in seen if "--sign" in args]
    assert len(signing) == 1
    assert signing[0][:-1] == ["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--timestamp=none"]
    assert signing[0][-1].endswith("candidate/bin/codex")
