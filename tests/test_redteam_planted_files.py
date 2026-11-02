"""A planted or hand-edited file the product reads back: the model's window and the system's process ceiling.

`decide/window.py` reads the window of a model from the ``config.json`` in its folder; any tool can write that file. `sandbox/run.py`
reads the highest pid from ``/proc/sys/kernel/pid_max`` when the user's process limit is none. Neither read may crash, take a
number that is not a plain positive whole number, or let a sandbox run start with no room at all.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

from poolhouse.decide import window
from poolhouse.decide.types import DecideError

run = importlib.import_module("poolhouse.sandbox.run")  # the package's `run` name is the function

pytestmark = pytest.mark.redteam

GOOD = {"max_position_embeddings": 32768, "hidden_size": 64, "num_hidden_layers": 2, "num_attention_heads": 4}
FIELDS = list(GOOD)


def planted(folder: Path, content: bytes | str) -> Path:
    (folder / "config.json").write_bytes(content if isinstance(content, bytes) else content.encode())
    return folder


@pytest.mark.parametrize("content", [b"", b"not json", b"\xff\xfe\x00", b"[]", b"null", b'"text"', b"7", b"{" * 5000, b"[" * 5000])
def test_a_config_that_is_not_a_json_object_is_a_refusal_not_a_crash(tmp_path, content):
    with pytest.raises(DecideError, match=r"config\.json"):
        window.of_directory(planted(tmp_path, content))


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [True, False, 0, -1, -(2**70), "32768", "1e5", 1.5, 32768.0, None, [32768], {"n": 1}])
def test_a_window_or_size_that_is_not_a_plain_positive_whole_number_is_refused(tmp_path, field, value):
    with pytest.raises(DecideError, match=rf"no usable {field}"):
        window.of_directory(planted(tmp_path, json.dumps({**GOOD, field: value})))


@pytest.mark.parametrize("inner", [[], "text", 7, None, True])
def test_a_text_config_that_is_not_an_object_never_supplies_the_flat_numbers(tmp_path, inner):
    """A planted ``text_config`` of another type is read as the flat config, never crashed on and never trusted over it."""
    assert window.of_directory(planted(tmp_path, json.dumps({**GOOD, "text_config": inner}))).window == 32768


def test_a_text_config_with_a_bad_number_is_not_rescued_by_the_flat_one(tmp_path):
    with pytest.raises(DecideError, match="no usable max_position_embeddings"):
        window.of_directory(planted(tmp_path, json.dumps({**GOOD, "text_config": {**GOOD, "max_position_embeddings": "9"}})))


def test_a_config_that_is_a_directory_or_a_dangling_link_is_a_refusal(tmp_path):
    (tmp_path / "config.json").mkdir()
    with pytest.raises(DecideError, match="cannot be read"):
        window.of_directory(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "config.json").symlink_to(tmp_path / "nowhere.json")
    with pytest.raises(DecideError, match="cannot be read"):
        window.of_directory(other)


@pytest.mark.parametrize("content", ["", "garbage", "-1", "0", "1e9", "99999999999999999999999999 ", "4194304\x00junk", "\xff"])
def test_a_hostile_pid_max_leaves_a_run_room_or_no_limit_and_never_crashes(monkeypatch, content):
    if run.resource is None:
        assert run.fair_share() is None  # no process limit on this platform: nothing is read
        return
    monkeypatch.setattr(run.resource, "getrlimit", lambda _which: (0, run.resource.RLIM_INFINITY))
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: content if str(self) == "/proc/sys/kernel/pid_max" else Path.read_bytes(self).decode())
    share = run.fair_share()
    assert share is None or share >= 1


def test_the_share_is_never_less_than_one_however_many_runs_are_alive(monkeypatch):
    if run.resource is None:
        assert run.fair_share() is None
        return
    monkeypatch.setattr(run.resource, "getrlimit", lambda _which: (0, 10))
    monkeypatch.setattr(run, "_tasks_of_user", lambda: os.getpid())
    monkeypatch.setattr(run, "_LIVE", [10**6])
    assert run.fair_share() == 1
