"""This device's grant key: made on first use, kept in a file under the passphrase of the environment (never asked for at a terminal), and used to
sign the grant a refusal names, at once, when policy allows it. A process with no agent marker may have any grant; a process an agent started
is refused the few it may not have (`AGENT_REFUSED`), with the reason and before anything is signed or any key is made, and every decision is on
the audit log with who asked.
"""

from __future__ import annotations

import json

import pytest
from node_kit import PASSPHRASE, NoKeystore, enrol_as_phone_key, enrol_first

from poolhouse import caller, keystore
from poolhouse.board import grants, person, person_key
from poolhouse.board.client import Denied
from poolhouse.fleet.onboard.manifest import Signer
from poolhouse.fleet.onboard.signing import UNLOCK_ENV, KeyStoreError

pytest_plugins = ["node_kit"]
WANT = grants.Want("link_remove", {"kind": "session", "name": "claude-abc123"}, "feat/*", "ab" * 32, 7 * 86_400_000, 3)
T0 = 1_800_000_000_000
WHERE = grants.Where("ef" * 32, {"pid": 4242, "start": 1_700_000})
HINT = {"action": "member_remove", "board": "demo", "name": "claude-abc123", "resource": "*", "device": "ab" * 32}


class Capture:
    """A key that keeps the bytes it is asked to sign, to read them back the way a caller would."""

    public = b"\x01" * 32

    def __init__(self) -> None:
        self.data: list[bytes] = []

    def sign_bytes(self, data: bytes) -> bytes:
        self.data.append(data)
        return b"\0" * 64


@pytest.fixture
def no_question(monkeypatch):
    """Nothing may ask: a prompt for text or a passphrase on this process's terminal ends the test."""
    def asked(*_args, **_kwargs):
        raise AssertionError("the key asked for something at a terminal")
    monkeypatch.setattr("builtins.input", asked)
    monkeypatch.setattr("getpass.getpass", asked)


def test_the_key_is_made_on_first_use_beside_the_node_and_signs_with_no_question(tmp_path, monkeypatch, no_question):
    monkeypatch.setenv(UNLOCK_ENV, PASSPHRASE)
    assert not person_key.kept(tmp_path), "no key is made by looking for one"
    key = person_key.keys(tmp_path)
    signed = grants.sign_issue(key, "demo", WANT, now_ms=T0, gid="g-0123456789abcdef")
    assert len(signed["sig"]) == 128 and person_key.kept(tmp_path) and key.directory == tmp_path / person_key.KEY_DIR
    again = grants.sign_issue(person_key.keys(tmp_path), "demo", WANT, now_ms=T0, gid="g-0123456789abcdef")
    assert again["sig"] == signed["sig"], "the same key signs again, from another object, with no question"


def test_the_passphrase_of_the_key_file_comes_from_the_environment_only_and_its_absence_is_one_sentence(tmp_path, monkeypatch, no_question):
    monkeypatch.setattr(keystore, "default", NoKeystore)  # a machine with no keystore of its own: the key is a file
    monkeypatch.delenv(UNLOCK_ENV, raising=False)
    key = person_key.keys(tmp_path)
    with pytest.raises(KeyStoreError, match=UNLOCK_ENV):
        _ = key.public
    assert not person_key.kept(tmp_path), "nothing was made that nobody could open"
    monkeypatch.setenv(UNLOCK_ENV, "")
    with pytest.raises(KeyStoreError, match=UNLOCK_ENV):
        _ = person_key.keys(tmp_path).public
    monkeypatch.setenv(UNLOCK_ENV, PASSPHRASE)
    made = person_key.keys(tmp_path).public
    monkeypatch.setenv(UNLOCK_ENV, "another passphrase")
    with pytest.raises(KeyStoreError):
        person_key.keys(tmp_path).sign_bytes(b"data")
    monkeypatch.setenv(UNLOCK_ENV, PASSPHRASE)
    assert person_key.keys(tmp_path).public == made


def test_what_is_signed_is_read_back_in_words_from_the_bytes_and_nothing_else_is_described():
    spy = Capture()
    grants.sign_issue(spy, "demo", WANT, now_ms=T0)
    line = grants.describe(spy.data[-1])
    for word in ("allow the session claude-abc123 to link_remove on feat/*", "board demo", "device " + "ab" * 32, "for 7 days", "ending " + grants.when(T0 + 7 * 86_400_000), "3 uses"):
        assert word in line, (word, line)
    grants.sign_revoke(spy, "demo", "g-0123456789abcdef", "no longer needed", now_ms=T0)
    assert "end the grant g-0123456789abcdef on board demo (no longer needed)" in grants.describe(spy.data[-1])
    grants.enrolment(spy, "the laptop", now_ms=T0)
    assert f"let the key {spy.public.hex()} (the laptop) act as a person key on the node" in grants.describe(spy.data[-1])
    grants.sign_unenroll(spy, "cd" * 32, WHERE, now_ms=T0)
    gone = grants.describe(spy.data[-1])
    assert f"take the person key {'cd' * 32} off the node {'ef' * 32}" in gone and "once" in gone and "process 4242" in gone, gone
    for data in (b"anything at all", b"poolhouse/grant/v1\nnot json", b"poolhouse/grant/v1\n{}", b"poolhouse/other/v1\n{}", b"", json.dumps(["a"]).encode()):
        with pytest.raises(ValueError, match="person key signs"):
            grants.describe(data)


def test_an_approval_names_the_whole_node_the_key_and_when_it_ends():
    other, spy = Capture(), Capture()
    carried = grants.enrolment(other, "a second device", now_ms=T0)
    grants.approve(carried, spy, grants.Where("cd" * 32, WHERE.anchor), now_ms=T0)
    line = grants.describe(spy.data[-1])
    assert f"let the key {other.public.hex()} (a second device) act as a person key on the node {'cd' * 32}" in line
    assert f"approved by the key {grants.fingerprint(spy.public)}" in line and f"until {grants.when(T0 + 300_000)}" in line
    assert "in the terminal whose shell is process 4242" in line, line


def test_a_carried_key_whose_name_is_not_plain_text_is_refused_before_anything_is_signed():
    spy = Capture()
    carried = {"public": "ab" * 32, "at_ms": T0, "proof": "00" * 64}
    for about in ("\x1b[2J\x1b[Hlet the key 0000 act", "line\nbreak", "café"):
        with pytest.raises(ValueError, match="plain ASCII"):
            grants.approve({**carried, "about": about}, spy, grants.Where("ab" * 32, WHERE.anchor))
    assert spy.data == []


def test_policy_refuses_an_agent_exactly_the_grants_that_end_a_place_in_the_pool_change_who_governs_it_or_release_what_failed_a_scan():
    assert set(person_key.AGENT_REFUSED) == {"member_remove", "admin_manage", "device_trust", "quarantine_release"}
    assert all(person_key.agent_refusal(action) for action in person_key.AGENT_REFUSED)
    for action in ("sync_now", "pool_leave", "note_verify", "task_authority", "add_device", "file_delete"):
        assert person_key.agent_refusal(action) == "", action


@pytest.mark.parametrize("action", sorted(person_key.AGENT_REFUSED))
def test_an_agent_is_refused_at_once_before_a_key_is_made_and_the_refusal_is_audited_with_who_asked(tmp_path, monkeypatch, no_question, action):
    monkeypatch.setenv(UNLOCK_ENV, PASSPHRASE)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDECODE", "1")
    assert person_key.provider(tmp_path / "node")("a-token", {**HINT, "action": action}) == "", "no token to send the request again with"
    assert not person_key.kept(tmp_path / "node"), "refused before any key was made or opened"
    rows = [r for r in caller.audit_rows() if r["event"] == "grant.refused"]
    assert [(r["action"], "agent CLAUDECODE" in r["who"]) for r in rows] == [(action, True)] and rows[0]["why"] == person_key.AGENT_REFUSED[action]


def test_a_process_with_no_agent_marker_has_the_same_grants_signed_at_once_and_a_node_takes_them(workspace_node):
    team = workspace_node
    lead, plain = team.member("lead"), team.member("plain")
    team.keep_person_key(lead)
    with pytest.raises(Denied) as refused:
        team.client.call("pool_leave", "", plain.token)
    for action in sorted(person_key.AGENT_REFUSED):
        token = person_key.provider(team.state)(plain.token, {**refused.value.grant, "action": action})
        assert token == plain.token, f"{action} is signed at once for a process with no agent marker"
        assert any(g.action == action and g.status == "live" and g.uses == 1 and g.until_ms - g.at_ms == person_key.LASTS_MS
                   for g in grants.listing(team.session(plain)).grants), "good for one use and two minutes, for the session that was refused"
    issued = [r for r in caller.audit_rows() if r["event"] == "grant.issued"]
    assert [r["action"] for r in issued] == sorted(person_key.AGENT_REFUSED) and all(r["who"].startswith("user ") for r in issued)


def test_the_grant_an_agent_may_have_is_signed_for_it_at_once_and_a_node_spends_it_once(workspace_node, monkeypatch):
    team = workspace_node
    lead, plain = team.member("lead"), team.member("plain")
    team.keep_person_key(lead)
    monkeypatch.setenv("CLAUDECODE", "1")
    team.client.person = person_key.provider(team.state)
    with pytest.raises(Denied, match="not in a pool"):
        team.client.call("pool_leave", "", plain.token)  # allowed by policy, signed, spent: the node then answers the call itself
    assert [e["op"] for e in grants.log(team.session(plain)) if e["action"] == "pool_leave"] == ["use", "issue"]
    team.client.person = None
    with pytest.raises(Denied) as again:
        team.client.call("pool_leave", "", plain.token)
    assert again.value.grant["action"] == "pool_leave", "the one use is spent: a second call is refused again, naming the grant"
    assert [r["who"] for r in caller.audit_rows() if r["event"] == "grant.issued" and r["action"] == "pool_leave"] == [caller.who()]
    assert "agent CLAUDECODE" in caller.who()


def test_the_key_a_node_does_not_hold_yet_is_made_and_enrolled_by_the_first_grant_a_command_needs(workspace_node):
    team = workspace_node
    plain = team.member("plain")
    assert grants.listing(team.session(plain)).persons == [] and not person_key.kept(team.state)
    team.client.person = person_key.provider(team.state, acting=person.acting(team.state))
    with pytest.raises(Denied, match="not in a pool"):
        team.client.call("pool_leave", "", plain.token)  # refused for want of a grant, the key made and enrolled, the grant signed and spent: the node then answers
    held = grants.listing(team.session(plain))
    assert person_key.kept(team.state) and held.persons == [grants.fingerprint(person_key.keys(team.state).public)], "the device's own key is the node's first key"
    assert [e["op"] for e in grants.log(team.session(plain)) if e["action"] == "pool_leave"] == ["use", "issue"]


def test_a_phone_key_enrolled_first_leaves_the_devices_own_key_to_be_enrolled_by_the_first_grant_a_command_needs(workspace_node):
    team = workspace_node
    plain = team.member("plain")
    phone = Signer.generate()
    enrol_as_phone_key(team, phone)
    assert grants.listing(team.session(plain)).persons == [grants.fingerprint(phone.public)] and not person_key.kept(team.state)
    team.client.person = person_key.provider(team.state, acting=person.acting(team.state))
    with pytest.raises(Denied, match="not in a pool"):
        team.client.call("pool_leave", "", plain.token)  # no phone tap: the key was made and enrolled beside the phone's, the grant signed and spent
    mine = grants.fingerprint(person_key.keys(team.state).public)
    assert sorted(grants.listing(team.session(plain)).persons) == sorted([grants.fingerprint(phone.public), mine])
    assert [e["op"] for e in grants.log(team.session(plain)) if e["action"] == "pool_leave"] == ["use", "issue"]


def test_a_node_that_holds_another_key_is_given_no_second_first_key_by_a_command_and_the_refusal_says_so(workspace_node):
    team = workspace_node
    plain = team.member("plain")
    enrol_first(team.client, plain.token, Signer.generate())
    with pytest.raises(Denied, match="another key"):
        person_key.enrolled(team.client, team.session(plain))
    team.client.person = person_key.provider(team.state, acting=person.acting(team.state))
    with pytest.raises(Denied) as refused:
        team.client.call("pool_leave", "", plain.token)
    assert refused.value.grant["action"] == "pool_leave", "nothing was signed: the node's refusal stands and names the grant"
    assert grants.log(team.session(plain)) == []


def test_a_command_signs_with_no_terminal_for_a_process_with_no_marker_and_is_refused_to_an_agent_with_the_reason(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    argv = ["grant", "note_verify", "--to", lead.name, "--until", "1h", "--json"]
    marked = team.cli(*argv, who=lead, env={"CLAUDECODE": "1"})
    assert marked.returncode == 3 and "refused to an agent" in json.loads(marked.stdout)["error"], marked.stdout
    assert grants.listing(team.session(lead)).grants == [], "nothing was signed for an agent"
    plain = team.cli(*argv, who=lead)
    assert plain.returncode == 0 and json.loads(plain.stdout)["action"] == "note_verify", (plain.stdout, plain.stderr)
    assert [g.action for g in grants.listing(team.session(lead)).grants] == ["note_verify"]
    audit = [r for r in caller.audit_rows() if r.get("action") == "granting"]
    assert [r["event"] for r in audit] == ["refused", "ran"]
    assert "agent CLAUDECODE" in audit[0]["who"] and audit[1]["who"].startswith("user "), "each run is on the log with who ran it"


def test_a_node_with_no_usable_keystore_and_no_passphrase_in_the_environment_signs_nothing_and_says_what_to_set(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    done = team.cli("grant", "note_verify", "--to", lead.name, "--until", "1h", "--json", who=lead, env={UNLOCK_ENV: ""})
    assert done.returncode == 3 and UNLOCK_ENV in json.loads(done.stdout)["error"], done.stdout
    assert grants.listing(team.session(lead)).grants == [] and not person_key.kept(team.state)
