"""A tool, build or runtime that is missing is built or downloaded the first time it is needed, and reused after. Every source
here is plumbing only: a canned server, a marked file in place of a release, a recorded call in place of a download."""

from __future__ import annotations

import contextlib
import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import codex_install, harnessing, opencode_install, provision, web
from poolhouse.decide import router
from poolhouse.fleet import environment as environments, updates
from poolhouse.gguf import tools
from poolhouse.serve import (
    binary,
    build_patches,
    build_platform,
    build_recover,
    build_release,
    llamacpp_compile,
)
from poolhouse.serve.build_paths import builds_dir
from poolhouse.serve.policy import LocalPolicy
from poolhouse.speech import asr
from poolhouse.testing.canned import canned_binary
from poolhouse.walk import ops

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


def test_a_missing_converter_is_fetched_whole_at_the_verified_commit_the_first_time(switched_on, monkeypatch, tmp_path):
    monkeypatch.setenv("POOLHOUSE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(tools, "source_dirs", lambda: (tools.llama_cpp_src(),))
    fetched = []

    def checkout(upstream, sha, dest, pipeline):
        fetched.append((upstream.git_url, sha, dest.name))
        dest.mkdir(parents=True)
        (dest / tools.CONVERTER_NAME).write_text(MARKED)

    monkeypatch.setattr(llamacpp_compile, "checkout", checkout)
    found = tools.require_converter()
    assert found == (tools.llama_cpp_src() / tools.CONVERTER_NAME).resolve() and tools.require_converter() == found
    assert fetched == [("https://github.com/ggml-org/llama.cpp", build_patches.pinned_commit(), "llama.cpp-src.part")], \
        "fetched once, at the verified commit, beside where it ends up"


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

    def install(self, names, *, on_progress=None, locked=False):
        assert locked, "what nobody asked for by name comes from the lock"
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


class Opened:
    """A browser context with one page already open: plumbing only."""

    def __init__(self):
        self.pages, self.guards = [SimpleNamespace(name="earlier", close=lambda: self.pages.pop(0))], []
        self.add_init_script = lambda script: self.guards.append(len(self.pages))
        self.set_default_timeout = lambda ms: None
        self.close = lambda: None

    def new_page(self):
        self.pages.append(SimpleNamespace(name="after", close=lambda: None))
        return self.pages[-1]


@pytest.mark.parametrize("guarded,name", [(True, "after"), (False, "earlier")])
def test_a_guarded_window_never_uses_a_page_open_before_the_guard(monkeypatch, tmp_path, guarded, name):
    context, proxies = Opened(), []

    def launched(play, profile, window, guard=None):
        proxies.append(guard.port if guard else None)
        return context

    monkeypatch.setattr(browser, "_context", launched)
    with browser._paged(None, browser.Window(profile=tmp_path, guarded=guarded)) as page:
        assert page.name == name and [one.name for one in context.pages] == [name]
    assert [bool(port) for port in proxies] == [guarded], "the proxy is listening when the browser is launched on it"
    assert context.guards == ([1] if guarded else []), "the removal comes before the page"


@pytest.mark.parametrize("rendered", [False, True])
def test_only_a_rendering_that_was_asked_for_may_install_or_download(switched_on, monkeypatch, rendered):
    monkeypatch.setattr(web, "check", lambda url: url)
    may = []

    @contextlib.contextmanager
    def browse():
        may.append(not provision.download_refusal())
        raise browser.BrowserUnavailable("plumbing only: no page is opened")
        yield

    web.read("https://tessyn.example/", fetch=lambda url: "<html><body>thin</body></html>", browse=browse, rendered=rendered)
    assert may == [rendered]


def test_a_pinned_digest_is_what_the_download_is_held_to(monkeypatch, tmp_path):
    release = {"assets": [{"name": "a.tar.gz", "digest": "sha256:" + "0" * 64}]}
    monkeypatch.setattr(opencode_install.net, "default", lambda: SimpleNamespace(json=lambda url, ask: release))
    held = []

    def download(asset, into, **how):
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


@pytest.mark.parametrize("guarded,own", [(False, False), (True, False), (True, True), (False, True)])
def test_a_missing_browser_is_answered_with_the_bundled_one_sandboxed_either_way(monkeypatch, tmp_path, guarded, own):
    chromium = Chromium()
    monkeypatch.setattr(browser, "bundled_chromium", lambda: "/kept/chromium")
    window = browser.Window(profile=tmp_path, guarded=guarded, **({"own_pages": True} if own else {}))
    with browser.browserguard.Guard() as guard:
        assert browser._context(SimpleNamespace(chromium=chromium), tmp_path, window, guard if guarded else None) == "context"
    assert chromium.asked[1]["executable_path"] == "/kept/chromium"
    through = {"server": f"http://127.0.0.1:{guard.port}", "bypass": "<-loopback>"} if guarded else None
    for options in chromium.asked:
        assert options["chromium_sandbox"] is (guarded or not own), "only a window for the project's own pages goes without"
        assert options.get("service_workers") == ("block" if guarded else None)
        assert options.get("proxy") == through and options["accept_downloads"] is not guarded


def test_a_guarded_window_is_not_launched_without_its_proxy(tmp_path):
    chromium = Chromium()
    with pytest.raises(AttributeError):
        browser._context(SimpleNamespace(chromium=chromium), tmp_path, browser.Window(profile=tmp_path, guarded=True))
    assert chromium.asked == []


def test_only_a_walk_of_this_machine_goes_without_the_browsers_sandbox():
    for base in ("http://127.0.0.1:8787", "http://localhost:1/ui", "http://[::1]:2"):
        assert ops._window(ops.Walk(base=base)).own_pages, base
    for base in ("https://tessyn.example", "http://192.168.1.4:8787", "http://127.0.0.1.tessyn.example", ""):
        assert not ops._window(ops.Walk(base=base)).own_pages, base


@pytest.mark.parametrize("tool,asked", [("web_read", {"rendered": True}), ("web_read", {}), ("web_look", {})])
def test_a_tool_that_reads_or_looks_at_a_page_installs_and_downloads_nothing(switched_on, monkeypatch, tool, asked):
    url = "https://tessyn.example/"
    origins = web.untrusted.Origins()
    origins.typed(url)
    monkeypatch.setattr(web.untrusted, "shared", lambda: origins)
    monkeypatch.setattr(web, "check", lambda url: url)
    held = []

    def fetch(url):
        held.append(provision.download_refusal())
        return "<html><body>thin</body></html>"

    @contextlib.contextmanager
    def browse():
        held.append(provision.download_refusal())
        raise browser.BrowserUnavailable("plumbing only: no page is opened")
        yield

    calls = {schema["function"]["name"]: call for schema, call in web.tools(fetch=fetch, browse=browse, vision=True)}
    got = calls[tool]({"url": url, **asked})
    assert held and all(held), "everything the tool did, it did with nothing to be installed or downloaded"
    if tool == "web_look":
        assert got["none"].startswith("no browser")
    else:
        assert "thin" in got["text"] and got["rendered"] is False, "with no browser the plain read is what there is"
    assert provision.download_refusal() == "", "and only while the tool ran"


def test_the_bundled_browser_is_downloaded_once_into_the_state_root(switched_on, monkeypatch, tmp_path):
    executable = tmp_path / "chromium"
    ran = []

    for name in ("PLAYWRIGHT_DOWNLOAD_HOST", "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST", "NODE_TLS_REJECT_UNAUTHORIZED",
                 "npm_config_playwright_download_host", "npm_package_config_playwright_chromium_download_host",
                 "NPM_CONFIG_PLAYWRIGHT_DOWNLOAD_HOST"):
        monkeypatch.setenv(name, "elsewhere")

    def run(argv, **kw):
        assert argv[1] == "-P" and Path(kw["cwd"]).name.startswith("poolhouse-launch-")
        ran.append(argv[2:4])
        assert "elsewhere" not in kw["env"].values(), "the browser comes from Playwright's own source, checked"
        assert kw["env"]["PLAYWRIGHT_BROWSERS_PATH"].endswith("browsers")
        if argv[2:4] == ["-m", "playwright"]:
            executable.write_text(MARKED)
        return subprocess.CompletedProcess(argv, 0, stdout=str(executable))

    assert browser.bundled_chromium(run) == str(executable) == browser.bundled_chromium(run)
    assert ran.count(["-m", "playwright"]) == 1


def test_the_browser_is_not_downloaded_when_switched_off():
    with pytest.raises(browser.BrowserUnavailable, match=provision.SWITCH):
        browser.bundled_chromium(lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout=""))
