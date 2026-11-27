"""The PreToolUse hook blocks on a real denial; its own failure allows the call and is logged, unless a downloaded file
could then run unnoticed or the call names a launcher file."""

from __future__ import annotations

import io

import pytest

from poolhouse import coding_policy, harnesshook
from poolhouse.guard import fetchlog


def broken(*_args, **_kwargs):
    raise RuntimeError("the classifier broke")


@pytest.fixture
def rail():
    return harnesshook.Rail(coding_policy.Policy("guarded"), "hook-failure", ("/w",), ("/state/harness/ab",))


def answer(rail, name, args):
    return harnesshook.pre({"tool_name": name, "tool_input": args}, rail)["hookSpecificOutput"]


@pytest.mark.parametrize("name,args", [("Bash", {"command": "ls"}), ("Edit", {"file_path": "/w/a"})])
def test_a_failed_classification_allows_the_call_and_logs_it(rail, monkeypatch, capsys, name, args):
    monkeypatch.setattr(harnesshook, "decide", broken)
    assert answer(rail, name, args)["permissionDecision"] == "allow"
    assert "did not block the call" in capsys.readouterr().err


def test_a_failed_classification_still_denies_a_call_naming_a_launcher_file(rail, monkeypatch):
    monkeypatch.setattr(harnesshook, "decide", broken)
    assert answer(rail, "Edit", {"file_path": "/state/harness/ab/settings.json"})["permissionDecision"] == "deny"


def test_a_failed_classification_still_denies_a_shell_call_after_a_download(rail, monkeypatch):
    monkeypatch.setattr(harnesshook, "decide", broken)
    monkeypatch.setattr(fetchlog, "read", lambda agent: ["/w/fetched.sh"])
    assert answer(rail, "Bash", {"command": "sh /w/fetched.sh"})["permissionDecision"] == "deny"
    assert answer(rail, "Edit", {"file_path": "/w/a"})["permissionDecision"] == "allow"


def test_a_shell_call_whose_download_cannot_be_recorded_is_denied_and_another_call_is_not(rail, monkeypatch, capsys):
    monkeypatch.setattr(fetchlog, "note", broken)
    assert answer(rail, "Bash", {"command": "ls"})["permissionDecision"] == "deny"
    assert answer(rail, "Read", {"file_path": "/w/a"})["permissionDecision"] == "allow"
    assert "did not block the call" in capsys.readouterr().err


@pytest.mark.parametrize("argv,said", [(["pre"], "not json"), (["pre", "--nonsense", "x"], "{}")])
def test_an_event_the_hook_cannot_read_allows_the_call(capsys, argv, said):
    out = io.StringIO()
    assert harnesshook.run(argv, stdin=io.StringIO(said), stdout=out) == 0 and out.getvalue() == ""
    assert "did not block the call" in capsys.readouterr().err
