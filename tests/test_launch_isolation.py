"""Nothing planted in the directory a command is run from is imported or executed by what an install starts. Every planted
file only writes a mark; the programs run are the real ones, offline, in throwaway environments."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import provision, safelaunch
from poolhouse.fleet import environment as environments, measuring
from poolhouse.gym import catalog
from poolhouse.net import packages, uvinstall

browser = __import__("importlib").import_module("poolhouse.scrape.browser")  # the package exports a function of this name


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A directory somebody else wrote, stood in: each module a launch could take from it writes `mark` when imported."""
    where = tmp_path / "project"
    where.mkdir()
    mark = tmp_path / "planted-code-ran"
    code = f"open({str(mark)!r}, 'a').write(__name__ + '\\n')\n"
    for package in ("pip", "ensurepip", "venv", "playwright", "poolhouse"):
        (where / package).mkdir()
        (where / package / "__init__.py").write_text(code, encoding="utf-8")
        (where / package / "__main__.py").write_text(code, encoding="utf-8")
    for module in ("json", "ladybug"):
        (where / f"{module}.py").write_text(code, encoding="utf-8")
    monkeypatch.chdir(where)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    return SimpleNamespace(where=where, ran=lambda: mark.read_text() if mark.exists() else "")


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    """An interpreter in a new environment with no pip."""
    where = tmp_path_factory.mktemp("bare") / "env"
    venv.create(where, with_pip=False)
    return where / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def test_ensurepip_and_pip_are_the_interpreters_own(project, bare, monkeypatch, tmp_path):
    monkeypatch.delenv(provision.SWITCH, raising=False)
    monkeypatch.setattr(uvinstall, "install", lambda *a, **k: None)  # no uv: the pip path
    monkeypatch.setattr(provision, "_present", lambda name: False)
    monkeypatch.setattr(packages, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""))
    provision._install(bare, tmp_path / "locked")
    done = packages._pip(bare, ["--version"], dict(os.environ), 120)
    assert done.returncode == 0 and done.stdout.startswith("pip ") and project.ran() == ""


def test_a_job_environment_is_made_by_the_interpreters_own_venv(project, monkeypatch, tmp_path):
    made = environments.Environment(tmp_path / "jobs")
    monkeypatch.setattr(made, "host_python", lambda: Path(sys._base_executable))
    made.create()
    assert made.exists and project.ran() == ""


def test_looking_for_a_python_imports_nothing_from_here(project, monkeypatch, tmp_path):
    other = "3.99"
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: sys.executable if name.endswith(f"python{other}") else None)
    assert environments.Environment(tmp_path, python_version=other).host_python() is None
    assert project.ran() == ""


def test_the_browser_commands_import_nothing_from_here(project, monkeypatch):
    monkeypatch.delenv(provision.SWITCH, raising=False)
    asked = []

    def run(argv, **how):
        asked.append(argv)
        if "install" in argv:
            argv = [*argv[:argv.index("install")], "--version"]   # the same launch, asked to download nothing
        how.pop("capture_output", None)
        return subprocess.run(argv, **{**how, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE})

    with pytest.raises(browser.BrowserUnavailable):
        browser.bundled_chromium(run)
    assert any("install" in argv for argv in asked) and project.ran() == ""


def test_the_bench_and_gym_probes_import_nothing_from_here(project):
    managed = SimpleNamespace(exists=True, python=Path(sys.executable))
    measuring._measures(managed)
    with contextlib.suppress(RuntimeError, ValueError):
        catalog.probe(sys.executable)
    assert project.ran() == ""


def test_uv_is_never_taken_from_the_current_directory(tmp_path, monkeypatch):
    here, elsewhere = tmp_path / "project", tmp_path / "tools"
    for folder in (here, here / "bin", elsewhere):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "uv").write_text("#!/bin/sh\n# PLUMBING ONLY\n")
        (folder / "uv").chmod(0o755)
    monkeypatch.chdir(here)
    planted = os.pathsep.join([".", "", "bin", str(here), str(here / "bin")])
    assert uvinstall.program(planted + os.pathsep + str(elsewhere)) == str(elsewhere / "uv")
    assert safelaunch.which("uv", planted) is None
    monkeypatch.chdir(tmp_path.anchor)
    assert safelaunch.which("uv", str(elsewhere)) == str(elsewhere / "uv"), "a root as the directory rules out only itself"


def test_on_windows_every_poolhouse_process_keeps_bare_names_from_running_out_of_the_current_directory():
    source = Path(__file__).resolve().parent.parent / "src"
    environment = {name: value for name, value in os.environ.items() if name.lower() != "nodefaultcurrentdirectoryinexepath"}
    environment["PYTHONPATH"] = str(source)

    def importing_on(system):
        # what Windows reads is this process's variable, which its children get; the platform name is all that is pretended
        code = ("import importlib, logging, os, subprocess, sys, typing\n"
                f"sys.platform = {system!r}\n"
                "import poolhouse\n"
                "print(os.environ.get('NoDefaultCurrentDirectoryInExePath'))")
        return subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True,
                              check=True, timeout=60).stdout.strip()

    assert (importing_on("win32"), importing_on(sys.platform if sys.platform != "win32" else "linux")) == ("1", "None")


def test_uv_runs_from_an_empty_directory(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(uvinstall, "program", lambda path=None: "/opt/uv")
    monkeypatch.setattr(uvinstall.subprocess, "run",
                        lambda argv, **kw: seen.append((list(Path(kw["cwd"]).iterdir()), argv)) or subprocess.CompletedProcess(argv, 0))
    monkeypatch.chdir(tmp_path)
    uvinstall.install(Path("env/bin/python"), "a", timeout=1, environment={})
    ((held, argv),) = seen
    assert held == [] and str(tmp_path / "env/bin/python") in argv
