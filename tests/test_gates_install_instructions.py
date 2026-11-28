"""What install-instructions counts: a string telling somebody to run a package installer."""

from __future__ import annotations

from pathlib import Path

import pytest
from gates import install_instructions


def _found(tmp_path: Path, source: str, name: str = "one.py") -> list[str]:
    where = tmp_path / "src" / "poolhouse" / name
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(source, encoding="utf-8")
    return [f.detail for f in install_instructions.find(tmp_path)]


@pytest.mark.parametrize("source", [
    'raise ImportError("ddgs is missing: pip install \'poolhouse[web]\'")\n',
    'HINT = "on macOS: brew install ffmpeg"\n',
    'def fix(extra):\n    return f"python -m pip install poolhouse[{extra}]"\n',
    'FIX = ["pip", "install", "poolhouse[store]"]\n',
    'say("error: install @earendil-works/pi-coding-agent")\n',
    'say("physical identity unavailable; install poolhouse[coordinator]")\n',
])
def test_a_string_telling_somebody_to_run_an_installer_is_found(tmp_path, source: str) -> None:
    assert _found(tmp_path, source)


@pytest.mark.parametrize("source", [
    '"""`pip install` is what this module runs."""\n',
    'def install():\n    """Runs uv pip install."""\n    return 1\n',
    'WAIT = 1\n"""A pip install is a different thing."""\n',
    'ARGV = [python, "-m", "pip", "install", spec]\n',
    'say("the libraries are installed the first time they are needed")\n',
])
def test_documentation_and_a_command_the_code_runs_itself_are_not_found(tmp_path, source: str) -> None:
    assert not _found(tmp_path, source)


def test_the_commands_a_guard_judges_are_data(tmp_path) -> None:
    assert not _found(tmp_path, 'CALLS = ["pip install requests"]\n', "decide/guards/cases.py")
