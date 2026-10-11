"""SKIP_BUDGETS is a person's override of the commit hook; an agent's is ignored, whatever its command line says."""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "budgets"


class Reached(Exception):
    """The hook went past the override and began to look at the staged files."""


@pytest.fixture
def hook(monkeypatch):
    loader = importlib.machinery.SourceFileLoader("budgets_hook_under_test", str(HOOK))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "BUDGETS", HOOK)  # exists, so main goes on to ask git
    monkeypatch.setattr(module, "_git", lambda *a, **k: (_ for _ in ()).throw(Reached()))
    for name in ("CLAUDECODE", "POOLHOUSE_AGENT", "SKIP_BUDGETS"):
        monkeypatch.delenv(name, raising=False)
    return module


def test_a_person_may_skip(hook, monkeypatch):
    monkeypatch.setenv("SKIP_BUDGETS", "1")
    assert hook.main() == 0


@pytest.mark.parametrize("marker", ["CLAUDECODE", "POOLHOUSE_AGENT"])
def test_an_agent_marker_makes_the_override_nothing(hook, monkeypatch, marker):
    monkeypatch.setenv("SKIP_BUDGETS", "1")
    monkeypatch.setenv(marker, "1")
    with pytest.raises(Reached):
        hook.main()


def test_without_the_override_the_hook_looks_at_the_commit(hook):
    with pytest.raises(Reached):
        hook.main()
