"""Standing grants from the client side, against a real node: what a person's key signs, what the commands print, and
what no process that is not at the person's terminal can do."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from node_kit import PASSPHRASE, TYPED

from poolhouse import node_launch, person
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


def setup(team, who, *extra, typed=TYPED):
    """The person runs `grants-setup` for the session ``who`` at a terminal and types what it asks."""
    return team.terminal("grants-setup", "--session", who.name, *extra, who=who, typed=typed)


def test_a_signature_made_in_python_is_one_the_node_verifies_and_only_the_enrolled_key_counts(team):
    lead = team.lead
    gids = team.allow(lead, "land_control")
    held = board_grants.listing(team.session(lead))
    assert [(g.gid, g.status, g.action, g.mine) for g in held.grants] == [(gids[0], "live", "land_control", True)]
    assert held.persons == [board_grants.fingerprint(team.person.public)]
    stranger = Signer.generate()
    with pytest.raises(Denied):
        issued(team, lead, board_grants.Want("land_trust", {"kind": "session", "name": lead.name}), key=stranger)
    again = Signer.generate()
    with pytest.raises(Denied):
        board_grants.enrol(team.client, lead.token, board_grants.enrolment(again, "an agent's key"))
    assert [g.action for g in board_grants.listing(team.session(lead)).grants] == ["land_control"]


def test_a_second_key_is_enrolled_only_with_the_approval_of_the_first(team):
    team.allow(team.lead, "note_verify")
    other = Signer.generate()
    proof = board_grants.enrolment(other, "a second device")
    with pytest.raises(Denied):
        board_grants.enrol(team.client, team.kid.token, proof)
    done = board_grants.enrol(team.client, team.kid.token, board_grants.approve(proof, team.person))
    assert done["keys"] == 2
    issued(team, team.lead, board_grants.Want("land_trust", {"kind": "session", "name": team.lead.name}), key=other)


def test_the_kit_text_of_a_grant_is_plain_ascii(team):
    for text in ("feat/é", "line\nbreak"):
        with pytest.raises(ValueError, match="ASCII"):
            board_grants.sign_issue(team.person, team.board, board_grants.Want("note_verify", {"kind": "session", "name": "x"}, text))
    with pytest.raises(ValueError, match="time"):
        board_grants.duration_ms("soon")
    with pytest.raises(ValueError, match=r"year|days"):
        board_grants.duration_ms("400d")
    assert board_grants.duration_ms("90m") == 5_400_000


def test_a_refusal_names_the_grant_and_a_person_at_a_terminal_signs_it_for_one_use(team):
    lead = team.lead
    team.allow(lead, "sync_now")  # enrols the key, nothing else
    plain = team.member("plain")
    call = lambda: team.client.call("set_join_policy", "", plain.token, policy="open")  # noqa: E731 - one request, asked twice
    with pytest.raises(Denied) as refused:
        call()
    assert refused.value.grant["action"] == "set_join_policy" and refused.value.grant["name"] == plain.name
    team.client.person = lambda token, hint: board_grants.issue(team.session(plain), team.person, board_grants.Want(
        hint["action"], {"kind": "session", "name": hint["name"]}, hint["resource"], hint["device"], 120_000, 1)) is not None
    assert team.client.call("set_join_policy", "", plain.token, policy="open")["policy"] == "open"
    team.client.person = None
    with pytest.raises(Denied):
        call()
    log = board_grants.log(team.session(plain))
    assert [e["op"] for e in log if e["action"] == "set_join_policy"] == ["use", "issue"]
    assert [e["user"] for e in log if e["op"] == "use"] == [plain.name]


def test_no_key_is_held_by_a_process_an_agent_started_or_with_no_terminal(team, monkeypatch):
    keys = person_key.keys(team.state)
    assert person_key.held(team.state) is None, "pytest has no terminal and no key is kept"
    monkeypatch.setenv("CLAUDECODE", "1")
    assert person_key.held(team.state, attended=True) is None
    assert person_key.provider(team.state, attended=True)("a-token", {"action": "sync_now"}) is False
    assert not keys.meta_path.exists()


def test_the_grant_commands_refuse_an_agent_and_a_process_with_no_terminal(team):
    lead = team.lead
    argv = ["grant", "note_verify", "--to", lead.name, "--until", "1h"]
    marked = team.cli(*argv, "--json", who=lead, env={"CLAUDECODE": "1"})
    assert marked.returncode == 3 and "agent" in json.loads(marked.stdout)["error"], marked.stdout
    plain = team.cli(*argv, "--json", who=lead)
    assert plain.returncode == 3 and "terminal" in json.loads(plain.stdout)["error"], plain.stdout
    assert board_grants.listing(team.session(lead)).grants == []


def test_a_person_sets_up_the_lead_grants_once_and_every_item_is_revoked_by_its_own_id(team):
    lead = team.lead
    code, out, err = setup(team, lead, "--days", "7")
    assert code == 0, (out, err)
    assert out.count("Type yes") == 1, "one question for the whole list"
    assert lead.name in out and "for 7 days" in out and "land_control" in out and "act as a person key" in out
    for action in board_grants.DEFAULT_ACTIONS:
        assert action in out, out
    held = board_grants.listing(team.session(lead))
    assert sorted(g.action for g in held.grants) == sorted(board_grants.DEFAULT_ACTIONS)
    assert all(g.status == "live" and g.to["kind"] == "session" and g.to["name"] == lead.name for g in held.grants)
    assert {g.to.get("subagents", False) for g in held.grants if g.action == "note_verify"} == {True}
    assert {g.to.get("subagents", False) for g in held.grants if g.action != "note_verify"} == {False}
    assert all(g.until_ms - g.at_ms == 7 * 86_400_000 for g in held.grants)
    code, shown, err = team.terminal("grants", "--json")
    assert code == 0 and {g["action"] for g in json.loads(shown.strip().splitlines()[-1])} == set(board_grants.DEFAULT_ACTIONS), (shown, err)
    assert team.cli("grants", "--json").returncode == 3, "no session and no terminal reads nothing"
    mine = team.cli("grants", "--mine", "--json", who=lead)
    assert {g["action"] for g in json.loads(mine.stdout)} == set(board_grants.DEFAULT_ACTIONS), mine.stdout
    # the lead holds them: landing control and task authority work, a subagent's note verification too
    lead_session = team.session(lead)
    assert lead_session.call("land_brake", pause=True)["paused"] is True
    assert lead_session.call("task_mode", mode="strict")["mode"] == "strict"
    note = lead_session.note_add("fact", "a claim", "it is so", verify_cmd="true")
    assert team.session(team.kid).note_verify(str(note["seq"]), 0, "ab" * 32).trust == "test-verified", "a subagent verifies under the grant to its parent"
    victim = next(g for g in held.grants if g.action == "land_control")
    code, out, err = team.terminal("revoke-grant", victim.gid, "--reason", "no longer needed", who=lead, typed=TYPED)
    assert code == 0, (out, err)
    with pytest.raises(Denied):
        lead_session.call("land_brake", pause=False)
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
    for method, params in (("task_mode", {"mode": "open"}), ("shard_consent", {"enabled": False}), ("land_brake", {"pause": True})):
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
        code, out, err = team.terminal(*argv, who=lead, typed=TYPED)
        assert code == 2 and "registration is open" in out + err and "--i-understand-registration-is-open" in out + err, (argv, out, err)
    assert len(board_grants.listing(team.session(lead)).grants) == held
    code, out, err = team.terminal("grant", "task_authority", "--class", "claude", "--i-understand-registration-is-open", "--until", "1h", who=lead, typed=TYPED)
    assert code == 0 and "registration is open" in out + err, (out, err)
    assert team.session(liar).call("task_mode", mode="strict")["mode"] == "strict", "a class grant is held by whatever claims the family: that is why it needs the flag"
    assert "class" in {g.to["kind"] for g in board_grants.listing(team.session(lead)).grants}


def test_a_grant_that_names_a_verified_model_says_in_the_listing_that_it_matches_nobody_yet(team):
    lead = team.lead
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    for argv in (["grant", "sync_now", "--account", "claude-sonnet-5-5", "--until", "1h"],
                 ["grant", "sync_now", "--class", "claude", "--verified", "--i-understand-registration-is-open", "--until", "1h"]):
        code, out, err = team.terminal(*argv, who=lead, typed=TYPED)
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
    code, out, err = team.terminal("grant", "file_delete", "--to", kid.name, "--scope", "abc*", "--until", "2h", "--uses", "3", "--json", who=lead, typed=TYPED)
    assert code == 0, (out, err)
    assert out.count("Type yes") == 1 and "file_delete" in out and kid.name in out and "abc*" in out and "for 2 hours" in out and "3 uses" in out, out
    done = json.loads(out.strip().splitlines()[-1])
    held = {g.gid: g for g in board_grants.listing(team.session(kid)).grants}[done["granted"]]
    assert (held.action, held.to, held.scope["resource"], held.uses, held.used, held.mine) == ("file_delete", {"kind": "session", "name": kid.name}, "abc*", 3, 0, True)
    assert 0 < held.until_ms - held.at_ms <= 2 * 3_600_000
    for argv in (["grant", "land_trust", "--until", "1h"], ["grant", "land_trust", "--to", "a", "--class", "claude", "--until", "1h"],
                 ["grant", "push_main", "--to", kid.name, "--until", "1h"], ["grant", "land_trust", "--to", kid.name, "--until", "400d"]):
        code, out, err = team.terminal(*argv, "--json", who=lead, typed={"Type yes": "no"})
        assert code in (2, 3), (argv, out, err)
    code, out, err = team.terminal("grant", "task_authority", "--to", kid.name, "--uses", "2", "--device", "any", "--until", "1h", "--json", who=lead, typed=TYPED)
    assert code in (2, 3) and "names the one device" in out + err, "a use count on an action another device may use names a device: " + out + err
    assert "land_trust" not in {g.action for g in board_grants.listing(team.session(lead)).grants}


def test_a_key_carried_from_another_device_is_enrolled_by_the_key_the_node_holds(team):
    lead = team.lead
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    other = Signer.generate()
    proof = json.dumps(board_grants.enrolment(other, "the laptop"))
    code, out, err = team.terminal("person-enroll", "--proof", proof, "--json", who=lead, typed=TYPED)
    assert code == 0, (out, err)
    assert "the laptop" in out and json.loads(out.strip().splitlines()[-1])["keys"] == 2
    assert person_key.kept(team.state)
    with pytest.raises(Invalid):
        board_grants.enrol(team.client, lead.token, {"public": "zz"})


def test_a_key_is_taken_off_the_node_by_a_key_the_node_holds_and_the_last_key_stays(team):
    lead = team.lead
    code, out, err = setup(team, lead)
    assert code == 0, (out, err)
    other = Signer.generate()
    code, out, err = team.terminal("person-enroll", "--proof", json.dumps(board_grants.enrolment(other, "a lost laptop")), who=lead, typed=TYPED)
    assert code == 0, (out, err)
    lost = board_grants.fingerprint(other.public)
    issued(team, lead, board_grants.Want("land_trust", {"kind": "session", "name": lead.name}), key=other)
    assert "land_trust" in {g.action for g in board_grants.listing(team.session(lead)).grants if g.status == "live"}
    code, out, err = team.terminal("person-unenroll", "--key", lost, who=lead, typed={"Type yes": "no"})
    assert code == 3 and lost in board_grants.listing(team.session(lead)).persons, "without the person's yes nothing is signed"
    code, out, err = team.terminal("person-unenroll", "--key", lost, "--json", who=lead, typed=TYPED)
    assert code == 0 and lost[:16] in out, (out, err)
    held = board_grants.listing(team.session(lead))
    assert lost not in held.persons and len(held.persons) == 1
    assert {g.status for g in held.grants if g.action == "land_trust"} == {"ignored"}, "what the removed key signed counts no more"
    mine = board_grants.fingerprint(person_key.keys(team.state).public)
    code, out, err = team.terminal("person-unenroll", "--key", mine, who=lead, typed=TYPED)
    assert code == 3 and "last person key" in out + err, (out, err)
    assert board_grants.listing(team.session(lead)).persons == [mine]


def test_a_node_that_lost_its_only_usable_key_is_started_again_by_deleting_persons_json_with_the_node_stopped(team):
    lead = team.lead
    team.allow(lead, "sync_now")
    assert board_grants.listing(team.session(lead)).persons == [board_grants.fingerprint(team.person.public)]
    team.stop()
    (team.state / "persons.json").unlink()
    node_launch.ensure_node(team.state)
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
        ["grant", "release_main", *fine], ["grant", "sudo", *fine], ["grant", "", *fine], ["grant", "land_trust\nnote_verify", *fine],
        ["revoke-grant", "g-nope"], ["revoke-grant", "../../etc/passwd"], ["revoke-grant", "g-" + "0" * 16],
        ["person-enroll", "--proof", "{not json"], ["person-enroll", "--proof", '{"public": 5}'], ["person-enroll", "--proof", "[]"],
        ["person-unenroll", "--key", "nope"], ["person-unenroll", "--key", "ab\ncd"], ["person-unenroll", "--key", "ab" * 32],
        ["grants-setup", "--session", "bad session name"], ["grants-setup", "--session", "claude-000000"], ["grants-setup", "--session", lead.name, "--days", "0"],
        ["grants-setup", "--session", lead.name, "--days", "9999"], ["grants-setup", "--session", kid.name], ["grants-setup", "--session", ""],
    ]
    for argv in hostile:
        code, out, err = team.terminal(*argv, "--json", who=lead, typed={"Type yes": "no"})
        assert code in (2, 3), (argv, code, out, err)
    after = board_grants.listing(team.session(lead))
    assert len(after.grants) == held and after.ignored == [] and len(after.persons) == 1


def test_the_persons_screen_signs_for_one_use_only_after_the_person_types_yes_and_a_marked_process_cannot(team, monkeypatch):
    monkeypatch.setattr(node_launch, "default_state", lambda: team.state)
    questions: list[str] = []
    answers = iter([False, True])
    monkeypatch.setattr(person, "confirm", lambda text: questions.append(text) or next(answers))
    monkeypatch.setattr(person_key, "tty_passphrase", lambda _prompt: PASSPHRASE)
    team.keep_person_key(team.lead)
    screen = person_session.session(Path.cwd())
    assert board_grants.listing(screen).grants == [], "the screen's own session holds no grant"
    with pytest.raises(Denied):
        screen.call("task_mode", mode="strict")
    assert board_grants.log(screen) == [] and len(questions) == 1, "the person did not type yes: nothing was signed"
    assert screen.call("task_mode", mode="strict")["mode"] == "strict"
    assert len(questions) == 2 and all("task_authority" in q for q in questions), questions
    assert screen.name in questions[1] and "for 2 minutes" in questions[1] and "1 use" in questions[1] and "Type yes" in questions[1], questions[1]
    log = board_grants.log(screen)
    assert [e["op"] for e in log if e["action"] == "task_authority"] == ["use", "issue"]
    spent = next(g for g in board_grants.listing(screen).grants if g.action == "task_authority")
    assert (spent.to, spent.uses, spent.used, spent.status) == ({"kind": "session", "name": screen.name}, 1, 1, "exhausted")
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(Denied):
        screen.call("task_mode", mode="open")
    assert board_grants.log(screen) == log and len(questions) == 2, "a process an agent started was not even asked, and signed nothing"
