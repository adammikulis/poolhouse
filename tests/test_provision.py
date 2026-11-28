"""A library that is missing is installed the first time it is needed: once, by one process, from the cache first, into this
environment only, and only the releases and digests the lock lists. Every installer and lock here is plumbing only: it writes
a module marked as such and downloads nothing."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import agent_dependency, pinned, provision
from poolhouse.fleet.settings import Settings
from poolhouse.httpguard import Refused
from poolhouse.net import packages, uvinstall

SRC = Path(__file__).resolve().parent.parent / "src"
MARKED = "# PLUMBING ONLY: written by a test's stand-in installer; no package was fetched\nHERE = True\n"
DIGEST = "--hash=sha256:" + "a" * 64
LOCK = f"""# PLUMBING ONLY: a release that does not exist, and one that is installed already
packaging==0.0.1 \\
    {DIGEST}
    # via plumbing-only-canned
plumbing-only-canned==1.0 \\
    {DIGEST}
    # via poolhouse (pyproject.toml)
"""


@pytest.fixture
def switched_on(monkeypatch):
    monkeypatch.delenv(provision.SWITCH, raising=False)


@pytest.fixture
def plumbing(tmp_path, monkeypatch, switched_on):
    """An extra holding one module that is not here, and a stand-in installer that writes it."""
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(provision, "_table", lambda: {"": [], "plumbing": ["plumbing-only-canned>=1"]})
    (tmp_path / "lock").write_text(LOCK, encoding="utf-8")
    monkeypatch.setattr(pinned, "FILE", tmp_path / "lock")
    name = "plumbing_only_canned_module"
    monkeypatch.delitem(sys.modules, name, raising=False)
    asked = []

    def installer(python, locked):
        asked.append((python, locked.read_text()))
        (site / f"{name}.py").write_text(MARKED, encoding="utf-8")
        return subprocess.CompletedProcess([], 0, "", "")

    yield SimpleNamespace(name=name, python=tmp_path / "venv" / "bin" / "python", run=installer, asked=asked, site=site)
    sys.modules.pop(name, None)


def test_a_missing_library_is_installed_the_first_time_and_found_after(plumbing, capsys):
    for _ in range(2):
        provision.ensure("plumbing", plumbing.name, python=plumbing.python, run=plumbing.run)
    ((python, written),) = plumbing.asked
    assert python == plumbing.python
    assert written == f"plumbing-only-canned==1.0 \\\n    {DIGEST}\n", "the locked release and its digest, not what is installed"
    said = capsys.readouterr()
    assert "needed for the first time" in said.err and said.out == "", "progress is said, and on standard error"


def test_what_is_here_asks_for_no_installer_even_switched_off(monkeypatch):
    monkeypatch.setenv(provision.SWITCH, "off")
    provision.ensure("plumbing", "json", run=lambda *a: pytest.fail("installed what is here"))


def test_switched_off_nothing_is_installed_and_the_reason_names_the_switch(plumbing, monkeypatch):
    monkeypatch.setenv(provision.SWITCH, "off")
    with pytest.raises(provision.Unavailable, match=provision.SWITCH) as raised:
        provision.ensure("plumbing", plumbing.name, python=plumbing.python, run=plumbing.run)
    assert raised.value.name == plumbing.name and not plumbing.asked


@pytest.mark.parametrize("asked", ["unlocked-canned>=1", "plumbing-only-canned>=2"])
def test_what_the_lock_does_not_hold_is_not_installed(plumbing, monkeypatch, asked):
    monkeypatch.setattr(provision, "_table", lambda: {"": [], "plumbing": [asked]})
    with pytest.raises(provision.Unavailable, match="hashed lock"):
        provision.ensure("plumbing", plumbing.name, python=plumbing.python, run=plumbing.run)
    assert not plumbing.asked


def test_an_installer_that_fails_says_what_it_said(plumbing):
    def broken(python, locked):
        return subprocess.CompletedProcess([], 1, "", "no wheel for this machine")

    with pytest.raises(ModuleNotFoundError, match="no wheel for this machine"):
        provision.ensure("plumbing", plumbing.name, python=plumbing.python, run=broken)


def test_a_system_python_is_never_installed_into(plumbing, monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    with pytest.raises(provision.Unavailable, match="system Python"):
        provision.ensure("plumbing", plumbing.name, run=plumbing.run)
    assert not plumbing.asked and "system Python" in provision.refusal()


def test_an_extra_follows_the_extras_it_names_keeps_its_markers_and_never_names_poolhouse():
    table = {"a": ["poolhouse[b,c]", "one>=1", 'never>=1; python_version < "3.0"'], "b": ['two; python_version >= "3.0"'],
             "c": ["poolhouse[a]", "one>=1"]}
    assert provision.requirements("a", table) == ["one>=1", "two"]
    with pytest.raises(provision.Unavailable, match="declares no"):
        provision.requirements("nothing", table)


CHILD = """
import subprocess, sys, time
from pathlib import Path
from poolhouse import pinned, provision
work = Path(sys.argv[1])
sys.path.insert(0, str(work))
provision._table = lambda: {"": [], "plumbing": ["plumbing-only-canned>=1"]}
pinned.FILE = work / "lock"
def installer(python, locked):
    with (work / "installs").open("a") as log:
        log.write("install\\n")
    time.sleep(1.0)
    (work / "plumbing_only_canned_module.py").write_text("# PLUMBING ONLY\\n")
    return subprocess.CompletedProcess([], 0, "", "")
provision.ensure("plumbing", "plumbing_only_canned_module", python=work / "venv" / "bin" / "python", run=installer)
import plumbing_only_canned_module
"""


def test_two_processes_asking_at_once_install_once(tmp_path, monkeypatch, switched_on):
    (tmp_path / "lock").write_text(LOCK, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    env.pop(provision.SWITCH, None)
    running = [subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(tmp_path)], env=env, stderr=subprocess.PIPE, text=True)
               for _ in range(2)]
    for one in running:
        _, errors = one.communicate(timeout=60)
        assert one.returncode == 0, errors
    assert (tmp_path / "installs").read_text() == "install\n"


def _uv(monkeypatch, codes):
    calls = []

    def install(python, spec, *, timeout, environment, offline=False):
        assert spec.name == "locked"
        calls.append(offline)
        return subprocess.CompletedProcess([], codes[offline], "", "")

    monkeypatch.setattr(uvinstall, "install", install)
    return calls


def test_what_the_package_cache_holds_installs_without_the_index(monkeypatch, tmp_path, switched_on):
    calls = _uv(monkeypatch, {True: 0})
    assert provision._install(tmp_path / "python", tmp_path / "locked").returncode == 0 and calls == [True]


def test_the_index_is_asked_only_when_the_cache_does_not_hold_everything(monkeypatch, tmp_path, switched_on):
    calls = _uv(monkeypatch, {True: 1, False: 0})
    assert provision._install(tmp_path / "python", tmp_path / "locked").returncode == 0 and calls == [True, False]


def test_a_lan_only_device_installs_from_its_cache_and_never_from_the_index(monkeypatch, tmp_path, switched_on):
    root = tmp_path / "daemon"
    root.mkdir()
    monkeypatch.setattr("poolhouse.fleet.runtime_paths.default_root", lambda: root)
    Settings(download_sources="lan").save(root / "settings.json")
    calls = _uv(monkeypatch, {True: 1, False: 0})
    with pytest.raises(provision.Unavailable, match="LAN only"):
        provision._install(tmp_path / "python", tmp_path / "locked")
    assert calls == [True]


def test_the_installer_takes_no_destination_or_module_path_from_the_environment(monkeypatch, tmp_path, switched_on):
    for name in ("PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PYTHONPATH", "PYTHONHOME"):
        monkeypatch.setenv(name, str(tmp_path / "elsewhere"))
    ran = []
    monkeypatch.setattr(uvinstall, "install", lambda *a, **k: None)  # no uv here: the pip path
    monkeypatch.setattr(provision, "_present", lambda name: False)
    monkeypatch.setattr(provision.subprocess, "run", lambda argv, **kw: ran.append((argv, kw["env"])))
    monkeypatch.setattr("poolhouse.net.packages.run",
                        lambda python, args, *, timeout, env=None, locked: ran.append(((*args, locked.name), env)))
    provision._install(tmp_path / "python", tmp_path / "locked")
    (bootstrap, first), (install, second) = ran
    assert bootstrap == [str(tmp_path / "python"), "-I", "-m", "ensurepip"] and install == ("install", "locked")
    for environment in (first, second):
        assert not {"PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PYTHONPATH", "PYTHONHOME"} & set(environment)


def test_uv_is_asked_for_every_requirement_and_for_the_cache_alone_when_told(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(uvinstall, "program", lambda path=None: "/opt/uv")
    monkeypatch.setattr(uvinstall.subprocess, "run", lambda argv, **kw: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    uvinstall.install(tmp_path / "python", ["a>=1", "b"], timeout=1, environment={}, offline=True)
    uvinstall.install(tmp_path / "python", "a>=1", timeout=1, environment={})
    assert "--offline" in ran[0] and ran[0][-2:] == ["a>=1", "b"]
    assert "--offline" not in ran[1] and ran[1][-1] == "a>=1" and "--require-hashes" not in ran[1]


def test_a_locked_install_is_held_to_its_digests_by_uv_and_by_pip(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(uvinstall, "program", lambda path=None: "/opt/uv")
    monkeypatch.setattr(uvinstall.subprocess, "run", lambda argv, **kw: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    uvinstall.install(tmp_path / "python", tmp_path / "locked", timeout=1, environment={})
    monkeypatch.setattr(packages, "_config", lambda *a: {})
    monkeypatch.setattr(packages, "_pip", lambda python, args, *a: ran.append(args))
    packages.run(tmp_path / "python", ["install"], timeout=1, env={}, locked=tmp_path / "locked")
    for argv in ran:
        assert {"--require-hashes", "--no-deps"} <= set(argv) and argv[-2:] == ["--requirement", str(tmp_path / "locked")]
    with pytest.raises(Refused, match="only installed"):
        packages.run(tmp_path / "python", ["download"], timeout=1, env={}, locked=tmp_path / "locked")
    with pytest.raises(Refused, match="requirement"):
        packages.run(tmp_path / "python", ["install", "-r", "x"], timeout=1, env={})


def test_a_chat_runtime_that_first_use_installs_is_no_problem_to_report_and_is_installed_when_chat_starts(monkeypatch):
    monkeypatch.setitem(sys.modules, "agents", None)
    monkeypatch.setattr(provision, "refusal", lambda: "")
    assert agent_dependency.problem() == ""
    asked = []

    def arrives(extra, *modules):
        asked.append((extra, modules))
        monkeypatch.delitem(sys.modules, "agents")

    monkeypatch.setattr(provision, "ensure", arrives)
    assert agent_dependency.problem(install=True) == "" and asked == [("agents", ("agents",))]
