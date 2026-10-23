"""Native, inert package plumbing only: these payloads implement no model behavior."""
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "patches/codex/macos/prepare_candidate.py"


@pytest.fixture(scope="module")
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
    try:
        yield root
    finally:
        # Only this test-created package: remove deliberately hostile ACLs before pytest cleanup.
        subprocess.run(["/bin/chmod", "-RN", str(root)], check=True, capture_output=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})


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


@pytest.mark.parametrize("attack", ["leaf-symlink", "directory-symlink", "root-symlink", "hardlink", "extra", "fifo", "manifest-escape", "wrong-architecture", "cpu-subtype", "writable"])
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
    elif attack == "cpu-subtype":
        data = bytearray(payload.read_bytes())
        struct.pack_into("<I", data, 8, 2)
        payload.write_bytes(data)
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
    with pytest.raises(ValueError):
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
        assert arguments[0] in ("/usr/bin/codesign", "/bin/chmod", "/usr/bin/clang")
        assert kwargs["start_new_session"] is True
        seen.append(arguments)
        return real(arguments, **kwargs)
    with monkeypatch.context() as patch:
        patch.setenv("CODESIGN_ALLOCATE", str(tmp_path / "foreign-tool"))
        patch.setenv("DYLD_INSERT_LIBRARIES", str(tmp_path / "foreign-library"))
        patch.setattr(builder.subprocess, "Popen", guarded)
        builder.prepare(package, tmp_path.resolve() / "candidate")
    signing = [args for args in seen if "--sign" in args]
    assert len(signing) == 1
    assert signing[0][:-1] == ["/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--timestamp=none"]
    assert signing[0][-1].endswith("candidate/bin/codex")


def test_duplicate_manifest_members_refused(builder, package, tmp_path):
    manifest = package / "codex-package.json"
    text = manifest.read_text()
    manifest.write_text(text[:-1] + ',"entrypoint":"bin/codex"}')
    before = hashes(package)
    with pytest.raises(ValueError, match="duplicate JSON"):
        builder.prepare(package, tmp_path.resolve() / "candidate")
    assert hashes(package) == before


def acl(path, entry, tmp_path):
    subprocess.run(["/bin/chmod", "+a", entry, str(path)], check=True, capture_output=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})


@pytest.mark.parametrize("location", ["output-parent", "source-directory", "source-file"])
def test_mutation_acl_refused_before_sign(builder, package, tmp_path, monkeypatch, location):
    parent = tmp_path.resolve() / "parent"
    parent.mkdir(mode=0o755)
    attacked = {"output-parent": parent, "source-directory": package / "bin", "source-file": package / "bin/codex"}[location]
    permission = "everyone allow writesecurity" if location == "source-file" else "everyone allow add_subdirectory,add_file,delete_child"
    acl(attacked, permission, tmp_path)
    assert attacked.stat().st_mode & 0o777 == 0o755
    before = hashes(package)
    called = []
    monkeypatch.setattr(builder, "_sign", lambda binary: called.append(binary))
    with pytest.raises(ValueError, match="ACL"):
        builder.prepare(package, parent / "candidate")
    assert not called and hashes(package) == before and not (parent / "candidate").exists()


def test_readonly_and_deny_acls_remain_supported(builder, package, tmp_path):
    parent = tmp_path.resolve() / "readonly-parent"
    parent.mkdir(mode=0o755)
    acl(parent, "everyone allow readattr,readextattr,readsecurity", tmp_path)
    acl(package / "bin/codex", "everyone deny delete", tmp_path)
    acl(package / "codex-path/rg", "everyone allow read,readattr,readsecurity", tmp_path)
    assert builder.prepare(package, parent / "candidate")["admission"] is False


def test_owned_tool_group_closes_descendant(builder, tmp_path):
    import psutil

    child_pid = tmp_path / "child-pid"
    code = ('import pathlib,subprocess,sys;'
            'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],'
            'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);'
            'pathlib.Path(sys.argv[1]).write_text(str(child.pid))')
    builder._tool([sys.executable, "-c", code, str(child_pid)])
    pid = int(child_pid.read_text())
    for _ in range(20):
        if not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
            break
        time.sleep(.01)
    else:
        pytest.fail("owned build-tool descendant survived cleanup")
