"""The classifier of plain source (`guard.runs_later`): the corpus of auto-run names is non-plain, ordinary source is plain, nothing keeps a name table."""

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
from poolhouse.guard.runs_later import PLAIN, classify, registry, tree, wired

REPO = Path(__file__).resolve().parent.parent
TEXT = b"x = 1\n"

N3 = ["lefthook.yml", ".lintstagedrc", ".lfsconfig", "action.yml", "Tiltfile", "cloudbuild.yaml", "gradlew", "gradle/wrapper/gradle-wrapper.jar",
      ".mvn/wrapper/maven-wrapper.jar", "Directory.Build.props", "app/App.csproj", "App.sln", "WORKSPACE", "BUILD.bazel", ".bazelrc",
      "requirements.txt", "Pipfile", ".prettierrc.js"]
N4 = [".github/copilot-instructions.md", ".windsurfrules", ".aider.conf.yml", ".continue/config.json", ".clinerules", ".cursorrules"]
N5 = [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", "codeowners"]
HARNESS = ["CLAUDE.md", "AGENTS.md", "GEMINI.md", ".mcp.json", ".claude/settings.json", ".codex/config.toml", ".pi/x.json", "docs/CLAUDE.md",
           "src/deep/AGENTS.md", "CLAUDE.MD", "claude.md"]
REGISTRY_NAMES = ["Makefile", "Dockerfile", "Jenkinsfile", "Tiltfile", "WORKSPACE", "Rakefile", "Gemfile", "Pipfile", "requirements.txt", "makefile",
                  "GNUmakefile", "justfile", "Vagrantfile", "Procfile", "CMakeLists.txt"]
ECOSYSTEM = ["conftest.py", "tests/conftest.py", "sitecustomize.py", "usercustomize.py", "setup.py", "noxfile.py", "build.rs", "webpack.config.js",
             "vite.config.ts", "jest.config.mjs", "package.json", "Cargo.toml", "pyproject.toml", "tox.ini", "evil.pth", "rules.mk", "x.cmake", "x.bzl",
             "x.gemspec", "build.gradle", "pom.xml", "mvnw", "mvnw.cmd", "gradlew.bat", ".envrc", ".npmrc", ".github/workflows/ci.yml", ".gitlab-ci.yml",
             ".husky/pre-commit", ".githooks/pre-push", ".git/hooks/pre-commit", ".vscode/tasks.json", ".devcontainer/devcontainer.json",
             ".pre-commit-config.yaml", ".gitattributes", ".gitmodules", "x.plist", "x.service", "x.timer", "Library/LaunchAgents/x.plist",
             "scripts/test", "scripts/hooks/x", "bin/tool", "yarn.lock", "go.mod", "Cargo.lock", "lib/tool.jar"]
ORDINARY = ["src/app.py", "tests/test_app.py", "docs/notes.md", "README.md", "src/lib.rs", "web/app.ts", "web/App.tsx", "src/Main.java", "main.go", "x.c",
            "include/x.h", "docs/guide.rst", "notes.txt", "page.html", "src/deep/er/module.py"]


def verdict(path: str, data: bytes = TEXT, mode: int = 0o644, links: int = 1):
    return classify(path, data, mode, links)


@pytest.mark.parametrize("path", [*N3, *N4, *N5, *HARNESS, *REGISTRY_NAMES, *ECOSYSTEM])
def test_the_corpus_of_auto_run_names_is_refused(path):
    assert not verdict(path).plain, path
    assert verdict(path).sentence in runs_later.CLASSES.values()


@pytest.mark.parametrize("path", ORDINARY)
def test_ordinary_source_is_applied(path):
    assert verdict(path).plain, (path, verdict(path))


def test_every_py_file_of_src_and_tests_that_no_non_plain_file_names_is_plain_source():
    names = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout.decode().split("\0")
    paths = [name for name in names if name and (REPO / name).is_file() and not (REPO / name).is_symlink()]

    def facts(rel):
        info = (REPO / rel).stat()
        return (REPO / rel).read_bytes()[:runs_later.READ_LIMIT], info.st_mode, info.st_nlink

    verdicts = tree(paths, facts)
    sources = [path for path in paths if path.endswith(".py") and path.split("/")[0] in ("src", "tests")]
    refused = [path for path in sources if not verdicts[path].plain]
    named = {path: verdicts[path] for path in refused if verdicts[path].klass == "named"}
    by_structure = [path for path in refused if path not in named]
    assert all(Path(path).name.lower() in ecosystems.names() or verdicts[path].klass in ("program", "instruction") for path in by_structure), by_structure
    seeds = [path for path in paths if not verdicts[path].plain and path not in named]
    reasons = wired(paths, seeds, lambda path: facts(path)[0])
    for path in named:
        assert path in reasons and Path(path).name in facts(reasons[path])[0].decode("utf-8", errors="replace"), (path, reasons.get(path))
    plain = len(sources) - len(refused)
    assert plain > len(by_structure) * 10 and plain > 100, f"a rule that refuses {len(refused)} of {len(sources)} source files refuses too much"
    assert any(verdicts[path].plain for path in sources if path.startswith("src/poolhouse/guard/")), "a project's ordinary modules are applied"


def test_a_python_file_named_by_a_makefile_a_workflow_or_a_package_manifest_is_non_plain_and_so_is_what_that_file_names():
    paths = ["Makefile", ".github/workflows/ci.yml", "package.json", "scripts/build.py", "tools/ci_step.py", "js/run.js", "tools/chain.py", "src/free.py",
             "docs/build.md"]
    held = {"Makefile": b"all:\n\tpython scripts/build.py\n", ".github/workflows/ci.yml": b"run: python tools/ci_step.py\n",
            "package.json": b'{"scripts": {"x": "node js/run.js"}}', "scripts/build.py": b"import os\nos.system('python tools/chain.py')\n",
            "tools/ci_step.py": TEXT, "js/run.js": b"1\n", "tools/chain.py": TEXT, "src/free.py": TEXT, "docs/build.md": b"see src/free.py\n"}
    verdicts = tree(paths, lambda path: (held[path], 0o644, 1))
    for named in ("scripts/build.py", "tools/ci_step.py", "js/run.js", "tools/chain.py"):
        assert verdicts[named].klass == "named" and not verdicts[named].plain, named
    assert verdicts["src/free.py"].plain and verdicts["docs/build.md"].plain, "a plain file naming another names nothing"
    assert wired(paths, ["Makefile"], lambda path: held[path]) == {"scripts/build.py": "Makefile", "tools/chain.py": "scripts/build.py"}


def test_a_name_relative_to_the_naming_files_directory_and_a_dotted_spelling_reach_the_same_file():
    paths = ["a/Makefile", "a/gen.sh", "b/gen.sh"]
    held = {"a/Makefile": b"x: ; sh ./gen.sh ../b/gen.sh\n"}
    assert set(wired(paths, ["a/Makefile"], lambda path: held.get(path))) == {"a/gen.sh", "b/gen.sh"}


@pytest.mark.parametrize("data, mode, links", [(b"#!/bin/sh\necho\n", 0o644, 1), (TEXT, 0o755, 1), (TEXT, 0o4644, 1), (TEXT, 0o644, 2), (b"x\0y", 0o644, 1),
                                                (b"\xff\xfe", 0o644, 1), (b"\x7fELF\x02", 0o644, 1), (b"\xcf\xfa\xed\xfe\x07", 0o644, 1),
                                                (b"PK\x03\x04", 0o644, 1), (b"\x1f\x8b\x08", 0o644, 1), (b"\0asm\x01", 0o644, 1),
                                                (b"\xf3\r\r\n\0\0", 0o644, 1), (b"\xca\xfe\xba\xbe", 0o644, 1)])
def test_a_file_that_is_not_a_plain_text_file_is_not_plain_source_whatever_its_name(data, mode, links):
    assert not classify("src/app.py", data, mode, links).plain


@pytest.mark.parametrize("facts", [(None, 0o644, 1), (TEXT, None, 1), (TEXT, 0o644, None), (None, None, None)])
def test_a_fact_the_caller_cannot_read_makes_the_file_non_plain(facts):
    assert not classify("src/app.py", *facts).plain


def test_a_crlf_text_file_with_a_two_character_first_line_is_still_plain_source():
    assert classify("src/app.py", b"//\r\nx = 1\r\n", 0o644, 1).plain


@pytest.mark.parametrize("path", ["src/Makefile", "SRC/MAKEFILE", "src/CONFTEST.PY", "src/.hidden.py", ".a/b.py", "src/a/.b/c.py",
                                  "CLAUDE.md", "src/Claude.MD", "x.PTH", "a/./.claude/x.md", "a//.github/y.md"])
def test_case_unicode_and_spelling_do_not_change_the_class_of_a_name(path):
    assert not verdict(path).plain, path


def test_a_full_width_spelling_of_a_registry_name_is_the_same_name():
    full_width = "".join(chr(ord(letter) + 0xFEE0) for letter in "Makefile")
    assert full_width != "Makefile" and not verdict(f"src/{full_width}").plain


@pytest.mark.parametrize("path", ["src/data.json", "src/data.csv", "src/config.yaml", "src/x.toml", "src/x.xml", "src/x.ini", "src/noextension", "src/x.unknownext",
                                  "src/x.lock", "src/x.exe", "src/x.sql", "src/x.png"])
def test_data_and_unrecognized_extensions_are_non_plain(path):
    assert verdict(path).klass == "data", path


def test_the_classes_have_one_parent_written_sentence_each_and_only_hidden_instruction_named_and_governing_never_apply():
    assert set(runs_later.CLASSES) == {"hidden", "program", "data", "instruction", "named", "governing"}
    assert verdict(".github/CODEOWNERS").governing and verdict("CLAUDE.md").klass == "instruction" and verdict("Makefile").governing
    assert not verdict("src/data.json").governing and not classify("src/app.py", TEXT, 0o755, 1).governing
    assert verdict("src/app.py").sentence == "" and verdict("src/app.py").klass == PLAIN


def test_the_hook_reads_the_part_of_the_answer_path_facts_alone_give_and_a_plain_dot_file_does_not_run():
    assert classify("conftest.py").runs == ecosystems.BUILD and classify("scripts/test").runs == ecosystems.PROGRAM
    assert classify(".git/hooks/pre-commit").runs and classify(".cursorrules").runs and classify("CLAUDE.md").runs
    assert not classify(".gitignore").runs and not classify("src/app.py").runs and not classify("src_copy").runs and not classify("LICENSE").runs


def test_the_name_tables_of_guard_autoexec_are_deleted_and_path_reason_answers_from_the_classifier(tmp_path):
    for table in ("RUNS", "FOLDERS", "FILES", "SUFFIX", "CONFIGS", "HOOKS", "AGENT", "BUILD", "CI", "SHELL_START", "EDITOR"):
        assert not hasattr(autoexec, table), table
    root = str(tmp_path)
    assert autoexec.path_reason("conftest.py", [root]) == ecosystems.BUILD
    assert autoexec.path_reason("src/app.py", [root]) == ""
    assert autoexec.path_reason("evil/x.pth", [root]) == ecosystems.suffixes()[".pth"]


@pytest.mark.parametrize("module", [claude, codex, pi])
def test_every_harness_record_holds_instruction_names_and_each_is_refused_at_any_depth(module):
    assert module.INSTRUCTIONS and all(isinstance(name, str) and name for name in module.INSTRUCTIONS)
    for name in module.INSTRUCTIONS:
        assert classify(name, TEXT, 0o644, 1).klass == "instruction" and classify(f"a/b/{name}", TEXT, 0o644, 1).klass == "instruction"
    assert {runs_later.fold(name) for name in module.INSTRUCTIONS} <= runs_later.harness_names()


def test_a_launcher_without_a_harness_record_fails_the_contract_and_the_hooks_read_the_records_without_importing_a_launcher():
    import tomllib

    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    launchers = {name.removeprefix("poolhouse-") for name, target in scripts.items() if name.startswith("poolhouse-") and target.split(":")[0] in
                 {"poolhouse.claude", "poolhouse.codex", "poolhouse.pi"}}
    assert launchers and launchers <= set(harnesses.RECORDS), f"a launcher has no harness record: {launchers - set(harnesses.RECORDS)}"
    assert all(record for record in harnesses.RECORDS.values())
    probe = ("import sys, time; from poolhouse.guard import runs_later; began = time.perf_counter(); runs_later.classify('CLAUDE.md'); "
             "slow = time.perf_counter() - began > 0.25; "
             "sys.exit(1 if any(m in sys.modules for m in ('poolhouse.claude', 'poolhouse.codex', 'poolhouse.pi')) else 2 if slow else 0)")
    done = subprocess.run([sys.executable, "-c", probe], cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO / "src")}, capture_output=True, check=False)
    assert done.returncode == 0, (done.returncode, "1: a launcher was imported, 2: the first classification took over 0.25 s", done.stderr.decode()[-300:])


def test_the_registry_snapshot_names_its_revision_and_digest_and_holds_the_names_the_design_lists():
    snapshot = registry()
    assert len(snapshot["registry"]["revision"]) == 40 and len(snapshot["registry"]["digest"]) == 64
    assert set(snapshot["plain_kinds"]) == {"programming", "markup", "prose"}
    for name in ("makefile", "dockerfile", "jenkinsfile", "tiltfile", "workspace", "rakefile", "gemfile", "pipfile", "requirements.txt"):
        assert name in snapshot["names"], name
    assert snapshot["extensions"][".py"] == "programming" and snapshot["extensions"][".json"] == "data" and snapshot["extensions"][".md"] in snapshot["plain_kinds"]


def refresher():
    spec = importlib.util.spec_from_file_location("refresh_language_kinds", REPO / "scripts" / "refresh_language_kinds.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTRY_FILE = """\
Alpha:
  type: programming
  extensions: [".al", ".COL"]
  filenames: [Alphafile]
Beta:
  type: data
  extensions: [".al", ".be"]
Gamma:
  type: prose
  extensions: [".ga"]
"""


def test_the_refresh_script_builds_the_snapshot_from_a_registry_file_and_its_check_names_a_difference(tmp_path, capsys):
    source, output = tmp_path / "languages.yml", tmp_path / "language-kinds.json"
    source.write_text(REGISTRY_FILE)
    script = refresher()
    assert script.main(["--source", str(source), "--output", str(output)]) == 0
    made = json.loads(output.read_text())
    assert made["extensions"] == {".al": "data", ".be": "data", ".col": "programming", ".ga": "prose"}, "the strictest kind of its claimants, lower case"
    assert made["names"] == {"alphafile": "programming"} and made["registry"]["revision"] == "local" and len(made["registry"]["digest"]) == 64
    assert script.main(["--source", str(source), "--output", str(output), "--check"]) == 0
    output.write_text(output.read_text().replace('".ga": "prose"', '".ga": "data"'))
    assert script.main(["--source", str(source), "--output", str(output), "--check"]) == 1
    assert "differs from the registry" in capsys.readouterr().err
    source.write_text(REGISTRY_FILE.replace("type: prose", "type: nonsense"))
    with pytest.raises(ValueError, match="type"):
        script.main(["--source", str(source), "--output", str(output)])
