"""The files of the kind that runs later (`guard.runs_later`): the corpus of auto-run names gets a reason, ordinary source gets none, nothing keeps a
name table. The answer informs a publication's result and the hooks; it refuses nothing."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import claude, codex, pi
from poolhouse.guard import autoexec, ecosystems, harnesses, runs_later
from poolhouse.guard.runs_later import registry, why

REPO = Path(__file__).resolve().parent.parent
TEXT = b"x = 1\n"

N3 = ["lefthook.yml", ".lintstagedrc", ".lfsconfig", "Tiltfile", "gradlew", "WORKSPACE", "BUILD.bazel", ".bazelrc", "requirements.txt", "Pipfile",
      ".prettierrc.js"]
N4 = [".github/copilot-instructions.md", ".windsurfrules", ".aider.conf.yml", ".continue/config.json", ".clinerules", ".cursorrules"]
N5 = [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", "codeowners"]
HARNESS = ["CLAUDE.md", "AGENTS.md", "GEMINI.md", ".mcp.json", ".claude/settings.json", ".codex/config.toml", ".pi/x.json", "docs/CLAUDE.md",
           "src/deep/AGENTS.md", "CLAUDE.MD", "claude.md", "opencode.json", ".opencode/plugin/x.js"]
REGISTRY_NAMES = ["Makefile", "Dockerfile", "Jenkinsfile", "Tiltfile", "WORKSPACE", "Rakefile", "Gemfile", "Pipfile", "requirements.txt", "makefile",
                  "GNUmakefile", "justfile", "Vagrantfile", "Procfile", "CMakeLists.txt"]
ECOSYSTEM = ["conftest.py", "tests/conftest.py", "sitecustomize.py", "usercustomize.py", "setup.py", "noxfile.py", "build.rs", "webpack.config.js",
             "vite.config.ts", "jest.config.mjs", "package.json", "Cargo.toml", "pyproject.toml", "tox.ini", "evil.pth", "rules.mk", "x.cmake", "x.bzl",
             "x.gemspec", "build.gradle", "pom.xml", "mvnw", "mvnw.cmd", "gradlew.bat", ".envrc", ".npmrc", ".github/workflows/ci.yml", ".gitlab-ci.yml",
             ".husky/pre-commit", ".githooks/pre-push", ".git/hooks/pre-commit", ".vscode/tasks.json", ".devcontainer/devcontainer.json",
             ".pre-commit-config.yaml", ".gitattributes", ".gitmodules", "x.plist", "x.service", "x.timer", "Library/LaunchAgents/x.plist",
             "scripts/test", "scripts/hooks/x", "bin/tool", "yarn.lock", "go.mod", "Cargo.lock", "lib/tool.jar"]
ORDINARY = ["src/app.py", "tests/test_app.py", "docs/notes.md", "README.md", "src/lib.rs", "web/app.ts", "web/App.tsx", "src/Main.java", "main.go", "x.c",
            "include/x.h", "docs/guide.rst", "notes.txt", "page.html", "src/deep/er/module.py", "src/data.json", "src/config.yaml", ".gitignore", "LICENSE"]


@pytest.mark.parametrize("path", [*N3, *N4, *N5, *HARNESS, *REGISTRY_NAMES, *ECOSYSTEM])
def test_the_corpus_of_auto_run_names_gets_a_reason_from_the_path_alone(path):
    assert why(path), path


@pytest.mark.parametrize("path", ORDINARY)
def test_ordinary_files_are_not_of_the_kind_that_runs_later(path):
    assert why(path, TEXT, 0o644) == "", (path, why(path, TEXT, 0o644))


@pytest.mark.parametrize("data, mode", [(b"#!/bin/sh\necho\n", 0o644), (TEXT, 0o755), (b"\x7fELF\x02", 0o644), (b"\xcf\xfa\xed\xfe\x07", 0o644),
                                        (b"\0asm\x01", 0o644), (b"\xf3\r\r\n\0\0", 0o644), (b"\xca\xfe\xba\xbe", 0o644)])
def test_a_program_by_its_shape_runs_later_whatever_its_name(data, mode):
    assert why("src/app.py", data[:runs_later.HEAD_BYTES], mode) == runs_later.EXECUTABLE


def test_a_crlf_text_file_with_a_two_character_first_line_is_no_program():
    assert why("src/app.py", b"//\r\nx = 1\r\n"[:runs_later.HEAD_BYTES], 0o644) == ""


@pytest.mark.parametrize("path", ["src/Makefile", "SRC/MAKEFILE", "src/CONFTEST.PY", "src/.hidden.py", ".a/b.py", "src/a/.b/c.py",
                                  "CLAUDE.md", "src/Claude.MD", "x.PTH", "a/./.claude/x.md", "a//.github/y.md"])
def test_case_and_spelling_do_not_change_the_answer_for_a_name(path):
    assert why(path), path


def test_a_full_width_spelling_of_a_registry_name_is_the_same_name():
    full_width = "".join(chr(ord(letter) + 0xFEE0) for letter in "Makefile")
    assert full_width != "Makefile" and why(f"src/{full_width}") == ecosystems.BUILD


def test_the_hook_reads_the_answer_path_facts_alone_give_and_a_plain_dot_file_does_not_run():
    assert why("conftest.py") == ecosystems.BUILD and why("scripts/test") == ecosystems.PROGRAM
    assert why(".git/hooks/pre-commit") and why(".cursorrules") and why("CLAUDE.md")
    assert not why(".gitignore") and not why("src/app.py") and not why("src_copy") and not why("LICENSE")


def test_the_name_tables_of_guard_autoexec_are_deleted_and_path_reason_answers_from_the_records(tmp_path):
    for table in ("RUNS", "FOLDERS", "FILES", "SUFFIX", "CONFIGS", "HOOKS", "AGENT", "BUILD", "CI", "SHELL_START", "EDITOR"):
        assert not hasattr(autoexec, table), table
    root = str(tmp_path)
    assert autoexec.path_reason("conftest.py", [root]) == ecosystems.BUILD
    assert autoexec.path_reason("src/app.py", [root]) == ""
    assert autoexec.path_reason("evil/x.pth", [root]) == ecosystems.suffixes()[".pth"]


@pytest.mark.parametrize("module", [claude, codex, pi])
def test_every_harness_record_holds_instruction_names_and_each_runs_later_at_any_depth(module):
    assert module.INSTRUCTIONS and all(isinstance(name, str) and name for name in module.INSTRUCTIONS)
    for name in module.INSTRUCTIONS:
        assert why(name) == runs_later.AGENT and why(f"a/b/{name}") == runs_later.AGENT
    assert {runs_later.fold(name) for name in module.INSTRUCTIONS} <= runs_later.harness_names()


def test_the_settings_of_a_harness_are_every_record_name_but_its_instruction_files():
    assert harnesses.settings() == {".claude", ".mcp.json", ".codex", ".pi", ".opencode", "opencode.json", "opencode.jsonc"}


def test_a_launcher_without_a_harness_record_fails_the_contract_and_the_hooks_read_the_records_without_importing_a_launcher():
    import tomllib

    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    launchers = {name.removeprefix("poolhouse-") for name, target in scripts.items() if name.startswith("poolhouse-") and target.split(":")[0] in
                 {"poolhouse.claude", "poolhouse.codex", "poolhouse.pi"}}
    assert launchers and launchers <= set(harnesses.RECORDS), f"a launcher has no harness record: {launchers - set(harnesses.RECORDS)}"
    assert all(record for record in harnesses.RECORDS.values())
    probe = ("import sys, time; from poolhouse.guard import runs_later; began = time.perf_counter(); runs_later.why('CLAUDE.md'); "
             "slow = time.perf_counter() - began > 0.25; "
             "sys.exit(1 if any(m in sys.modules for m in ('poolhouse.claude', 'poolhouse.codex', 'poolhouse.pi')) else 2 if slow else 0)")
    done = subprocess.run([sys.executable, "-c", probe], cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO / "src")}, capture_output=True, check=False)
    assert done.returncode == 0, (done.returncode, "1: a launcher was imported, 2: the first answer took over 0.25 s", done.stderr.decode()[-300:])


def test_the_registry_snapshot_names_its_revision_and_digest_and_holds_the_names_the_design_lists_and_nothing_unread():
    snapshot = registry()
    assert len(snapshot["registry"]["revision"]) == 40 and len(snapshot["registry"]["digest"]) == 64
    assert set(snapshot) == {"registry", "names"}
    for name in ("makefile", "dockerfile", "jenkinsfile", "tiltfile", "workspace", "rakefile", "gemfile", "pipfile", "requirements.txt"):
        assert name in snapshot["names"], name


def refresher():
    spec = importlib.util.spec_from_file_location("refresh_language_kinds", REPO / "scripts" / "refresh_language_kinds.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTRY_FILE = """\
Alpha:
  type: programming
  extensions: [".al"]
  filenames: [Alphafile, Shared]
Beta:
  type: data
  filenames: [shared]
Gamma:
  type: prose
  filenames: [GAMMA]
"""


def test_the_refresh_script_builds_the_snapshot_from_a_registry_file_and_its_check_names_a_difference(tmp_path, capsys):
    source, output = tmp_path / "languages.yml", tmp_path / "language-kinds.json"
    source.write_text(REGISTRY_FILE)
    script = refresher()
    assert script.main(["--source", str(source), "--output", str(output)]) == 0
    made = json.loads(output.read_text())
    assert made["names"] == {"alphafile": "programming", "gamma": "prose", "shared": "data"}, "the strictest kind of its claimants, lower case"
    assert set(made) == {"names", "registry"} and made["registry"]["revision"] == "local" and len(made["registry"]["digest"]) == 64
    assert script.main(["--source", str(source), "--output", str(output), "--check"]) == 0
    output.write_text(output.read_text().replace('"gamma": "prose"', '"gamma": "data"'))
    assert script.main(["--source", str(source), "--output", str(output), "--check"]) == 1
    assert "differs from the registry" in capsys.readouterr().err
    source.write_text(REGISTRY_FILE.replace("type: prose", "type: nonsense"))
    with pytest.raises(ValueError, match="type"):
        script.main(["--source", str(source), "--output", str(output)])
