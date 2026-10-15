"""Every checker answers the same from its caches as from a whole run, through edits to the tree.

The tree is a copy of this repository, so the edits are real files in real modules. The
"full" answer is each checker with its caches switched off; the incremental one reads and
writes the caches in a machine-local directory made for the test.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import gates  # noqa: E402
from gates import _floors, _mutation, _perfile, _ruff, _util, pyright_errors  # noqa: E402

pytestmark = pytest.mark.slow

PROBE = '''

def probe_edit():
    print("probe")
    parser = argparse.ArgumentParser()
    try:
        return Path.home()
    except Exception:
        return parser
'''


def copy_of_the_tree(where: Path) -> Path:
    candidates = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "src", "tests"],
        cwd=REPO, capture_output=True, check=True,
    )
    for raw in candidates.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] not in {"src", "tests"}:
            raise ValueError(f"invalid snapshot candidate: {relative}")
        source = REPO / relative
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode) or source.resolve() != REPO.resolve() / relative:
            raise ValueError(f"snapshot candidate is not a regular contained file: {relative}")
        destination = where / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
        after = source.lstat()
        identity = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in identity):
            raise RuntimeError(f"snapshot candidate changed while copying: {relative}")
    shutil.copy(REPO / "pyproject.toml", where / "pyproject.toml")
    (where / "contracts").mkdir()
    for name in ("glossary-rules.toml", "glossary-allowlist.toml"):
        shutil.copy(REPO / "contracts" / name, where / "contracts" / name)
    (where / "scripts" / "gates").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "gates" / "survivors.txt", where / "scripts" / "gates")
    return where


@pytest.fixture
def snapshot_repository(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    files = {".gitignore": "tests/_generated_*/\n", "src/kept.py": "tracked = True\n",
             "tests/test_kept.py": "tracked = True\n",
             "tests/_generated_tracked/test_kept.py": "tracked = True\n",
             "pyproject.toml": "", "contracts/glossary-rules.toml": "",
             "contracts/glossary-allowlist.toml": "", "scripts/gates/survivors.txt": ""}
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "add", "--force", "--", *files], cwd=root, check=True, capture_output=True)
    monkeypatch.setattr(sys.modules[__name__], "REPO", root)
    return root


def test_snapshot_retains_git_candidates_when_an_ignored_artifact_vanishes(
    tmp_path, monkeypatch, snapshot_repository,
):
    root = snapshot_repository
    for name in ("src/untracked.py", "tests/test_untracked.py", "tests/_generated_transient/test_gone.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("candidate = True\n", encoding="utf-8")
    actual_run = subprocess.run

    def enumerate_then_remove(*args, **kwargs):
        result = actual_run(*args, **kwargs)
        if args[0][:2] == ["git", "ls-files"]:
            shutil.rmtree(root / "tests" / "_generated_transient")
        return result

    monkeypatch.setattr(subprocess, "run", enumerate_then_remove)
    copied = copy_of_the_tree(tmp_path / "snapshot")
    selected = {
        "src/kept.py", "src/untracked.py", "tests/test_kept.py", "tests/test_untracked.py",
        "tests/_generated_tracked/test_kept.py",
    }
    assert {path.relative_to(copied).as_posix() for path in copied.rglob("*.py")} == selected
    for name in selected:
        assert (copied / name).read_bytes() == (root / name).read_bytes()


def test_snapshot_refuses_a_missing_listed_candidate(tmp_path, snapshot_repository):
    (snapshot_repository / "src" / "kept.py").unlink()
    with pytest.raises(FileNotFoundError):
        copy_of_the_tree(tmp_path / "snapshot")


def source_files(root: Path) -> list[Path]:
    return sorted((root / "src" / "poolhouse").rglob("*.py"))


def rows(found) -> list[tuple]:
    return sorted((f.path, f.line, f.detail) for f in found)


def answers(root: Path, names: set[str]) -> dict[str, list[tuple]]:
    """The checkers' findings under root, restricted to these metric names."""
    return {c.NAME: rows(gates.findings(c, root)) for c in gates.checkers()
            if c.NAME in names and not gates.skipped(c)}


def fresh_process_memos() -> None:
    """Expire run-local memos as a fresh gate process would, retaining the disk caches."""
    for cached in (_util.read, _util.parse, _util.calls, _util.python_files, _ruff.report):
        cached.cache_clear()


class Recomputed:
    """Counts which files each cached checker had to read, by wrapping ``_perfile.each``."""

    def __init__(self, monkeypatch) -> None:
        self.files: list[str] = []
        real = _perfile.each

        def each(root, paths, compute, owner=None, salt=""):
            def counted(path):
                self.files.append(path.relative_to(root).as_posix())
                return compute(path)

            return real(root, paths, counted, owner=owner or compute, salt=salt)

        for module in [*gates.checkers(), _perfile, _floors, _mutation]:
            if hasattr(module, "each"):
                monkeypatch.setattr(module, "each", each)

    def take(self) -> set[str]:
        seen, self.files = set(self.files), []
        return seen


class Session:
    """A copy of the tree, its checkers, and a way to compare incremental runs with whole ones."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "cache"))
        monkeypatch.delenv("POOLHOUSE_GATES_FULL", raising=False)
        self.root = copy_of_the_tree(tmp_path / "tree")
        self.monkeypatch, self.nowhere = monkeypatch, tmp_path / "nowhere"
        monkeypatch.setattr(_perfile, "REPO", self.root.resolve())
        self.recomputed = Recomputed(monkeypatch)
        self.everything = {c.NAME for c in gates.checkers()}
        self.cheap = self.everything - {pyright_errors.NAME}

    def full(self, names: set[str]) -> dict[str, list[tuple]]:
        fresh_process_memos()
        with self.monkeypatch.context() as whole:
            whole.setattr(_perfile, "REPO", self.nowhere)
            whole.setattr(pyright_errors, "_pyright_incremental",
                          SimpleNamespace(findings=lambda root, run: run(None)))
            return answers(self.root, names)

    def agree(self, label: str, names: set[str] | None = None):
        """The incremental answers and the files they read, after checking them against a full run."""
        names = self.cheap if names is None else names
        fresh_process_memos()
        started = time.monotonic()
        incremental = answers(self.root, names)
        print(f"{label}: incremental {time.monotonic() - started:.1f}s")
        touched = self.recomputed.take()
        assert incremental == self.full(names), label
        self.recomputed.take()
        return incremental, touched

    def append(self, path: Path, text: str) -> str:
        path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")
        return path.relative_to(self.root).as_posix()


def test_every_checker_agrees_with_a_whole_run_through_edits(tmp_path, monkeypatch):
    if pyright_errors.skip():
        pytest.skip(pyright_errors.skip())
    tree = Session(tmp_path, monkeypatch)
    first, touched = tree.agree("cold", tree.everything)
    assert touched
    again, touched = tree.agree("nothing changed")
    assert again == {k: first[k] for k in tree.cheap if k in first}
    assert touched == set()

    leaf = source_files(tree.root)[-1]
    tree.append(leaf, PROBE)
    after_leaf, touched = tree.agree("a leaf source file edited")
    assert touched == {leaf.relative_to(tree.root).as_posix()}
    assert len(after_leaf["print-calls"]) == len(first["print-calls"]) + 1
    assert len(after_leaf["broad-excepts"]) == len(first["broad-excepts"]) + 1

    core = tree.root / "src" / "poolhouse" / "files.py"
    where = tree.append(core, PROBE)
    after_core, touched = tree.agree("a widely imported file edited")
    assert touched == {where}
    assert len(after_core["duplicate-bodies"]) == len(first["duplicate-bodies"]) + 2

    twin = tree.root / "src" / "poolhouse" / "zz_twin.py"
    twin.write_text(core.read_text(encoding="utf-8"), encoding="utf-8")
    after_twin, touched = tree.agree("a file written twice")
    assert touched == {"src/poolhouse/zz_twin.py"}
    assert len(after_twin["duplicate-bodies"]) > len(after_core["duplicate-bodies"])

    where = tree.append(sorted((tree.root / "tests").glob("test_*.py"))[0],
                        "\n\nclass FakeProbe:\n    pass\n")
    after_test, touched = tree.agree("a test file edited")
    assert touched == {where}
    assert len(after_test["ad-hoc-fakes"]) == len(first["ad-hoc-fakes"]) + 1

    twin.unlink()
    after_delete, touched = tree.agree("a file deleted")
    assert touched == set()
    assert after_delete["duplicate-bodies"] == after_core["duplicate-bodies"]

    final, _ = tree.agree("everything, pyright included", tree.everything)
    assert any(f[0] == leaf.relative_to(tree.root).as_posix() for f in final["pyright-errors"])


def test_edited_ruff_findings_agree_with_a_fresh_tool_process(tmp_path, monkeypatch):
    if _ruff.skip():
        pytest.skip(_ruff.skip())
    tree = Session(tmp_path, monkeypatch)
    names = {"ruff-bugbear"}
    first, _ = tree.agree("ruff before the edit", names)
    probe = tree.root / "src" / "poolhouse" / "zz_ruff_probe.py"
    probe.write_text("def probe_edit(value=[]):\n    return value\n", encoding="utf-8")

    command = _ruff.command()
    assert command is not None
    targets = [name for name in _ruff.ROOTS if (tree.root / name).exists()]
    done = subprocess.run([*command, "check", "--no-cache", "--output-format", "json", *targets],
                          cwd=tree.root, capture_output=True, text=True, check=False)
    assert done.returncode == 1, done.stderr
    expected = sorted(
        (Path(item["filename"]).relative_to(tree.root).as_posix(), item["location"]["row"],
         f"{item['code']} {item['message']}")
        for item in json.loads(done.stdout) if item["code"].startswith("B")
        and item["code"][1:].isdigit()
    )
    assert any(path == "src/poolhouse/zz_ruff_probe.py" and detail.startswith("B006 ")
               for path, _, detail in expected)
    incremental, _ = tree.agree("ruff after the edit", names)
    assert incremental["ruff-bugbear"] == expected
    assert incremental["ruff-bugbear"] != first["ruff-bugbear"]


def test_the_repository_itself_agrees_with_a_whole_run(tmp_path, monkeypatch):
    if pyright_errors.skip():
        pytest.skip(pyright_errors.skip())
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "cache"))
    monkeypatch.delenv("POOLHOUSE_GATES_FULL", raising=False)
    names = {c.NAME for c in gates.checkers()}
    cold = answers(REPO, names)
    warm = answers(REPO, names)
    with monkeypatch.context() as whole:
        whole.setattr(_perfile, "REPO", tmp_path / "nowhere")
        whole.setattr(pyright_errors, "_pyright_incremental",
                      SimpleNamespace(findings=lambda root, run: run(None)))
        assert {c.NAME: rows(gates.findings(c, REPO)) for c in gates.checkers()
                if not gates.skipped(c)} == cold == warm
