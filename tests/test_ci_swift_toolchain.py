"""The `affected` job's step that hands the app crate's build a Swift runtime it accepts, run as the workflow writes it with recording
stand-ins for `sudo`, `xcode-select` and `swiftc`: nothing of this computer's toolchain is read or changed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
JOB = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]["affected"]
NAME = "the Swift runtime as a system toolchain input"


def macos(test):
    return pytest.mark.skipif(sys.platform != "darwin", reason="the step reads files with the stat of macOS")(test)


def step() -> dict:
    [found] = [each for each in JOB["steps"] if each.get("name") == NAME]
    return found


def hosted_image(tmp_path: Path, runtime: list[str] | None = None) -> tuple[subprocess.CompletedProcess, list[list[str]], Path]:
    """Run the step over an image whose selected developer folder is reached through a link, as a hosted runner's is; the finished step, what
    it asked of `sudo` and the folder the link leads to."""
    real = (tmp_path / "Xcode_16.4.app" / "Contents" / "Developer").resolve()
    macosx = real / "Toolchains" / "XcodeDefault.xctoolchain" / "usr" / "lib" / "swift" / "macosx"
    macosx.mkdir(parents=True)
    link = tmp_path / "Xcode.app"
    link.symlink_to(tmp_path / "Xcode_16.4.app")
    selected, asked = tmp_path / "selected", tmp_path / "asked"
    selected.write_text(str(link / "Contents" / "Developer"))
    reported = [str(macosx), "/usr/lib/swift"] if runtime is None else runtime
    target = json.dumps({"paths": {"runtimeLibraryPaths": reported}})
    programs = tmp_path / "bin"
    programs.mkdir()
    for name, body in {
        "xcode-select": f'cat "{selected}"',
        "swiftc": f"printf '%s' '{target}'",
        "sudo": f'printf "%s\\n" "$*" >> "{asked}"; if [ "$1 $2" = "xcode-select --switch" ]; then printf "%s" "$3" > "{selected}"; fi',
        "python": f'exec "{sys.executable}" "$@"',
    }.items():
        (programs / name).write_text(f"#!/bin/sh\n{body}\n")
        (programs / name).chmod(0o755)
    done = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step()["run"]], capture_output=True, text=True,
                          check=False, env={**os.environ, "PATH": f"{programs}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path)})
    return done, [line.split() for line in asked.read_text().splitlines()] if asked.exists() else [], real


@macos
def test_the_step_selects_the_folder_the_link_leads_to_and_gives_roots_ownership_to_the_runtime_the_toolchain_reports(tmp_path):
    done, asked, real = hosted_image(tmp_path)
    assert done.returncode == 0, done.stderr
    macosx = str(real / "Toolchains" / "XcodeDefault.xctoolchain" / "usr" / "lib" / "swift" / "macosx")
    assert asked == [["xcode-select", "--switch", str(real)], ["chown", "-R", "root:wheel", macosx], ["chmod", "-R", "go-w", macosx]]
    assert f"{macosx}: owner {os.getuid()}" in done.stdout, "what the image held is in the log"


@macos
def test_a_runtime_folder_that_is_not_there_fails_the_step(tmp_path):
    done, asked, _ = hosted_image(tmp_path, runtime=[str(tmp_path / "gone")])
    assert done.returncode != 0
    assert [call[0] for call in asked] == ["xcode-select"], "nothing is handed to root"


def test_the_step_runs_on_a_hosted_runner_only_and_before_anything_is_built():
    assert step()["if"] == "runner.environment == 'github-hosted'"
    names = [each.get("name") for each in JOB["steps"]]
    assert names.index(NAME) < names.index("build the node the tests start") < names.index("the tests the pull request reaches")
    assert "continue-on-error" not in step()


def test_the_build_keeps_its_demand_of_the_runtime_folders():
    source = (ROOT / "app" / "src-tauri" / "native_service_build.rs").read_text(encoding="utf-8")
    assert "canonical == path && metadata.is_dir() && metadata.uid() == 0 && metadata.mode() & 0o002 == 0" in source
