"""`runtime.rerun`: a person's script runs again, isolated, under the selected runtime, the one copy of Poolhouse
the machine's keystore admits; and the scripts that keep a key there use it before they import anything else."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import real_machine, runtime

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SCRIPTS = ("agent-identity", "release-key")
A = "a" * 40


def selected(prefix: Path) -> runtime.Runtime:
    return runtime.Runtime(prefix, A, "1", "id")


def test_a_script_runs_again_isolated_under_the_selected_runtime_without_source_paths(tmp_path, monkeypatch):
    calls = []
    chosen = selected(tmp_path / "prefix")
    monkeypatch.setattr(runtime, "available", lambda: chosen)
    monkeypatch.setattr(runtime.os, "execve", lambda *given: calls.append(given))
    monkeypatch.setenv("PYTHONPATH", str(SRC))
    runtime.rerun(Path("/x/scripts/tool"), ["check", "--repo", "o/r"])
    [(path, argv, env)] = calls
    assert path == str(chosen.python)
    assert argv == [str(chosen.python), "-I", "/x/scripts/tool", "check", "--repo", "o/r"]
    assert "PYTHONPATH" not in env


def test_no_selected_runtime_leaves_the_script_where_it_is(monkeypatch):
    monkeypatch.setattr(runtime, "available", lambda: None)
    monkeypatch.setattr(runtime.os, "execve", lambda *_given: pytest.fail("replaced the process"))
    runtime.rerun(Path("/x/scripts/tool"), [])


def test_a_process_already_in_the_selected_runtime_is_not_replaced(monkeypatch):
    monkeypatch.setattr(runtime, "available", lambda: selected(Path(sys.prefix)))
    monkeypatch.setattr(runtime.os, "execve", lambda *_given: pytest.fail("replaced the process"))
    runtime.rerun(Path("/x/scripts/tool"), [])


def test_an_isolated_process_is_never_replaced_again(tmp_path):
    code = (f"import os, sys; sys.path.insert(0, {str(SRC)!r}); from poolhouse import runtime\n"
            f"runtime.available = lambda: runtime.Runtime(__import__('pathlib').Path({str(tmp_path)!r}), {A!r}, '1', 'id')\n"
            "os.execve = lambda *given: print('REPLACED')\n"
            "runtime.rerun(__import__('pathlib').Path('x'), [])\nprint('KEPT')")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert done.stdout.split() == ["KEPT"], done.stderr


def test_a_checkout_copy_never_gets_the_real_keystore_only_the_installed_runtime_does(tmp_path, monkeypatch):
    real = tmp_path / "real"
    monkeypatch.setenv("POOLHOUSE_HOME", str(real))
    here = SRC / "poolhouse" / "real_machine.py"
    assert "not the one installed" in real_machine.refusal(under_test=False, root=real, module=here)
    installed = real / "runtimes" / "x" / "lib" / "poolhouse" / "real_machine.py"
    assert real_machine.refusal(under_test=False, root=real, module=installed) == ""


def needs_the_runtime(node: ast.AST) -> bool:
    """Whether the node imports what only the selected runtime is sure to hold."""
    if isinstance(node, ast.Import):
        return any(a.name.split(".")[0] in ("jwt", "cryptography")
                   or a.name == "poolhouse.workspace.github_identity" for a in node.names)
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] == "cryptography" or (
            node.module == "poolhouse.workspace.github_identity") or (
            node.module == "poolhouse.workspace" and any(a.name == "github_identity" for a in node.names)) or (
            node.module == "poolhouse" and any(a.name in ("credentials", "person") for a in node.names))
    return False


def assert_runtime_import_order(text: str) -> None:
    """Apply the same rerun and isolation guard to a script and its hostile mutations."""
    tree = ast.parse(text)
    main = next(n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test))
    assert "runtime.rerun(Path(__file__).resolve(), sys.argv[1:])" in ast.unparse(main)
    later = [n.lineno for n in ast.walk(tree) if needs_the_runtime(n)]
    assert later and min(later) > main.lineno, "sensitive imports must follow runtime rerun"
    assert "if not sys.flags.isolated:\n    sys.path.insert(0" in text
    assert text.index("sys.flags.isolated") < text.index("runtime.rerun")
    assert "os.execve" not in text


@pytest.mark.parametrize("name", SCRIPTS)
def test_a_script_that_keeps_a_key_reruns_before_it_imports_what_it_needs(name):
    assert_runtime_import_order((ROOT / "scripts" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("statement", [
    "import poolhouse.workspace.github_identity",
    "import poolhouse.workspace.github_identity as holder",
    "from poolhouse.workspace import github_identity as holder",
    "from poolhouse.workspace.github_identity import command as run",
])
def test_packaged_holder_imports_require_the_selected_runtime(statement):
    assert needs_the_runtime(ast.parse(statement).body[0])


def test_moving_the_committed_holder_import_before_rerun_fails_the_same_guard():
    text = (ROOT / "scripts" / "agent-identity").read_text(encoding="utf-8")
    assert_runtime_import_order(text)
    tree = ast.parse(text)
    holder = next(node for node in tree.body if isinstance(node, ast.ImportFrom)
                  and node.module == "poolhouse.workspace.github_identity")
    main = next(node for node in tree.body if isinstance(node, ast.If)
                and "__main__" in ast.unparse(node.test))
    lines = text.splitlines(keepends=True)
    moved = lines.pop(holder.lineno - 1)
    lines.insert(main.lineno - 1, moved)
    mutated = "".join(lines)
    assert mutated != text
    with pytest.raises(AssertionError, match="sensitive imports must follow runtime rerun"):
        assert_runtime_import_order(mutated)
