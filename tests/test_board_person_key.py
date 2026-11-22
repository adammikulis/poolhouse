"""The person's grant key: it signs at once with no question and no terminal, takes its passphrase from the environment only, and the words
that `grants.describe` reads out of the bytes it signs stay exact."""

from __future__ import annotations

import getpass
import json
import sys

import pytest
from node_kit import PASSPHRASE, NoKeystore, person_keys

from poolhouse import caller
from poolhouse.board import grants, person_key
from poolhouse.fleet.onboard.signing import UNLOCK_ENV, KeyStoreError, SigningKeys

pytest_plugins = ["node_kit"]
WANT = grants.Want("link_remove", {"kind": "session", "name": "claude-abc123"}, "feat/*", "ab" * 32, 7 * 86_400_000, 3)
T0 = 1_800_000_000_000
WHERE = grants.Where("ef" * 32, {"pid": 4242, "start": 1_700_000})


class Capture:
    """A key that keeps the bytes it is asked to sign, to read them back the way the person would be shown them."""

    public = b"\x01" * 32

    def sign_bytes(self, data: bytes) -> bytes:
        self.data = data
        return b"\0" * 64


@pytest.fixture
def key(tmp_path) -> SigningKeys:
    """A person key in a temporary directory, in a file under the kit's passphrase (never the keystore)."""
    return person_keys(tmp_path / "person")


def test_a_person_key_signs_at_once_with_no_terminal_and_no_question(key):
    assert not caller.is_terminal(sys.stdin) and not caller.is_terminal(sys.stdout)
    signed = grants.sign_issue(key, "demo", WANT, now_ms=T0)
    assert len(signed["sig"]) == 128
    assert len(grants.sign_issue(key, "demo", WANT)["sig"]) == 128
    assert "confirm" not in key.meta(), "a person key has no confirm-before-signing record"


def test_a_grant_reads_back_in_words_from_the_bytes_signed():
    spy = Capture()
    grants.sign_issue(spy, "demo", WANT, now_ms=T0)
    words = grants.describe(spy.data)
    for word in ("allow the session claude-abc123 to link_remove on feat/*", "board demo", "device " + "ab" * 32, "for 7 days",
                 "ending " + grants.when(T0 + 7 * 86_400_000), "3 uses"):
        assert word in words, (word, words)
    assert grants.want_line("demo", WANT, T0) == words


def test_the_words_are_read_from_the_bytes_signed_and_only_a_grant_a_revocation_or_a_key_has_any():
    spy = Capture()
    grants.sign_revoke(spy, "demo", "g-0123456789abcdef", "no longer needed")
    assert "end the grant g-0123456789abcdef on board demo (no longer needed)" in grants.describe(spy.data)
    grants.enrolment(spy, "the laptop")
    assert f"let the key {spy.public.hex()} (the laptop) act as a person key on the node" in grants.describe(spy.data)
    grants.sign_unenroll(spy, "cd" * 32, WHERE, now_ms=T0)
    said = grants.describe(spy.data)
    assert f"take the person key {'cd' * 32} off the node {'ef' * 32}" in said and f"until {grants.when(T0 + 300_000)}" in said
    assert "once" in said and "process 4242" in said, said
    for data in (b"anything at all", b"poolhouse/grant/v1\nnot json", b"poolhouse/grant/v1\n{}", b"poolhouse/other/v1\n{}", b"", json.dumps(["a"]).encode()):
        with pytest.raises(ValueError, match="person key signs"):
            grants.describe(data)


def test_an_approval_names_the_whole_node_the_key_and_when_it_ends(key):
    spy = Capture()
    carried = grants.enrolment(Capture(), "a second device", now_ms=T0)
    grants.approve(carried, spy, grants.Where("cd" * 32, WHERE.anchor), now_ms=T0)
    said = grants.describe(spy.data)
    assert f"let the key {Capture.public.hex()} (a second device) act as a person key on the node {'cd' * 32}" in said
    assert f"approved by the key {grants.fingerprint(spy.public)}" in said and f"until {grants.when(T0 + 300_000)}" in said
    assert "in the terminal whose shell is process 4242" in said, said
    grants.sign_rebind(spy, "demo", grants.MoveSession("claude-abc123", "01" * 16), grants.Where("ab" * 32, WHERE.anchor), now_ms=T0)
    said = grants.describe(spy.data)
    assert "(the shell is process 4242)" in said and f"on the node {'ab' * 32}" in said and "end the tokens it holds" in said, said


def test_a_carried_key_whose_name_is_not_plain_text_is_refused_before_anything_is_signed():
    spy = Capture()
    carried = {"public": "ab" * 32, "at_ms": T0, "proof": "00" * 64}
    for about in ("\x1b[2J\x1b[Hlet the key 0000 act", "line\nbreak", "café"):
        with pytest.raises(ValueError, match="plain ASCII"):
            grants.approve({**carried, "about": about}, spy, grants.Where("ab" * 32, WHERE.anchor))
    assert not hasattr(spy, "data")


def test_the_person_keys_passphrase_comes_from_the_environment_and_nothing_prompts_for_it(tmp_path, monkeypatch):
    monkeypatch.setenv(UNLOCK_ENV, PASSPHRASE)
    monkeypatch.setattr(getpass, "getpass", lambda *_a: pytest.fail("the person key asked for a passphrase at a terminal"))
    first = person_key.keys(tmp_path)
    assert first.directory == tmp_path / "person" and not person_key.kept(tmp_path)
    assert len(grants.sign_issue(first, "demo", WANT)["sig"]) == 128 and person_key.kept(tmp_path)
    again = person_key.keys(tmp_path)
    assert again.public == first.public and len(grants.sign_issue(again, "demo", WANT)["sig"]) == 128


def test_without_the_passphrase_in_the_environment_the_key_file_is_not_opened_and_nothing_is_asked(tmp_path, monkeypatch):
    monkeypatch.delenv(UNLOCK_ENV, raising=False)
    monkeypatch.setattr(getpass, "getpass", lambda *_a: pytest.fail("the person key asked for a passphrase at a terminal"))
    monkeypatch.setattr(sys, "stdin", type("Tty", (), {"isatty": lambda self: True})())
    with pytest.raises(KeyStoreError, match=UNLOCK_ENV):
        person_key.no_prompt("signing key passphrase: ")
    keys = SigningKeys(tmp_path / "person", ks=NoKeystore(), passphrase=person_key.no_prompt)
    with pytest.raises(KeyStoreError, match=UNLOCK_ENV):
        grants.sign_issue(keys, "demo", WANT)
    assert [f for f in (tmp_path / "person").rglob("*") if f.is_file()] == [], "no key was made that nobody could open again"


def test_a_process_with_a_terminal_and_the_passphrase_in_its_environment_signs_at_once_and_is_never_asked(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    env = {UNLOCK_ENV: PASSPHRASE}
    argv = ["grant", "note_verify", "--to", lead.name, "--until", "1h", "--json"]
    code, out, err = team.terminal(*argv, who=lead, env=env)
    assert code == 0 and "Type yes" not in out + err and "passphrase: " not in out, (out, err)
    assert [g.action for g in grants.listing(team.session(lead)).grants] == ["note_verify"]


def test_without_the_passphrase_the_command_is_refused_at_once_with_the_reason_and_asks_nothing(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    code, out, err = team.terminal("grant", "note_verify", "--to", lead.name, "--until", "1h", "--json", who=lead, timeout=60)
    assert code != 0 and UNLOCK_ENV in out + err and "passphrase: " not in out
    assert grants.listing(team.session(lead)).grants == []
