"""Only the three current role names may constrain saved rules."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from poolhouse import chatpolicy, roles, rules as saved

ROOT = Path(__file__).resolve().parents[1]
OLD = ("reader", "operator", "runner")
SCANNED = ["src/poolhouse/roles.py", "src/poolhouse/rules.py", "src/poolhouse/chat.py",
           "docs/agent-roles.md", "docs/assistant-security.md", "README.md", "docs/FEATURES.md",
           "docs/memory.md", "docs/commands.md", "src/poolhouse/cli/reference.py",
           "docs/notes/agent-control-plane.md", "docs/notes/undo-mode.md"]
SCANNED += [str(p.relative_to(ROOT)) for pat in ("tests/test_roles.py", "tests/test_redteam_*.py",
                                                 "tests/test_requests*.py", "tests/test_chat*.py")
            for p in ROOT.glob(pat)]
ROLE_USE = re.compile(
    r"`(?:reader|operator|runner)`|[\"'](?:reader|operator|runner)[\"']|roles?\W+(?:reader|operator|runner)\b"
    r"|\b(?:reader|operator|runner) role\b|/role (?:reader|operator|runner)")
ALLOWED = {"tests/test_role_names.py"}


def test_the_table_uses_the_three_new_names():
    assert tuple(roles.ROLES) == ("read-only", "approve-first", "plan-and-go")
    assert (roles.DEFAULT, roles.TASK_DEFAULT) == ("approve-first", "plan-and-go")
    assert tuple(roles.ROLES) == chatpolicy.ROLE_NAMES


@pytest.mark.parametrize("rel", sorted(set(SCANNED) - ALLOWED))
def test_no_file_uses_an_old_word_as_a_role_name(rel):
    path = ROOT / rel
    if not path.exists():
        pytest.skip(rel)
    hits = [f"{rel}:{n}: {ln.strip()[:100]}" for n, ln in enumerate(path.read_text().splitlines(), 1)
            if ROLE_USE.search(ln)]
    assert not hits, "\n".join(hits)


def _file(tmp_path, version, role):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"schema_version": version, "rules": [
        {"tool": "serve_up", "match": {"model": "quince-2b.gguf"}, "verdict": "always", "role": role}]}))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("role", [*OLD, "unknown", None, False, 0, [], {}])
def test_unsupported_saved_roles_refuse_the_entire_file_without_rewrite(tmp_path, role):
    path = _file(tmp_path, 2, role)
    original = path.read_bytes()
    loaded = saved.Rules(path)
    assert loaded.broken and loaded.rules == []
    for current in chatpolicy.ROLE_NAMES:
        assert loaded.covers("serve_up", {"model": "quince-2b.gguf"}, current, False) is None
    assert path.read_bytes() == original


@pytest.mark.parametrize("role", [*OLD, "unknown", None, False, 0, [], {}])
def test_unsupported_caller_roles_cannot_use_a_global_rule(tmp_path, role):
    loaded = saved.Rules(_file(tmp_path, 2, ""))
    assert not loaded.broken
    assert loaded.covers("serve_up", {"model": "quince-2b.gguf"}, role, False) is None


def test_mixed_supported_and_unsupported_rules_match_nothing(tmp_path):
    path = _file(tmp_path, 2, "approve-first")
    value = json.loads(path.read_text())
    value["rules"].append({**value["rules"][0], "role": "unsupported"})
    path.write_text(json.dumps(value))
    loaded = saved.Rules(path)
    assert loaded.broken and loaded.rules == []
    assert loaded.covers("serve_up", {"model": "quince-2b.gguf"}, "approve-first", False) is None


def test_an_unknown_role_name_in_a_saved_rule_is_refused_not_dropped(tmp_path):
    rules = saved.Rules(_file(tmp_path, 2, "root"))
    assert "role 'root'" in rules.broken and rules.rules == []
    with pytest.raises(ValueError, match="no role"):
        roles.get("operator")
