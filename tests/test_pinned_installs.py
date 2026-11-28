"""Everything fetched and then run is held to a version and a digest this project records: libraries to the hashed lock,
llama.cpp and its converter to the verified version. Every installer and download here is plumbing only."""

from __future__ import annotations

import argparse
import ast
import platform
import subprocess
import sys
import zipfile
from fnmatch import fnmatch
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import pinned, provision, safelaunch
from poolhouse.fleet import environment as environments
from poolhouse.serve import build_patches, build_platform, build_recover, build_release
from poolhouse.serve.build_paths import BuildFailed
from poolhouse.speech import asr, vad

SRC = Path(__file__).resolve().parent.parent / "src" / "poolhouse"
LOCK = """# PLUMBING ONLY: a lock of releases that do not exist
canned-dependency==2.0 \\
    --hash=sha256:{b}
    # via canned-root
canned-root==1.0 \\
    --hash=sha256:{a}
    # via poolhouse (pyproject.toml)
elsewhere==3.0 ; sys_platform == 'plan9' \\
    --hash=sha256:{c}
    # via
    #   canned-root
    #   unrelated
unrelated==4.0 \\
    --hash=sha256:{d}
""".format(a="a" * 64, b="b" * 64, c="c" * 64, d="d" * 64)


@pytest.fixture
def lock(tmp_path, monkeypatch):
    path = tmp_path / "requirements.lock"
    path.write_text(LOCK, encoding="utf-8")
    monkeypatch.setattr(pinned, "FILE", path)
    return path


def test_a_selection_is_the_requirement_and_what_it_depends_on_with_their_digests(lock):
    chosen = pinned.select(["canned-root>=1"])
    assert [one.name for one in chosen] == ["canned-dependency", "canned-root", "elsewhere"]
    assert [one.applies() for one in chosen] == [True, True, False]
    written = pinned.text(chosen)
    assert written.count("--hash=sha256:") == 3 and "unrelated" not in written and "#" not in written
    assert written.splitlines()[0].endswith("\\") and not written.splitlines()[-1].endswith("\\")


@pytest.mark.parametrize("asked", ["absent", "canned-root>=2", "canned-root @ https://example.invalid/x.whl"])
def test_what_the_lock_holds_no_digest_for_is_refused(lock, asked):
    with pytest.raises(pinned.Unpinned, match="not installed"):
        pinned.select([asked])


def test_a_lock_naming_a_release_without_a_digest_installs_nothing(lock):
    lock.write_text("canned-root==1.0\n", encoding="utf-8")
    with pytest.raises(pinned.Unpinned, match="digests"):
        pinned.entries()


def test_the_projects_lock_covers_every_extra_installed_at_first_use():
    held = pinned.entries()
    for extra in ("", *pinned.EXTRAS):
        asked = provision.requirements(extra)
        assert pinned.select(asked, held), extra
    assert all(any(line.lstrip().startswith("--hash=sha256:") for line in one.lines[1:]) for one in held)


def test_every_extra_the_product_installs_at_first_use_is_one_the_lock_covers():
    asked = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            named = ast.unparse(node.func)
            index = 0 if named.endswith("provision.ensure") else 1 if named.endswith("provision.module") else None
            if index is not None and len(node.args) > index and isinstance(node.args[index], ast.Constant):
                asked.add(node.args[index].value)
    assert len(asked) > 15 and asked <= set(pinned.EXTRAS), sorted(asked - set(pinned.EXTRAS))


def test_llama_cpp_is_downloaded_from_the_pinned_release_without_asking_for_the_newest(monkeypatch):
    monkeypatch.setattr(build_release, "_releases_for", lambda *a, **k: pytest.fail("asked GitHub what is newest"))
    (release,) = build_release._llama_releases()
    assert release["tag_name"] == "b11774" and release == build_patches.pinned_release()
    for one in release["assets"]:
        assert one["browser_download_url"] == f"{build_patches.RELEASES}/b11774/{one['name']}"
        assert build_patches.DIGEST.fullmatch(one["digest"]) and one["size"] > 0


def test_the_newest_llama_cpp_release_is_taken_only_when_asked_for_by_name(monkeypatch):
    monkeypatch.setattr(build_release, "_releases_for", lambda repo, **k: [{"tag_name": "newest", "assets": []}])
    assert build_release._llama_releases(newest=True)[0]["tag_name"] == "newest"


@pytest.mark.parametrize("newest", [False, True])
def test_only_the_pinned_llama_cpp_download_is_kept_where_there_is_no_virus_scanner(monkeypatch, newest):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    release = {"tag_name": "b1", "assets": [{"name": "llama-b1-bin-macos-arm64.tar.gz"}]}
    monkeypatch.setattr(build_release, "_llama_releases", lambda *a, **k: [release])
    asked = []

    def download(asset, into, **how):
        asked.append(how.get("pinned"))
        raise BuildFailed("plumbing only")

    monkeypatch.setattr(build_release.gh_updates, "download", download)
    with pytest.raises(BuildFailed):
        build_release.build_from_release(argparse.Namespace(force=True, upstream_head=newest))
    assert asked == [not newest]


@pytest.mark.parametrize("system,machine,tools", [("Darwin", "arm64", ()), ("Darwin", "x86_64", ()), ("Windows", "AMD64", ()),
                                                  ("Windows", "ARM64", ()), ("Windows", "AMD64", ("nvcc",)),
                                                  ("Windows", "AMD64", ("vulkaninfo",))])
def test_the_pinned_release_has_a_build_for_each_supported_machine(monkeypatch, system, machine, tools):
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setattr(safelaunch, "which", lambda name: name if name in tools else None)
    monkeypatch.delenv("VULKAN_SDK", raising=False)
    names = [one["name"] for one in build_patches.pinned_release()["assets"]]
    for pattern in build_release._release_asset_globs():
        assert [name for name in names if fnmatch(name, pattern)], pattern


@pytest.mark.parametrize("written", ['{"commit": "' + "a" * 40 + '"}',
                                     '{"commit": "' + "a" * 40 + '", "release": {"tag": "latest", "assets": {}}}',
                                     '{"commit": "' + "a" * 40 + '", "release": {"tag": "b1", "assets": '
                                     '{"x.zip": {"sha256": "short", "size": 1}}}}'])
def test_with_no_whole_pin_no_release_is_downloaded(monkeypatch, tmp_path, written):
    manifest = tmp_path / "verified.json"
    manifest.write_text(written, encoding="utf-8")
    monkeypatch.setattr(build_patches, "manifest_path", lambda: manifest)
    with pytest.raises(BuildFailed):
        build_patches.pinned_release()


def test_a_first_build_with_no_verified_commit_never_compiles_upstreams_newest(monkeypatch, tmp_path):
    monkeypatch.setattr(build_recover, "can_build_from_source", lambda: True)
    monkeypatch.setattr(build_recover, "verified_upstream", lambda: "")
    monkeypatch.setattr(build_recover, "build_from_source", lambda args: pytest.fail("built master's tip"))

    def release(args):
        raise BuildFailed("plumbing only")

    monkeypatch.setattr(build_recover, "build_from_release", release)
    with pytest.raises(BuildFailed, match="from the verified release"):
        build_recover._make(force=False)


@pytest.mark.parametrize("system", ["Darwin", "Windows"])
def test_a_source_build_downloads_no_web_page_nothing_pins(monkeypatch, system):
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(safelaunch, "which", lambda name: None)
    assert "-DLLAMA_USE_PREBUILT_UI=OFF" in build_platform.cmake_flags()


METADATA = """Metadata-Version: 2.1
Name: poolhouse
Version: 0.0.1
Requires-Dist: packaging>=24.2
Requires-Dist: numpy>=1.24; extra == 'graph'
Requires-Dist: poolhouse[graph]; extra == 'train'
Requires-Dist: pillow; extra == 'unasked'
"""


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.delenv(provision.SWITCH, raising=False)


class Installs(environments.Environment):
    """A job environment whose pip is recorded, with a plumbing-only app wheel: nothing is installed."""

    declared = METADATA

    def pip(self, args, *, timeout=3600.0, locked=None):
        self.asked = [*getattr(self, "asked", []), (args, locked.read_text() if locked else None)]
        return subprocess.CompletedProcess(args, 0, "", "")

    def wheels(self):
        made = Path(self.root) / "wheels"
        made.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(made / "poolhouse-0.0.1-py3-none-any.whl", "w") as archive:
            archive.writestr("poolhouse-0.0.1.dist-info/METADATA", self.declared)
        return made


def test_job_libraries_installed_at_need_are_what_the_app_wheel_declares_from_the_lock(allowed, tmp_path):
    environment = Installs(tmp_path)
    core = next(one for one in environments.CATALOG if one.name == "core")
    assert environment.locked_install(core).returncode == 0
    (first, written), (second, nothing) = environment.asked
    assert first == ["install"] and "--hash=sha256:" in written
    assert "numpy==" in written and "packaging==" in written and "pillow==" not in written and "poolhouse" not in written
    assert second == ["install", "--no-deps", "poolhouse"] and nothing is None


def test_a_job_library_the_app_wheel_asks_for_outside_the_lock_is_not_installed(allowed, tmp_path):
    environment = Installs(tmp_path)
    environment.declared = METADATA.replace("numpy>=1.24", "numpy<1")
    core = next(one for one in environments.CATALOG if one.name == "core")
    with pytest.raises(ValueError, match="not installed"):
        environment.locked_install(core)
    assert not hasattr(environment, "asked")


@pytest.mark.parametrize("library", [environments.Library("x", "X", "x", ("numpy",), index="https://example.invalid/simple"),
                                     environments.Library("y", "Y", "y", ("canned @ https://example.invalid/y.tar.gz",))])
def test_a_library_the_lock_cannot_hold_is_not_installed_at_need(allowed, tmp_path, library):
    environment = Installs(tmp_path)
    with pytest.raises((OSError, ValueError), match="not installed"):
        environment.locked_install(library)
    assert not hasattr(environment, "asked")


def test_the_voice_detector_is_fetched_and_run_at_one_commit(allowed, monkeypatch, tmp_path):
    asked = []
    hub = SimpleNamespace(load=lambda repo, name, **how: asked.append(repo) or (object(), None),   # plumbing only: loads nothing
                          get_dir=lambda: str(tmp_path))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(hub=hub))
    vad.SileroVAD().start()
    assert asked == ["snakers4/silero-vad:63d4a2a7bfce8cd9bb39ed8fa0afcb5236d73e01"]


@pytest.mark.parametrize("name,revision", [("small.en", "d1d751a5f8271d482d14ca55d9e2deeebbae577f"), ("somebody/chose-this", None)])
def test_the_default_speech_model_is_fetched_at_its_pinned_revision(monkeypatch, name, revision):
    loaded = []
    monkeypatch.setattr(provision, "module",
                        lambda *_: SimpleNamespace(WhisperModel=lambda model, **how: loaded.append(how) or argparse.Namespace()))
    asr.FasterWhisperASR(name)._load()
    assert loaded[0].get("revision") == revision
