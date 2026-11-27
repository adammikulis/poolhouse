"""A tool, build or runtime that is missing is built or downloaded the first time it is needed, and reused after. Every source
here is plumbing only: a canned server, a marked file in place of a release, a recorded call in place of a download."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import codex_install, harnessing, opencode_install, provision
from poolhouse.decide import router
from poolhouse.fleet import environment as environments, updates
from poolhouse.gguf import tools
from poolhouse.net import git as netgit
from poolhouse.serve import binary, build_platform, build_recover, build_release
from poolhouse.serve.build_paths import builds_dir
from poolhouse.serve.policy import LocalPolicy
from poolhouse.speech import asr
from poolhouse.testing.canned import canned_binary

browser = importlib.import_module("poolhouse.scrape.browser")  # the package exports a function of the same name
MARKED = "# PLUMBING ONLY: stands where a download would be\n"


@pytest.fixture
def switched_on(monkeypatch):
    monkeypatch.delenv(provision.SWITCH, raising=False)
    for name in ("LLAMA_CPP_SERVER", "LLAMA_CPP_DIR", "LLAMA_CPP_ROOT", "POOLHOUSE_LLAMA_BUILD"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def releases(monkeypatch):
    """A canned release in the place of llama.cpp's: it holds a server that only answers, and a marked quantiser."""
    made = []

    def release(args):
        dest = builds_dir() / "b9999"
        if not (dest / "llama-server").is_file():
            made.append(args.force)
            dest.mkdir(parents=True, exist_ok=True)
            canned_binary(dest, name="llama-server")
            (dest / "llama-quantize").write_text(MARKED)
            (dest / "BUILD.json").write_text('{"commit": "b9999", "source": "release"}')
        return dest, "b9999"

    monkeypatch.setattr(build_recover, "can_build_from_source", lambda: False)
    monkeypatch.setattr(build_recover, "build_from_release", release)
    monkeypatch.setattr(build_release, "build_from_release", release)
    return made


def test_a_device_with_no_llama_server_gets_one_the_first_time_and_keeps_it(switched_on, releases, tmp_path, capsys):
    policy = LocalPolicy(tmp_path / "pins.json")
    first = binary.require_binary(policy=policy, strict=True)
    assert first == (builds_dir() / "b9999" / "llama-server").resolve()
    assert binary.require_binary(policy=policy, strict=True) == first and releases == [False]
    assert capsys.readouterr().out == "", "what the build says goes to standard error"


def test_a_build_that_may_not_be_fetched_says_why(releases, monkeypatch):
    for name in ("LLAMA_CPP_SERVER", "LLAMA_CPP_DIR", "POOLHOUSE_LLAMA_BUILD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(binary.BinaryNotFound, match=provision.SWITCH):
        binary.require_binary()
    assert releases == []


def test_a_named_path_that_is_missing_is_not_stood_in_for(switched_on, releases, tmp_path):
    with pytest.raises(binary.BinaryNotFound, match="no path is named"):
        binary.require_binary(explicit=tmp_path / "absent")
    assert releases == []


def test_a_missing_quantiser_comes_with_a_release_downloaded_once(switched_on, releases, monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "source_dirs", tuple)
    monkeypatch.setenv("PATH", str(tmp_path))
    first = tools.require_quantize()
    assert first == (builds_dir() / "b9999" / "llama-quantize").resolve()
    assert tools.require_quantize() == first and releases == [False]
    first.unlink()
    with pytest.raises(tools.ToolNotFound, match="nothing was fetched"):
        tools.require_quantize(tmp_path / "x")
    assert releases == [False]


def test_a_missing_converter_is_cloned_whole_the_first_time(switched_on, monkeypatch, tmp_path):
    monkeypatch.setenv("POOLHOUSE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(tools, "source_dirs", lambda: (tools.llama_cpp_src(),))
    cloned = []

    def clone(url, dest, **kw):
        cloned.append(dest)
        dest.mkdir(parents=True)
        (dest / tools.CONVERTER_NAME).write_text(MARKED)

    monkeypatch.setattr(netgit, "clone", clone)
    found = tools.require_converter()
    assert found == (tools.llama_cpp_src() / tools.CONVERTER_NAME).resolve() and tools.require_converter() == found
    assert [path.name for path in cloned] == ["llama.cpp-src.part"], "cloned once, beside where it ends up"


def test_a_cmake_on_path_is_used_and_a_missing_one_is_installed(monkeypatch, tmp_path):
    assert build_platform.cmake_program("/usr/bin/cmake") == "/usr/bin/cmake"
    monkeypatch.setattr(build_platform.shutil, "which", lambda name: None)
    asked = []
    monkeypatch.setattr(provision, "module", lambda name, extra: asked.append((name, extra))
                        or SimpleNamespace(CMAKE_BIN_DIR=str(tmp_path)))
    assert Path(build_platform.cmake_program()).parent == tmp_path and asked == [("cmake", "llama-build")]


class Managed:
    """A job environment's two doors, recorded: plumbing only, nothing is installed."""

    def __init__(self, root, works=True):
        self.root, self.works, self.installed, self.ready_now = root, works, [], False

    def install(self, names, *, on_progress=None):
        self.installed.append(names)
        self.ready_now = self.works
        return {name: {"ok": self.works, "error": "" if self.works else "no space"} for name in names}

    def ready(self):
        if not self.ready_now:
            raise OSError("the libraries are not in the managed environment")


def test_job_libraries_that_are_missing_are_installed_once(switched_on, tmp_path):
    managed = Managed(tmp_path)
    for _ in range(2):
        environments.provide(managed, ["core", "serve-mlx"], managed.ready)
    assert managed.installed == [["core", "serve-mlx"]]


def test_job_libraries_that_cannot_be_installed_say_why(switched_on, tmp_path):
    with pytest.raises(OSError, match="no space"):
        environments.provide(Managed(tmp_path, works=False), ["core"], Managed(tmp_path).ready)


def test_job_libraries_are_not_installed_when_switched_off(tmp_path):
    managed = Managed(tmp_path)
    with pytest.raises(OSError, match="not in the managed environment.*" + provision.SWITCH):
        environments.provide(managed, ["core"], managed.ready)
    assert managed.installed == []


def test_codex_is_fetched_once_at_its_pinned_release_and_digest(switched_on, monkeypatch):
    kept = []

    def keep(url, want, member, target, *, sha256=""):
        kept.append((url, want, sha256))
        target.write_text(MARKED)

    monkeypatch.setattr(opencode_install, "keep", keep)
    monkeypatch.setattr(codex_install.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(codex_install.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(harnessing.shutil, "which", lambda name: None)
    first = harnessing.installed_binary("codex")
    assert first == codex_install.kept() != "" and harnessing.installed_binary("codex") == first
    ((url, want, digest),) = kept
    assert url.endswith(codex_install.VERSION) and want == "codex-aarch64-apple-darwin.tar.gz" and len(digest) == 64
    assert all(len(digest) == 64 and int(digest, 16) >= 0 for _, _, digest in codex_install.ASSETS.values())


def test_codex_switched_off_is_not_fetched_and_pi_is_not_one_poolhouse_installs(monkeypatch):
    monkeypatch.setattr(harnessing.shutil, "which", lambda name: None)
    monkeypatch.setattr(opencode_install, "keep", lambda *a, **k: pytest.fail("downloaded"))
    with pytest.raises(opencode_install.InstallFailed, match=provision.SWITCH):
        harnessing.installed_binary("codex")
    assert harnessing.installed_binary("pi") == "" and not harnessing.installable("pi")


def test_a_pinned_digest_is_what_the_download_is_held_to(monkeypatch, tmp_path):
    release = {"assets": [{"name": "a.tar.gz", "digest": "sha256:" + "0" * 64}]}
    monkeypatch.setattr(opencode_install.net, "default", lambda: SimpleNamespace(json=lambda url, ask: release))
    held = []

    def download(asset, into):
        held.append(asset["digest"])
        raise updates.UpdateError("stopped before any bytes: plumbing only")

    monkeypatch.setattr(updates, "download", download)
    with pytest.raises(opencode_install.InstallFailed):
        opencode_install.keep("https://api.github.com/x", "a.tar.gz", "a", tmp_path / "a", sha256="1" * 64)
    assert held == ["sha256:" + "1" * 64]


def test_audio_uses_the_ffmpeg_on_path_else_installs_one(monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", lambda name: None)
    monkeypatch.setattr(provision, "module", lambda name, extra: SimpleNamespace(get_ffmpeg_exe=lambda: f"/kept/{extra}"))
    assert asr.ffmpeg_program() == "/kept/ffmpeg"


def test_audio_with_no_ffmpeg_to_be_had_says_why(monkeypatch):
    monkeypatch.setattr(asr.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    with pytest.raises(asr.AudioConversionError, match=provision.SWITCH):
        asr.ffmpeg_program()


@pytest.mark.parametrize("backend,fetches", [("pointer", True), ("auto", False)])
def test_the_pointer_model_is_downloaded_when_chosen_by_name_and_never_by_auto(switched_on, monkeypatch, backend, fetches):
    made = []
    monkeypatch.setattr(router, "PointerDecider", lambda source, **how: made.append(how["download"]) or object())
    router.build("pointer", router.Config(backend=backend))
    assert made == [fetches]


class Chromium:
    """Playwright's launcher with no installed Chrome: plumbing only."""

    def __init__(self):
        self.asked = []

    def launch_persistent_context(self, profile, **options):
        self.asked.append(options)
        if "channel" in options:
            raise RuntimeError("Chromium distribution 'chrome' is not found at /Applications")
        return "context"


def test_a_missing_browser_is_answered_with_the_bundled_one(monkeypatch, tmp_path):
    chromium = Chromium()
    monkeypatch.setattr(browser, "bundled_chromium", lambda: "/kept/chromium")
    window = browser.Window(profile=tmp_path)
    assert browser._context(SimpleNamespace(chromium=chromium), tmp_path, window) == "context"
    assert chromium.asked[1]["executable_path"] == "/kept/chromium"


def test_the_bundled_browser_is_downloaded_once_into_the_state_root(switched_on, tmp_path):
    executable = tmp_path / "chromium"
    ran = []

    def run(argv, **kw):
        ran.append(argv[1:3])
        assert kw["env"]["PLAYWRIGHT_BROWSERS_PATH"].endswith("browsers")
        if argv[1:3] == ["-m", "playwright"]:
            executable.write_text(MARKED)
        return subprocess.CompletedProcess(argv, 0, stdout=str(executable))

    assert browser.bundled_chromium(run) == str(executable) == browser.bundled_chromium(run)
    assert ran.count(["-m", "playwright"]) == 1


def test_the_browser_is_not_downloaded_when_switched_off():
    with pytest.raises(browser.BrowserUnavailable, match=provision.SWITCH):
        browser.bundled_chromium(lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout=""))
