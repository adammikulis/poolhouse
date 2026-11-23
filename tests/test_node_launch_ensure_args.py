"""`node_launch ensure` with no --arg leaves the node's arguments to `ensure_node` (the machine's own, --lan where allowed)."""
from __future__ import annotations

from poolhouse import node_launch


def test_ensure_without_args_passes_none_so_the_machine_decides(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(node_launch, "ensure_node", lambda state, extra=None: seen.append(extra) or {"action": "running"})
    assert node_launch.main(["ensure"]) == 0
    assert seen == [None], "an empty list would skip the machine's own network arguments"


def test_ensure_with_args_passes_them(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(node_launch, "ensure_node", lambda state, extra=None: seen.append(extra) or {"action": "running"})
    assert node_launch.main(["ensure", "--arg=--lan"]) == 0
    assert seen == [["--lan"]]
