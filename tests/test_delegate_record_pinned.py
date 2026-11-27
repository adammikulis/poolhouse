"""The record of a run, read from the events the real pinned OpenCode prints, in the real confinement and on a computer with none.

It runs where the release is kept under the account's state root and skips elsewhere (the fixtures of tests/test_opencode_pinned.py, which read and
copy it). NOT A MODEL: the model side is the suite's canned server, which asks for the tool calls the test names; no model behaviour is under test.
"""

from __future__ import annotations

import pytest
import test_opencode_pinned as pinned

from poolhouse import delegate_confined, delegate_record

pytest_plugins = ["node_kit", "delegate_kit", "tests.confined_kit"]
release, served, delegated = pinned.release, pinned.served, pinned.delegated


@pytest.mark.parametrize("sandboxed", [True, False], ids=["boxed", "no-sandbox"])
def test_the_record_of_a_run_on_the_pinned_release_holds_each_command_its_exit_status_output_and_test_counts(delegated, monkeypatch, sandboxed):
    if not sandboxed:
        monkeypatch.setattr(delegate_confined, "unsandboxable", lambda: "PLUMBING: this platform has no sandbox")
    body = ("import unittest\n\nclass Plumbing(unittest.TestCase):\n    def test_one(self):\n        pass\n\n"
            "    def test_two(self):\n        self.fail('PLUMBING')\n")
    ran = delegated.run([("write", {"path": "test_plumbing.py", "content": body}),
                         ("shell", {"command": "cat test_plumbing.py OUT-MARK"}),
                         ("shell", {"command": "python3 -m unittest test_plumbing"}),
                         ("shell", {"command": "python3 -m unittest nothing_of_that_name"}),
                         ("read", {"path": "test_plumbing.py"})], "--edit")
    [name] = delegate_record.names()
    events = delegate_record.read(name)[0]
    results = [each for each in events if each["kind"] == "result"]
    assert [each["tool"] for each in results] == ["write", "shell", "shell", "shell", "read"], (events, ran.log[-4000:])
    assert (results[1]["command"], results[1]["exit"]) == ("cat test_plumbing.py OUT-MARK", 1), results[1]
    assert "class Plumbing" in results[1]["output"] and "OUT-MARK: No such file" in results[1]["output"], results[1]
    assert results[1]["seconds"] >= 0 and results[4]["file"] == "test_plumbing.py"
    worked, unstarted = [each for each in events if each["kind"] == "test"]
    assert (worked["passed"], worked["failed"], worked["ok"], worked["setup"]) == (1, 1, False, ""), (worked, results[2]["output"])
    assert unstarted["errors"] == 1 and "No module named" in unstarted["setup"], (unstarted, results[3]["output"])
    kinds = [each["kind"] for each in events]
    for kind in ("run", "model", "attempt", "step", "call", "text", "usage", "attempt_end", "notice", "end", *(["preflight"] if sandboxed else [])):
        assert kind in kinds, (kind, kinds)
    assert any(pinned.SAID in each.get("text", "") for each in events if each["kind"] == "text")
    seen = delegate_record.view(name)
    assert seen["tests"]["errors"] == 1 and "(looks like setup)" in seen["tests_said"] and seen["spent"], seen
    assert [each["about"] for each in events if each["kind"] == "notice" and each["about"] == "test setup"] == ["test setup"]
