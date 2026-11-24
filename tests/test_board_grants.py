"""Standing grants from the client side, against a real node: what this device's key signs, what the commands print, and what an agent is refused
at once. Nothing is typed: a command that is not an agent's runs, and one that is an agent's is refused with the reason."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poolhouse import caller, node_launch
from poolhouse.board import grants as board_grants, person as person_session, person_key
from poolhouse.board.client import Denied, Invalid
from poolhouse.fleet.onboard.manifest import Signer

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node):
    node = workspace_node
    node.lead = node.member("lead")
    node.kid = node.member("kid", parent=node.lead)
    return node


def issued(node, who, want, key=None):
    return board_grants.issue(node.session(who), key or node.person, want)


def run(team, *argv, who=None, env=None) -> tuple[int, str, str]:
    """A command of `poolhouse-workspace` as a process with no terminal and no agent marker (or ``env``'s): the exit status, stdout and stderr."""
    done = team.cli(*argv, who=who, env=env)
    return done.returncode, done.stdout, done.stderr


def setup(team, who, *extra):
    """`grants-setup` for the session ``who``: this device's key is made and enrolled on the way, and nobody is asked."""
    return run(team, "grants-setup", "--session", who.name, *extra, who=who)


def test_a_signature_made_in_python_is_one_the_node_verifies_and_only_the_enrolled_key_counts(team):
    lead = team.lead
    gids = team.allow(lead, "link_remove")
    held = board_grants.listing(team.session(lead))
    assert [(g.gid, g.status, g.action, g.mine) for g in held.grants] == [(gids[0], "live", "link_remove", True)]
    assert held.persons == [board_grants.fingerprint(team.person.public)]
    stranger = Signer.generate()
    with pytest.raises(Denied):
        issued(team, lead, board_grants.Want("device_trust", {"kind": "session", "name": lead.name}), key=stranger)
    again = Signer.generate()
    with pytest.raises(Denied):
        board_grants.enrol(team.client, lead.token, board_grants.enrolment(again, "an agent's key"))
    assert [g.action for g in board_grants.listing(team.session(lead)).grants] == ["link_remove"]


def test_a_second_key_is_enrolled_only_with_the_approval_of_the_first(team):
    team.allow(team.lead, "note_verify")
    other = Signer.generate()
    proof = board_grants.enrolment(other, "a second device")
    with pytest.raises(Denied):
        board_grants.enrol(team.client, team.kid.token, proof)
    here = board_grants.here(team.client)
    approved = board_grants.approve(proof, team.person, here)
    done = board_grants.enrol(team.client, team.kid.token, approved)
    assert done["keys"] == 2
    issued(team, team.lead, board_grants.Want("device_trust", {"kind": "session", "name": team.lead.name}), key=other)
    board_grants.unenrol(team.client, team.kid.token, team.person, board_grants.fingerprint(other.public))
    with pytest.raises(Denied, match="used already"):
        board_grants.enrol(team.client, team.kid.token, approved)
    with pytest.raises(Denied, match="for this node"):
        board_grants.enrol(team.client, team.kid.token, board_grants.approve(proof, team.person, board_grants.Where("ab" * 32, here.anchor)))


def test_the_kit_text_of_a_grant_is_plain_ascii(team):
    for text in ("feat/é", "line\nbreak"):
        with pytest.raises(ValueError, match="ASCII"):
            board_grants.sign_issue(team.person, team.board, board_grants.Want("note_verify", {"kind": "session", "name": "x"}, text))
    with pytest.raises(ValueError, match="time"):
        board_grants.duration_ms("soon")
    with pytest.raises(ValueError, match=r"year|days"):
        board_grants.duration_ms("400d")
    assert board_grants.duration_ms("90m") == 5_400_000


def test_a_refusal_names_the_grant_and_this_devices_key_signs_it_for_one_use(team):
    lead = team.lead
    team.allow(lead, "sync_now")  # enrols the key, nothing else
    plain = team.member("plain")
    call = lambda: team.client.call("pool_leave", "", plain.token)  # noqa: E731 - one request, asked twice
    with pytest.raises(Denied) as refused:
        call()
    assert refused.value.grant["action"] == "pool_leave" and refused.value.grant["name"] == plain.name
    team.client.person = lambda token, hint: token if board_grants.issue(team.session(plain), team.person, board_grants.Want(
        hint["action"], {"kind": "session", "name": hint["name"]}, hint["resource"], hint["device"], 120_000, 1)) else ""
    with pytest.raises(Denied) as spent:  # the one use was spent on a call the node then answered: this device is in no pool
        team.client.call("pool_leave", "", plain.token)
    assert "not in a pool" in str(spent.value) and not getattr(spent.value, "grant", None)
    team.client.person = None
    with pytest.raises(Denied):
        call()
    log = board_grants.log(team.session(plain))
    assert [e["op"] for e in log if e["action"] == "pool_leave"] == ["use", "issue"]
    assert [e["user"] for e in log if e["op"] == "use"] == [plain.name]


PERSON_COMMANDS = [["grant", "note_verify", "--until", "1h"], ["revoke-grant", "g-0123456789abcdef"], ["distrust", "--key", "ab" * 32], ["grants-setup"],
                   ["person-proof"], ["person-enroll", "--proof", "{}"], ["person-unenroll", "--key", "ab" * 32]]


@pytest.mark.parametrize("argv", PERSON_COMMANDS, ids=lambda argv: argv[0])
def test_the_commands_that_sign_with_the_devices_key_are_refused_to_an_agent_at_once_with_the_reason_and_make_no_key(team, argv):
    lead = team.lead
    argv = [*argv, "--to", lead.name] if argv[0] == "grant" else argv
    marked = team.cli(*argv, "--json", who=lead, env={"CLAUDECODE": "1"})
    said = json.loads(marked.stdout)["error"]
    assert marked.returncode == 3 and "refused to an agent" in said and "terminal outside the agent" in said, marked.stdout
    assert board_grants.listing(team.session(lead)).grants == [] and board_grants.listing(team.session(lead)).persons == []
    assert not person_key.kept(team.state), "refused before a key was made"
    row = [r for r in caller.audit_rows() if r["event"] == "refused"][-1]
    assert "agent CLAUDECODE" in row["who"] and row["why"], "the refusal is on the log with who asked and why"


def test_a_process_with_no_agent_marker_runs_the_same_commands_with_no_terminal_and_each_run_is_on_the_log(team):
    lead = team.lead
    code, out, err = run(team, "grant", "note_verify", "--to", lead.name, "--until", "1h", "--json", who=lead)
    assert code == 0 and json.loads(out.strip().splitlines()[-1])["action"] == "note_verify", (out, err)
    assert [g.action for g in board_grants.listing(team.session(lead)).grants] == ["note_verify"]
    assert [r["event"] for r in caller.audit_rows() if r.get("action") == "granting"] == ["ran"]


def test_a_person_sets_up_the_lead_grants_once_and_every_item_is_revoked_by_its_own_id(team):
    lead = team.lead
    code, out, err = setup(team, lead, "--days", "7", "--json")
    assert code == 0, (out, err)
    assert "Type yes" not in out + err, "nobody is asked anything"
    for action in board_grants.DEFAULT_ACTIONS:
        assert action in out + err, "the exact list is printed"
    assert json.loads(out.strip().splitlines()[-1])["key_enrolled"] is True, "the device's key was made and enrolled on the way"
    assert set(board_grants.DEFAULT_ACTIONS) == {"task_authority", "note_verify", "shard_call"}
    assert "shard_consent" not in out and "sync_now" not in out, "what turns test shards on, and a sync, are granted by a person's own command"
    held = board_grants.listing(team.session(lead))
    assert sorted(g.action for g in held.grants) == sorted(board_grants.DEFAULT_ACTIONS)
    assert all(g.status == "live" and g.to["kind"] == "session" and g.to["name"] == lead.name for g in held.grants)
    assert {g.to.get("subagents", False) for g in held.grants if g.action == "note_verify"} == {True}
    assert {g.to.get("subagents", False) for g in held.grants if g.action != "note_verify"} == {False}
    assert all(g.until_ms - g.at_ms == 7 * 86_400_000 for g in held.grants)
    code, shown, err = run(team, "grants", "--json")
    assert code == 0 and {g["action"] for g in json.loads(shown.strip().splitlines()[-1])} == set(board_grants.DEFAULT_ACTIONS), "a process with no session and no agent marker reads as the device's own screen"
    assert team.cli("grants", "--json", env={"CLAUDECODE": "1"}).returncode == 3, "an agent with no session reads nothing"
    mine = team.cli("grants", "--mine", "--json", who=lead)
    assert {g["action"] for g in json.loads(mine.stdout)} == set(board_grants.DEFAULT_ACTIONS), mine.stdout
    # the lead holds them: task authority works, a subagent's note verification too
    lead_session = team.session(lead)
    assert lead_session.call("task_mode", mode="strict")["mode"] == "strict"
    note = lead_session.note_add("fact", "a claim", "it is so", verify_cmd="true")
    assert team.session(team.kid).note_verify(str(note["seq"]), 0, "ab" * 32).trust == "test-verified", "a subagent verifies under the grant to its parent"
    victim = next(g for g in held.grants if g.action == "task_authority")
    code, out, err = run(team, "revoke-grant", victim.gid, "--reason", "no longer needed", who=lead)
    assert code == 0, (out, err)
    with pytest.raises(Denied):
        lead_session.call("task_mode", mode="open")
    shown = json.loads(team.cli("grants", "--json", who=lead).stdout)
    assert {g["gid"]: g["status"] for g in shown}[victim.gid] == "revoked"
    log = json.loads(team.cli("grant-log", "--json", who=lead).stdout)
    assert [e["op"] for e in log if e["gid"] == victim.gid] == ["revoke", "use", "issue"]


def test_a_process_that_registers_as_the_leads_family_with_a_lying_model_inherits_nothing_from_the_setup(team):
    lead = team.lead
    liar = team.member("liar", model="claude-opus-5-5", harness="totally-qwen")
    assert liar.name.startswith("claude-") and not liar.parent, "it is a main session of the lead's family as far as the node can tell"
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    lead_session, liar_session = team.session(lead), team.session(liar)
    assert lead_session.call("task_mode", mode="strict")["mode"] == "strict"
    for method, params in (("task_mode", {"mode": "open"}), ("shard_consent", {"enabled": False})):
        with pytest.raises(Denied):
            liar_session.call(method, **params)
    with pytest.raises(Denied):
        liar_session.note_verify(str(lead_session.note_add("fact", "t", "b", verify_cmd="true")["seq"]), 0, "ab" * 32)
    assert {g.to["name"] for g in board_grants.listing(liar_session).grants if g.mine} == set(), "the liar holds none of them"


def test_a_class_grant_needs_the_person_to_say_registration_is_open_and_then_does_hold_for_any_process_that_claims_the_family(team):
    lead = team.lead
    liar = team.member("liar", model="claude-opus-5-5", harness="totally-qwen")
    code, out, err = setup(team, lead, "--days", "1")
    assert code == 0, (out, err)
    held = len(board_grants.listing(team.session(lead)).grants)
    for argv in (["grants-setup", "--family", "claude"], ["grant", "shard_call", "--class", "claude", "--until", "1h"]):
        code, out, err = run(team, *argv, who=lead)
        assert code == 2 and "registration is open" in out + err and "--i-understand-registration-is-open" in out + err, (argv, out, err)
    assert len(board_grants.listing(team.session(lead)).grants) == held
    code, out, err = run(team, "grant", "task_authority", "--class", "claude", "--i-understand-registration-is-open", "--until", "1h", who=lead)
    assert code == 0 and "registration is open" in out + err, (out, err)
    assert team.session(liar).call("task_mode", mode="strict")["mode"] == "strict", "a class grant is held by whatever claims the family: that is why it needs the flag"
    assert "class" in {g.to["kind"] for g in board_grants.listing(team.session(lead)).grants}


def test_a_grant_that_names_a_verified_model_says_in_the_listing_that_it_matches_nobody_yet(team):
    lead = team.lead
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    for argv in (["grant", "sync_now", "--account", "claude-sonnet-5-5", "--until", "1h"],
                 ["grant", "sync_now", "--class", "claude", "--verified", "--i-understand-registration-is-open", "--until", "1h"]):
        code, out, err = run(team, *argv, who=lead)
        assert code == 0, (argv, out, err)
    rows = json.loads(team.cli("grants", "--json", who=lead).stdout)
    assert {(r["to"]["kind"], r["matches_nobody_yet"]) for r in rows if r["to"]["kind"] != "session"} == {("account", True), ("class", True)}
    assert not any(r["matches_nobody_yet"] for r in rows if r["to"]["kind"] == "session")
    text = team.cli("grants", who=lead).stdout
    assert text.count("matches nobody") == 2, text


def test_the_grant_command_issues_what_it_says_and_nothing_else(team):
    lead, kid = team.lead, team.kid
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    code, out, err = run(team, "grant", "file_delete", "--to", kid.name, "--scope", "abc*", "--until", "2h", "--uses", "3", "--json", who=lead)
    assert code == 0, (out, err)
    done = json.loads(out.strip().splitlines()[-1])
    assert (done["action"], done["to"], done["on"], done["uses"]) == ("file_delete", kid.name, "abc*", 3), "the result says exactly what was signed"
    held = {g.gid: g for g in board_grants.listing(team.session(kid)).grants}[done["granted"]]
    assert (held.action, held.to, held.scope["resource"], held.uses, held.used, held.mine) == ("file_delete", {"kind": "session", "name": kid.name}, "abc*", 3, 0, True)
    assert 0 < held.until_ms - held.at_ms <= 2 * 3_600_000
    for argv in (["grant", "device_trust", "--until", "1h"], ["grant", "device_trust", "--to", "a", "--class", "claude", "--until", "1h"],
                 ["grant", "push_main", "--to", kid.name, "--until", "1h"], ["grant", "device_trust", "--to", kid.name, "--until", "400d"]):
        code, out, err = run(team, *argv, "--json", who=lead)
        assert code in (2, 3), (argv, out, err)
    code, out, err = run(team, "grant", "task_authority", "--to", kid.name, "--uses", "2", "--device", "any", "--until", "1h", "--json", who=lead)
    assert code in (2, 3) and "names the one device" in out + err, "a use count on an action another device may use names a device: " + out + err
    assert "device_trust" not in {g.action for g in board_grants.listing(team.session(lead)).grants}


def test_a_key_carried_from_another_device_is_enrolled_by_the_key_the_node_holds(team):
    lead = team.lead
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    other = Signer.generate()
    proof = json.dumps(board_grants.enrolment(other, "the laptop"))
    code, out, err = run(team, "person-enroll", "--proof", proof, "--json", who=lead)
    assert code == 0, (out, err)
    assert json.loads(out.strip().splitlines()[-1]) == {"fingerprint": board_grants.fingerprint(other.public), "keys": 2}
    assert person_key.kept(team.state)
    with pytest.raises(Invalid):
        board_grants.enrol(team.client, lead.token, {"public": "zz"})


def test_a_key_is_taken_off_the_node_by_a_key_the_node_holds_and_the_last_key_stays(team):
    lead = team.lead
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    other = Signer.generate()
    code, out, err = run(team, "person-enroll", "--proof", json.dumps(board_grants.enrolment(other, "a lost laptop")), who=lead)
    assert code == 0, (out, err)
    lost = board_grants.fingerprint(other.public)
    issued(team, lead, board_grants.Want("device_trust", {"kind": "session", "name": lead.name}), key=other)
    assert "device_trust" in {g.action for g in board_grants.listing(team.session(lead)).grants if g.status == "live"}
    code, out, err = run(team, "person-unenroll", "--key", lost, "--json", who=lead, env={"CLAUDECODE": "1"})
    assert code == 3 and lost in board_grants.listing(team.session(lead)).persons, "an agent's removal of a key signs nothing"
    code, out, err = run(team, "person-unenroll", "--key", lost, "--json", who=lead)
    assert code == 0 and lost[:16] in out, (out, err)
    held = board_grants.listing(team.session(lead))
    assert lost not in held.persons and len(held.persons) == 1
    assert {g.status for g in held.grants if g.action == "device_trust"} == {"ignored"}, "what the removed key signed counts no more"
    mine = board_grants.fingerprint(person_key.keys(team.state).public)
    code, out, err = run(team, "person-unenroll", "--key", mine, who=lead)
    assert code == 3 and "last person key" in out + err, (out, err)
    assert board_grants.listing(team.session(lead)).persons == [mine]


def test_a_node_that_lost_its_only_usable_key_is_started_again_by_deleting_persons_json_with_the_node_stopped(team):
    lead = team.lead
    team.allow(lead, "sync_now")
    assert board_grants.listing(team.session(lead)).persons == [board_grants.fingerprint(team.person.public)]
    team.stop()
    (team.state / "persons.json").unlink()
    team.start()
    assert board_grants.listing(team.session(lead)).persons == [], "the node holds no key and trusts no grant signed by one"
    assert {g.status for g in board_grants.listing(team.session(lead)).grants} == {"ignored"}
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    assert len(board_grants.listing(team.session(lead)).persons) == 1


def test_hostile_arguments_to_the_person_commands_are_refused_before_anything_is_written(team):
    lead, kid = team.lead, team.kid
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    held = len(board_grants.listing(team.session(lead)).grants)
    fine = ["--to", kid.name, "--until", "1h"]
    hostile = [
        ["grant", "note_verify", "--to", "x" * 500, "--until", "1h"], ["grant", "note_verify", "--to", "bad session name", "--until", "1h"],
        ["grant", "note_verify", *fine, "--scope", "café"], ["grant", "note_verify", *fine, "--scope", "a*b"],
        ["grant", "note_verify", *fine, "--scope", "feat/../x"], ["grant", "note_verify", *fine, "--scope", "x" * 1000],
        ["grant", "note_verify", "--to", kid.name, "--until", "0d"], ["grant", "note_verify", "--to", kid.name, "--until", "-1h"],
        ["grant", "note_verify", "--to", kid.name, "--until", "99999999999d"], ["grant", "note_verify", *fine, "--uses", "-3"],
        ["grant", "note_verify", *fine, "--uses", "2000000"], ["grant", "note_verify", *fine, "--device", "not-a-device"],
        ["grant", "release_main", *fine], ["grant", "sudo", *fine], ["grant", "", *fine], ["grant", "device_trust\nnote_verify", *fine],
        ["revoke-grant", "g-nope"], ["revoke-grant", "../../etc/passwd"], ["revoke-grant", "g-" + "0" * 16],
        ["person-enroll", "--proof", "{not json"], ["person-enroll", "--proof", '{"public": 5}'], ["person-enroll", "--proof", "[]"],
        ["person-enroll", "--proof", '{"public": "ab", "about": "\\u001b[2J\\u001b[Hlet the key", "at_ms": 1, "proof": "00"}'],
        ["rebind", "claude-code", "no-such-session"], ["rebind", "x" * 500, lead.name], ["rebind", "", ""], ["rebind", "../x", "ab\ncd"], ["rebind", "claude-code"],
        ["person-unenroll", "--key", "nope"], ["person-unenroll", "--key", "ab\ncd"], ["person-unenroll", "--key", "ab" * 32],
        ["grants-setup", "--session", "bad session name"], ["grants-setup", "--session", "claude-000000"], ["grants-setup", "--session", lead.name, "--days", "0"],
        ["grants-setup", "--session", lead.name, "--days", "9999"], ["grants-setup", "--session", kid.name], ["grants-setup", "--session", ""],
    ]
    for argv in hostile:
        code, out, err = run(team, *argv, "--json", who=lead)
        assert code in (2, 3), (argv, code, out, err)
    after = board_grants.listing(team.session(lead))
    assert len(after.grants) == held and after.ignored == [] and len(after.persons) == 1


def test_the_devices_screen_signs_for_one_use_at_once_and_a_marked_process_gets_what_an_agent_may_have_and_is_refused_the_rest(team, monkeypatch):
    monkeypatch.setattr(node_launch, "default_state", lambda: team.state)
    team.keep_person_key(team.lead)
    screen = person_session.session(Path.cwd())
    assert board_grants.listing(screen).grants == [], "the screen's own session holds no grant"
    assert screen.call("task_mode", mode="strict")["mode"] == "strict", "refused for want of a grant, signed for one use at once, and sent again"
    log = board_grants.log(screen)
    assert [e["op"] for e in log if e["action"] == "task_authority"] == ["use", "issue"]
    spent = next(g for g in board_grants.listing(screen).grants if g.action == "task_authority")
    assert (spent.to, spent.uses, spent.used, spent.status) == ({"kind": "session", "name": screen.name}, 1, 1, "exhausted")
    assert spent.until_ms - spent.at_ms == person_key.LASTS_MS
    monkeypatch.setenv("CLAUDECODE", "1")
    assert screen.call("task_mode", mode="open")["mode"] == "open", "a grant an agent may have is signed for it too"
    with pytest.raises(Denied) as refused:
        screen.call("member_remove", fingerprint="ab" * 32)
    assert refused.value.grant["action"] == "member_remove", "a grant that ends another device's place in the pool is refused to an agent: the node's refusal stands"
    assert [e["op"] for e in board_grants.log(screen) if e["action"] == "member_remove"] == []
    assert any(r["event"] == "grant.refused" and r["action"] == "member_remove" and "agent CLAUDECODE" in r["who"] for r in caller.audit_rows())
