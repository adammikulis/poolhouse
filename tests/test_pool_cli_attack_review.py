"""Independent hostile probes of the canonical pool command."""

import json

import pytest

from poolhouse import errors, pool_cli


@pytest.mark.parametrize("argv", [
    ["join", "--mode", "dev"],
    ["join", "--passphrase", "unsafe", "--group", "other"],
    ["--pool-key", "other.key", "join"],
    ["join", "code"],
    ["leave", "--keep-running"],
])
def test_old_membership_controls_never_select_python_fallback(argv, monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("membership dispatched before parser rejected legacy controls")
    monkeypatch.setattr(pool_cli, "execute", unexpected)
    with pytest.raises(SystemExit) as stopped:
        pool_cli.main(argv)
    assert stopped.value.code == 2


@pytest.mark.parametrize("argv", [["join", "code", "--host", "device.invalid"], ["leave"]])
def test_dead_node_never_reports_completed(argv, monkeypatch, capsys):
    def unavailable(*_args, **_kwargs):
        raise errors.NotRunning("node unavailable")
    monkeypatch.setattr(pool_cli.pool, "join", unavailable)
    monkeypatch.setattr(pool_cli._node, "session", unavailable)
    assert pool_cli.main([*argv, "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "node unavailable"}


def test_owner_refusal_preserves_required_approval(monkeypatch, capsys):
    def denied(*_args, **_kwargs):
        raise pool_cli.pool.Refusal("approval declined", needs="approval", where="device", then="stop")
    monkeypatch.setattr(pool_cli.pool, "join", denied)
    assert pool_cli.main(["join", "code", "--host", "device.invalid", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "approval declined", "needs": "approval", "where": "device", "then": "stop",
    }
