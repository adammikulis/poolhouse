"""An agent that widens its own access: naming a lead it is not, loosening the registry, or forging the file."""

from __future__ import annotations

from pathlib import Path

import pytest

from poolhouse import authority, home
from poolhouse.person import HumanRequired

pytest_plugins = ["node_kit"]


def record(name: str, parent: str = "", *, token: bool = True) -> None:
    """What the node leaves beside a session's token when it registers: the token and the parent it named."""
    folder = home.state("node") / "client" / "board"
    folder.mkdir(parents=True, exist_ok=True)
    if token:
        (folder / f"{name}.token").write_text("t\n", encoding="utf-8")
    (folder / f"{name}.parent").write_text(parent + "\n", encoding="utf-8")


def env(name: str | None, **more: str) -> dict[str, str]:
    marked = {"CLAUDECODE": "1", **more}
    return {**marked, "POOLHOUSE_WORKSPACE_AGENT": name} if name is not None else marked


@pytest.fixture(autouse=True)
def registry(monkeypatch, tmp_path):
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    for name in (*authority.DELEGATING, "POOLHOUSE_NONINTERACTIVE", "POOLHOUSE_WORKSPACE_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))


def passes(name: str | None) -> bool:
    try:
        return authority.require("runtime.deploy", "deploy", (False, False), env(name)) == authority.DELEGATED
    except HumanRequired:
        return False


def test_a_marked_process_with_no_name_passes_no_delegated_gate():
    assert not passes(None)
    assert not passes("")


def test_a_name_the_node_never_recorded_passes_no_delegated_gate():
    assert not passes("agent-feedface")


def test_a_name_with_a_parent_record_is_a_helper_not_a_lead():
    record("agent-aaaaaa")
    record("claude-bbbbbb", parent="agent-aaaaaa")
    assert passes("agent-aaaaaa")
    assert not passes("claude-bbbbbb")


def test_a_parent_record_without_a_token_is_not_a_lead():
    record("agent-cccccc", token=False)
    assert not passes("agent-cccccc")


def test_a_name_that_is_a_path_or_a_pattern_is_not_a_lead():
    record("agent-dddddd")
    for name in ("../agent-dddddd", "agent-dddddd/x", "*", "agent-dddddd\n", "A" * 3):
        assert not passes(name)


def test_a_helper_recorded_on_any_board_is_a_helper():
    record("agent-eeeeee")
    other = Path(home.state("node")) / "client" / "second"
    other.mkdir(parents=True)
    (other / "agent-eeeeee.token").write_text("t\n", encoding="utf-8")
    (other / "agent-eeeeee.parent").write_text("agent-aaaaaa\n", encoding="utf-8")
    assert not passes("agent-eeeeee")
