"""The oracle of docs/design-isolated-launcher.md 4.4.2: run the real tool on a marker-writing payload at each candidate name.

Every name a tool ran without being told to must be non-plain by `guard.runs_later` (structure, the registry, a harness or an ecosystem
record, or the files a non-plain file names). A name that left a marker and is plain fails the test, and the fix is to add it to the record
of its ecosystem (`guard/ecosystems.py`). The oracle measures what tools do; the classifier never reads its output at run time. Source a
tool only runs because something imports it or the person asks for it (a test file, a module) is plain source by design and not a candidate.
"""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse.guard import runs_later

PYTHON_PAYLOAD = "import os\nopen(os.path.join(os.environ['ORACLE_MARKERS'], {slug!r}), 'w').close()\n"
MAKE_PAYLOAD = "$(shell touch \"$(ORACLE_MARKERS)/{slug}\")\nall: ;\n"
SHELL_PAYLOAD = "#!/bin/sh\ntouch \"$ORACLE_MARKERS/{slug}\"\n"
RUST_PAYLOAD = "fn main() {{ std::fs::write(std::env::var(\"ORACLE_MARKERS\").unwrap() + \"/{slug}\", \"\").unwrap(); }}\n"
JS_PAYLOAD = "require('fs').writeFileSync(process.env.ORACLE_MARKERS + '/{slug}', '');\n"
CLEAN = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def slug(name: str) -> str:
    return name.replace("/", "__")


def run(argv: list[str], cwd: Path, markers: Path, **extra: str) -> subprocess.CompletedProcess:
    env = {**CLEAN, "HOME": str(cwd / "home"), "ORACLE_MARKERS": str(markers), **extra}
    (cwd / "home").mkdir(exist_ok=True)
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=180, check=False)


def plant(root: Path, name: str, payload: str, mode: int = 0o644) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.format(slug=slug(name)))
    path.chmod(mode)


def fired(markers: Path) -> set[str]:
    return {entry.name.replace("__", "/") for entry in markers.iterdir()}


def must_be_non_plain(names: set[str], root: Path) -> None:
    plain = [name for name in sorted(names) if runs_later.classify(name, (root / name).read_bytes(), (root / name).stat().st_mode, 1).plain]
    assert not plain, f"a tool ran {plain} and the classifier calls them plain source: add them to the record of their ecosystem"


@pytest.fixture
def scratch(tmp_path):
    markers = tmp_path / "markers"
    markers.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    return root, markers


def test_every_name_python_and_pytest_run_without_being_asked_is_non_plain(scratch):
    root, markers = scratch
    names = ["conftest.py", "tests/conftest.py", "sitecustomize.py", "usercustomize.py", "setup.py", "noxfile.py", "pytest_plugin.py", "plugins/helper.py",
             "src/sitecustomize.py", "manage.py", "tasks.py", "fabfile.py", "wsgi.py", "app.py", "main.py", "run.py", "__main__.py"]
    for name in names:
        plant(root, name, PYTHON_PAYLOAD)
    (root / "tests" / "test_probe.py").write_text("def test_probe():\n    pass\n")
    path = {"PYTHONPATH": os.pathsep.join([str(root), str(root / "src")])}
    run([sys.executable, "-c", "pass"], root, markers, **path)
    run([sys.executable, "setup.py", "--version"], root, markers, **path)
    run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "tests"], root, markers, **path)
    ran = fired(markers)
    assert {"conftest.py", "tests/conftest.py", "sitecustomize.py", "setup.py"} <= ran, f"the oracle ran nothing it should have: {sorted(ran)}"
    must_be_non_plain(ran, root)


def test_every_name_make_runs_without_being_asked_is_non_plain(scratch):
    root, markers = scratch
    found = set()
    for index, name in enumerate(("Makefile", "makefile", "GNUmakefile")):
        fresh = root / f"case{index}"
        fresh.mkdir()
        plant(fresh, name, MAKE_PAYLOAD)
        run(["make", "-n"], fresh, markers)
        found |= {f"{fresh.name}/{each}" for each in fired(markers)}
        shutil.rmtree(markers)
        markers.mkdir()
        planted = fresh / name
        assert not runs_later.classify(name, planted.read_bytes(), planted.stat().st_mode, 1).plain, name
    assert len(found) == 3, f"make ran {sorted(found)}"


def test_a_file_a_makefile_and_a_package_manifest_name_is_non_plain_because_the_tool_runs_what_they_name(scratch):
    root, markers = scratch
    plant(root, "Makefile", "all:\n\tsh scripts/build.sh\n")
    plant(root, "scripts/build.sh", SHELL_PAYLOAD)
    plant(root, "scripts/plain-looking.py", PYTHON_PAYLOAD)
    plant(root, "package.json", '{{"scripts": {{"test": "node tools/check.js"}}}}\n')
    plant(root, "tools/check.js", JS_PAYLOAD)
    run(["make"], root, markers)
    run(["npm", "test", "--offline", "--no-audit", "--no-fund", "--no-update-notifier"], root, markers, npm_config_cache=str(root / "npmcache"))
    ran = fired(markers)
    assert ran == {"scripts/build.sh", "tools/check.js"}, sorted(ran)
    paths = ["Makefile", "scripts/build.sh", "scripts/plain-looking.py", "package.json", "tools/check.js"]
    verdicts = runs_later.tree(paths, lambda path: ((root / path).read_bytes(), (root / path).stat().st_mode, 1))
    assert not verdicts["scripts/build.sh"].plain and not verdicts["tools/check.js"].plain
    assert verdicts["tools/check.js"].klass == "named", "a .js file is source by its extension and non-plain only because package.json names it"
    assert verdicts["scripts/plain-looking.py"].plain, "a file nothing names stays plain"


def usable(program: str) -> bool:
    """Whether ``program`` is on the machine and runs (a rustup shim with no default toolchain is present and cannot build)."""
    return shutil.which(program) is not None and subprocess.run([program, "--version"], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(not usable("cargo"), reason="the oracle runs the tools the machine has: cargo is absent or has no toolchain here")
def test_every_name_cargo_runs_without_being_asked_is_non_plain(scratch):
    root, markers = scratch
    (root / "Cargo.toml").write_text('[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n')
    plant(root, "src/main.rs", "fn main() {{}}\n")
    plant(root, "build.rs", RUST_PAYLOAD)
    account = Path(pwd.getpwuid(os.getuid()).pw_dir)
    built = run(["cargo", "build", "--offline", "--quiet"], root, markers, CARGO_HOME=os.environ.get("CARGO_HOME") or str(account / ".cargo"),
                CARGO_TARGET_DIR=str(root / "target"), RUSTUP_HOME=os.environ.get("RUSTUP_HOME") or str(account / ".rustup"))
    ran = fired(markers)
    assert built.returncode == 0, built.stderr.decode(errors="replace")[-800:]
    assert ran == {"build.rs"}, sorted(ran)
    must_be_non_plain(ran, root)


def test_a_git_hook_runs_and_every_hook_path_is_non_plain(scratch):
    root, markers = scratch
    env = {"GIT_AUTHOR_NAME": "o", "GIT_AUTHOR_EMAIL": "o@example.invalid", "GIT_COMMITTER_NAME": "o", "GIT_COMMITTER_EMAIL": "o@example.invalid"}
    run(["git", "init", "-q"], root, markers, **env)
    names = [".git/hooks/pre-commit", ".git/hooks/commit-msg", ".git/hooks/post-commit"]
    for name in names:
        plant(root, name, SHELL_PAYLOAD, 0o755)
    (root / "a.txt").write_text("x\n")
    run(["git", "add", "a.txt"], root, markers, **env)
    run(["git", "commit", "-q", "-m", "probe"], root, markers, **env)
    ran = fired(markers)
    assert ran == set(names), sorted(ran)
    must_be_non_plain(ran, root)
