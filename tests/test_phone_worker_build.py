"""The phone worker's build: what its manifest lets reach it, and that only the pinned programs go into the APK.

The APK itself needs the Android SDK and is built by `app/phone-worker/build.py`; these checks need neither.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest
from defusedxml import ElementTree

ROOT = Path(__file__).resolve().parents[1]
ANDROID = "{http://schemas.android.com/apk/res/android}"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("phone_worker_build", ROOT / "app" / "phone-worker" / "build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the import system's own recipe: a dataclass looks its module up while it is made
    spec.loader.exec_module(module)
    return module


def manifest(build, release: bool) -> Any:
    return ElementTree.fromstring(build.manifest(release))


def services(found: Any) -> dict[str, tuple[str, str]]:
    return {s.get(ANDROID + "name"): (s.get(ANDROID + "exported"), s.get(ANDROID + "permission"))
            for s in found.iter("service")}


def test_only_an_app_signed_with_the_same_key_reaches_a_release_and_the_shell_only_a_debug_build(build):
    release, debug = manifest(build, True), manifest(build, False)
    assert release.get("package") == "app.poolhouse.worker" and debug.get("package") == "app.poolhouse.worker.nodetest"
    assert services(release) == {"app.poolhouse.worker.WorkerService": ("true", "app.poolhouse.worker.CONTROL")}
    assert services(debug) == {"app.poolhouse.worker.WorkerService": ("true", "app.poolhouse.worker.nodetest.CONTROL"),
                               "app.poolhouse.worker.DebugWorkerService": ("true", "android.permission.DUMP")}
    for found in (release, debug):
        (declared,) = found.findall("permission")
        assert declared.get(ANDROID + "protectionLevel") == "signature"
        (state,) = found.iter("provider")
        assert state.get(ANDROID + "authorities") == f"{found.get('package')}.state"
        assert state.get(ANDROID + "permission") == f"{found.get('package')}.CONTROL", "the state is for the app that holds the switch"
        for part in ("receiver", "activity"):
            assert not list(found.iter(part)), "nothing else is a way in"
    assert release.find("application").get(ANDROID + "debuggable") == "false"
    assert "DebugWorkerService.java" not in [p.name for p in build.sources(True)]
    assert "DebugWorkerService.java" in [p.name for p in build.sources(False)]


def test_the_app_asks_for_no_storage_keeps_no_backup_and_may_run_what_it_built(build):
    found = manifest(build, False)
    asked = {p.get(ANDROID + "name") for p in found.iter("uses-permission")}
    assert asked == {f"android.permission.{name}" for name in ("INTERNET", "ACCESS_NETWORK_STATE", "FOREGROUND_SERVICE", "WAKE_LOCK")}
    assert found.find("application").get(ANDROID + "allowBackup") == "false"
    assert found.find("uses-sdk").get(ANDROID + "targetSdkVersion") == "28", "29 and later may not execute the app's own files"


def package(files: dict[str, bytes]) -> bytes:
    """A Debian package (`ar` with a `data.tar.xz`) holding ``files`` under the Termux prefix."""
    inner = io.BytesIO()
    with tarfile.open(fileobj=inner, mode="w:xz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(f"./data/data/com.termux/files/usr/{name}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    parts = [b"!<arch>\n"]
    for name, content in (("debian-binary", b"2.0\n"), ("data.tar.xz", inner.getvalue())):
        parts.append(f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(content):<10}`\n".encode() + content + b"\n" * (len(content) % 2))
    return b"".join(parts)


def test_a_program_goes_in_only_from_the_pinned_package(build):
    data = package({"bin/proot": b"program", "bin/other": b"not wanted"})
    pinned = hashlib.sha256(data).hexdigest()
    assert build.package_files(data, pinned, {"bin/proot": "bin/proot"}) == {"bin/proot": b"program"}
    changed = package({"bin/proot": b"another program"})
    with pytest.raises(build.BuildError, match="pinned SHA-256"):
        build.package_files(changed, pinned, {"bin/proot": "bin/proot"})
    for path, digest, _ in build.PACKAGES:
        assert path.endswith("_aarch64.deb") and len(digest) == 64


def committed(root: Path, text: str) -> str:
    """Commit ``text`` as the one source file of the checkout at ``root``; the commit's id."""
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "-C", str(root)]
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "app").mkdir(exist_ok=True)
    (root / "src" / "one.py").write_text(text, encoding="utf-8")
    (root / "app" / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    if not (root / ".git").is_dir():
        subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*git, "add", "--all"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", text], check=True, capture_output=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_the_apk_carries_the_commit_it_is_built_from(build, tmp_path):
    commit = committed(tmp_path, "one = 1\n")
    (tmp_path / "src" / "one.py").write_text("uncommitted = True\n", encoding="utf-8")
    carried = build.tree_assets(tmp_path, "HEAD")
    assert carried["guest/commit"] == f"{commit}\n".encode()
    with tarfile.open(fileobj=io.BytesIO(carried["guest/tree.tgz"]), mode="r:gz") as archive:
        assert archive.extractfile("src/one.py").read() == b"one = 1\n"


def test_the_guest_makes_its_own_working_copy_and_node_with_no_computer_and_again_only_for_new_source(build, tmp_path):
    source, work, assets, tools = (tmp_path / name for name in ("source", "work", "assets", "tools"))
    for folder in (work, assets, tools):
        folder.mkdir()
    # Plumbing only: a stand-in `cargo` that records the build it was asked for and writes the file cargo would.
    (tools / "cargo").write_text('#!/bin/sh\necho "$@" >> "$WORK/builds"\nmkdir -p "$CARGO_TARGET_DIR/release"\n'
                                 'cp ../src/one.py "$CARGO_TARGET_DIR/release/poolhouse-node"\nchmod 755 "$CARGO_TARGET_DIR/release/poolhouse-node"\n',
                                 encoding="utf-8")
    (tools / "cargo").chmod(0o755)

    def prepare() -> None:
        env = {"PATH": f"{tools}:/usr/bin:/bin", "WORK": str(work), "ASSETS": str(assets), "HOME": str(tmp_path)}
        subprocess.run(["/bin/sh", str(ROOT / "app" / "phone-worker" / "guest" / "prepare.sh")], env=env, check=True, capture_output=True)

    def carry(text: str) -> str:
        commit = committed(source, text)
        for name, data in build.tree_assets(source, "HEAD").items():
            (assets / name.removeprefix("guest/")).write_bytes(data)
        return commit

    first = carry("one = 1\n")
    prepare()
    assert (work / "repo" / "src" / "one.py").read_text() == "one = 1\n" and (work / "poolhouse-node").read_text() == "one = 1\n"
    said = subprocess.run(["git", "-C", str(work / "repo"), "log", "--format=%s"], check=True, capture_output=True, text=True).stdout
    assert said.strip() == first, "the phone's own working copy names the commit it holds"
    prepare()
    assert len((work / "builds").read_text().splitlines()) == 1, "the same source is built once"
    carry("one = 2\n")
    prepare()
    assert (work / "poolhouse-node").read_text() == "one = 2\n"
    builds = (work / "builds").read_text().splitlines()
    assert len(builds) == 2 and all(line == "build --locked --release -j 3 -p poolhouse-node" for line in builds)


def test_the_guest_scripts_the_app_starts_are_the_ones_the_build_packs(build):
    java = "".join(path.read_text(encoding="utf-8") for path in build.sources(False))
    for name in ("setup.sh", "node.sh"):
        assert f"/opt/worker/{name}" in java and name in build.GUEST
    assert sorted(p.name for p in (ROOT / "app" / "phone-worker" / "guest").iterdir()) == sorted(build.GUEST)
