"""A machine that joined the node's pool lists it with `poolhouse-members ls` and needs no pool key file."""

import argparse
import json
import os

import pytest

from poolhouse import node_launch, node_pool
from poolhouse.fleet import members
from poolhouse.fleet.discovery import DiscoveryError

POOL = {"pool": "0123456789abcdef", "project": "ab" * 8, "policy": "open", "members": [
    {"fingerprint": "a" * 64, "name": "desk", "status": "active", "self": True, "connected": False, "addr": None},
    {"fingerprint": "b" * 64, "name": "", "status": "active", "self": False, "connected": True, "addr": "192.0.2.9:7447"},
    {"fingerprint": "c" * 64, "name": "gone", "status": "revoked", "self": False, "connected": False, "addr": None}]}


def args(tmp_path, *, as_json=False):
    return argparse.Namespace(pool_key=str(tmp_path / "no-such.key"), timeout=0.1, json=as_json)


def test_only_active_members_are_listed_with_who_is_this_machine():
    rows = node_pool.rows(POOL)
    assert [(r["name"], r["state"]) for r in rows] == [("desk", "this machine"), ("b" * 12, "connected")]
    assert rows[1]["addr"] == "192.0.2.9:7447"


def test_the_network_arguments_ask_for_the_lan_and_pass_a_project_on():
    assert node_pool.network_args() == ["--lan"]
    assert node_pool.network_args(project="team", project_dir="/r") == ["--lan", "--project", "team", "--project-dir", "/r"]


def test_ls_without_a_key_file_prints_the_pool_of_the_running_node(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(node_pool, "status", lambda state=None: POOL)
    assert members.cmd_ls(args(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "0123456789abcdef" in out and "desk" in out and "192.0.2.9:7447" in out and "gone" not in out
    assert members.cmd_ls(args(tmp_path, as_json=True)) == 0
    assert [m["name"] for m in json.loads(capsys.readouterr().out)["members"]] == ["desk", "b" * 12]


def test_ls_without_a_key_file_and_without_a_node_still_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(node_pool, "status", lambda state=None: None)
    with pytest.raises(DiscoveryError, match="no pool key"):
        members.cmd_ls(args(tmp_path))


def test_status_is_none_when_no_node_answers(tmp_path):
    assert node_pool.status(tmp_path / "empty") is None
    found, why = node_pool.ask(tmp_path / "empty")
    assert found is None and why


@pytest.mark.parametrize(("own", "extra", "started"), [
    (True, None, ["--lan"]), (False, None, []), (True, ["--listen", "127.0.0.1:0"], ["--listen", "127.0.0.1:0"])])
def test_the_devices_own_node_starts_on_the_lan_and_a_scratch_node_stays_local(tmp_path, monkeypatch, own, extra, started):
    own_state, scratch = tmp_path / "own", tmp_path / "scratch"
    seen: list[list[str]] = []
    monkeypatch.delenv(node_launch.LAN_ENV)  # the suite sets it off for itself; this test is about the default
    monkeypatch.setattr(node_launch, "default_state", lambda: own_state)
    monkeypatch.setattr(node_launch, "node_health", lambda state: {"ok": True} if seen else None)
    monkeypatch.setattr(node_launch.node_supervise, "resolve", lambda state: None)
    monkeypatch.setattr(node_launch, "supervised", lambda state: False)
    monkeypatch.setattr(node_launch, "_start_supervisor", lambda state, words: seen.append(words))
    node_launch.ensure_node(None if own else scratch, extra=extra, wait_s=1)
    assert seen == [started]


def test_under_the_test_suite_the_devices_own_node_starts_local_and_never_on_the_lan(tmp_path, monkeypatch):
    assert os.environ[node_launch.LAN_ENV] == "off", "the suite sets it for itself and every process it starts"
    own_state = tmp_path / "own"
    seen: list[list[str]] = []
    monkeypatch.setattr(node_launch, "default_state", lambda: own_state)
    monkeypatch.setattr(node_launch, "node_health", lambda state: {"ok": True} if seen else None)
    monkeypatch.setattr(node_launch.node_supervise, "resolve", lambda state: None)
    monkeypatch.setattr(node_launch, "supervised", lambda state: False)
    monkeypatch.setattr(node_launch, "_start_supervisor", lambda state, words: seen.append(words))
    node_launch.ensure_node(None, wait_s=1)
    assert seen == [[]], "no --lan: a node a test starts is reachable from this machine only"


WSL_FIX = "this device looks like WSL2 behind NAT: set networkingMode=mirrored in .wslconfig and run wsl --shutdown, or run `python -m poolhouse.device_setup --yes` on it"
NONE_FIX = "no member can dial it yet: have it dial a member, or give it an address"


def reaching(**states):
    """A pool of this device and one member per ``name=(reach, hint)``."""
    members = [{"fingerprint": "a" * 64, "name": "desk", "status": "active", "self": True, "reach": "self", "reach_hint": "", "addr": None}]
    members += [{"fingerprint": f"{i:02x}" * 32, "name": name, "status": "active", "self": False, "connected": reach == "channel",
                 "addr": "192.0.2.9:7447" if reach == "direct" else None, "reach": reach, "reach_hint": hint}
                for i, (name, (reach, hint)) in enumerate(states.items(), start=1)]
    return {"pool": "0123456789abcdef", "project": "", "policy": "open", "members": members}


SHOWN = reaching(laptop=("direct", ""), wsl=("channel", WSL_FIX), lost=("none", NONE_FIX))


def test_every_member_row_says_how_it_is_reached_and_what_to_do_when_it_is_not_direct():
    rows = {r["name"]: r for r in node_pool.rows(SHOWN)}
    assert [(n, r["reach"]) for n, r in rows.items()] == [("desk", "self"), ("laptop", "direct"), ("wsl", "channel"), ("lost", "none")]
    assert rows["laptop"]["reach_hint"] == "" and rows["wsl"]["reach_hint"] == WSL_FIX and rows["lost"]["reach_hint"] == NONE_FIX
    assert [m["reach"] for m in json.loads(node_pool.render(SHOWN, as_json=True))["members"]] == ["self", "direct", "channel", "none"]


def test_a_node_that_does_not_say_how_a_member_is_reached_is_shown_as_unknown_not_as_direct():
    assert {r["reach"] for r in node_pool.rows(POOL)} == {"unknown"}


def test_the_members_table_has_a_reach_column_and_one_line_with_the_fix_for_each_member_not_reached_directly():
    text = node_pool.render(SHOWN, as_json=False)
    header, *body = text.splitlines()[1:]
    column = header.index("REACH")
    assert column < header.index("ADDRESS") and [line[column:].split()[0] for line in body[:4]] == ["self", "direct", "channel", "none"]
    assert f"wsl: {WSL_FIX}" in text and f"lost: {NONE_FIX}" in text and "laptop:" not in text


def test_runtime_status_lists_the_members_not_reached_directly_with_their_fix():
    lines = node_pool.reach_lines(SHOWN)
    assert lines == [f"reach        wsl: channel ({WSL_FIX})", f"reach        lost: none ({NONE_FIX})"]
    assert node_pool.reach_lines(None) == [] and node_pool.reach_lines(reaching(laptop=("direct", ""))) == []


def test_the_doctor_fails_a_member_nothing_reaches_and_passes_one_reached_over_its_channel_with_the_note():
    found = {f.name: f for f in node_pool.reach_findings(SHOWN)}
    assert list(found) == ["reach: laptop", "reach: wsl", "reach: lost"], "this device is not a finding about itself"
    assert [f.good for f in found.values()] == [True, True, False]
    assert found["reach: wsl"].note == WSL_FIX and found["reach: lost"].note == NONE_FIX and found["reach: lost"].said == "none"
    assert node_pool.reach_findings(None) == []
