"""The person's grant key: what it shows before it signs, that only a typed yes on the terminal device lets it sign, and that
neither stdin and stdout nor the environment stands in for that."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pty
import select
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest
from node_kit import PASSPHRASE, TYPED, NoKeystore, person_keys

from poolhouse import person
from poolhouse.board import grants, person_key
from poolhouse.fleet.onboard.signing import ConfirmedKeys, KeyStoreError, SigningKeys

pytest_plugins = ["node_kit"]
SRC = str(Path(__file__).resolve().parents[1] / "src")
ASK = "import sys\nimport time\nfrom poolhouse import person\ntime.sleep(float(sys.argv[2]))\nprint(person.confirm('Type yes: ', wait_s=float(sys.argv[1])))\n"
WANT = grants.Want("link_remove", {"kind": "session", "name": "claude-abc123"}, "feat/*", "ab" * 32, 7 * 86_400_000, 3)
T0 = 1_800_000_000_000
WHERE = grants.Where("ef" * 32, {"pid": 4242, "start": 1_700_000})


class Capture:
    """A key that keeps the bytes it is asked to sign, to put them to the person key the way a caller would."""

    public = b"\x01" * 32

    def sign_bytes(self, data: bytes) -> bytes:
        self.data = data
        return b"\0" * 64


@pytest.fixture
def person_key_at(tmp_path, monkeypatch):
    """A person key in a temporary directory whose terminal question is `person.confirm`, answered from ``answers`` and kept in ``asked``."""
    def make(*answers: bool) -> tuple[ConfirmedKeys, list[str]]:
        asked: list[str] = []
        queue = list(answers)
        monkeypatch.setattr(person, "confirm", lambda text, **_: asked.append(text) or (queue.pop(0) if queue else False))
        return person_keys(tmp_path / "person"), asked
    return make


def run_confirm(typed: str | None, *, controlling: bool = True, wait_s: float = 5.0, sleep: float = 0.0, expect: str = "Type yes: ") -> tuple[str, str]:
    """`person.confirm` in a child whose stdin and stdout are pipes, with a pseudo-terminal as its controlling terminal (or none):
    what it printed and what the terminal showed. ``typed`` is written once the prompt shows, or at once when ``sleep`` is set."""
    leader, follower = pty.openpty()
    kwargs = {"preexec_fn": lambda: fcntl.ioctl(follower, termios.TIOCSCTTY, 0)} if controlling else {}
    child = subprocess.Popen([sys.executable, "-c", ASK, str(wait_s), str(sleep)], env={**os.environ, "PYTHONPATH": SRC}, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, pass_fds=(follower,), **kwargs)
    shown = b""
    sent = typed is None
    if sleep and typed is not None:
        os.write(leader, f"{typed}\n".encode())
        sent = True
    end = time.monotonic() + 30
    while child.poll() is None and time.monotonic() < end:
        if select.select([leader], [], [], 0.1)[0]:
            try:
                data = os.read(leader, 65536)
            except OSError:
                break
            if not data:
                break
            shown += data
            if not sent and expect.encode() in shown:
                os.write(leader, f"{typed}\n".encode())
                sent = True
    out, err = child.communicate(timeout=30)
    os.close(follower)
    os.close(leader)
    assert child.returncode == 0, err
    return out.strip(), shown.decode(errors="replace")


@pytest.mark.parametrize(("typed", "said"), [("yes", "True"), ("YES", "True"), ("", "False"), ("y", "False"), ("no", "False"), ("yes please", "False"), ("ok", "False")])
def test_only_a_typed_yes_on_the_terminal_device_is_a_yes_whatever_stdin_and_stdout_are(typed, said):
    out, shown = run_confirm(typed)
    assert out == said and "Type yes: " in shown


def test_text_typed_before_the_question_is_not_an_answer_and_no_answer_is_no():
    out, shown = run_confirm("yes", sleep=1.5, wait_s=2.0)
    assert out == "False" and "Type yes: " in shown and "no answer" in shown, "a yes typed ahead is dropped"
    out, shown = run_confirm(None, wait_s=1.0)
    assert out == "False" and "no answer" in shown


def test_with_no_terminal_there_is_nobody_to_ask_and_the_answer_is_no_at_once():
    started = time.monotonic()
    out, shown = run_confirm("yes", controlling=False, wait_s=60.0)
    assert out == "False" and shown == "" and time.monotonic() - started < 20


def test_a_person_key_asks_what_it_would_sign_in_words_and_signs_only_after_a_yes(person_key_at):
    key, asked = person_key_at(False, True)
    with pytest.raises(KeyStoreError, match="confirmation"):
        grants.sign_issue(key, "demo", WANT, now_ms=T0)
    assert len(asked) == 1
    for word in ("allow the session claude-abc123 to link_remove on feat/*", "board demo", "device " + "ab" * 32, "for 7 days", "ending " + grants.when(T0 + 7 * 86_400_000), "3 uses", "Type yes to sign it: "):
        assert word in asked[0], (word, asked[0])
    signed = grants.sign_issue(key, "demo", WANT)
    assert len(asked) == 2 and len(signed["sig"]) == 128


def test_the_question_is_read_from_the_bytes_signed_and_only_a_grant_a_revocation_or_a_key_is_signed(person_key_at):
    key, asked = person_key_at(True, True, True, True)
    revoke = grants.sign_revoke(key, "demo", "g-0123456789abcdef", "no longer needed")
    assert "end the grant g-0123456789abcdef on board demo (no longer needed)" in asked[-1] and revoke["gid"] == "g-0123456789abcdef"
    grants.enrolment(key, "the laptop")
    assert f"let the key {key.public.hex()} (the laptop) act as a person key on the node" in asked[-1]
    grants.sign_unenroll(key, "cd" * 32, WHERE, now_ms=T0)
    assert f"take the person key {'cd' * 32} off the node {'ef' * 32}" in asked[-1] and f"until {grants.when(T0 + 300_000)}" in asked[-1]
    assert "once" in asked[-1] and "process 4242" in asked[-1], asked[-1]
    before = len(asked)
    for data in (b"anything at all", b"poolhouse/grant/v1\nnot json", b"poolhouse/grant/v1\n{}", b"poolhouse/other/v1\n{}", b"", json.dumps(["a"]).encode()):
        with pytest.raises(ValueError, match="person key signs"):
            key.sign_bytes(data)
    assert len(asked) == before, "bytes that are none of those are refused without a question"


def test_one_yes_covers_the_list_it_showed_and_nothing_else(person_key_at):
    key, asked = person_key_at(True, False)
    other = grants.Want("sync_now", {"kind": "session", "name": "claude-abc123"})
    key.approve([grants.want_line("demo", WANT, T0)], "One thing:")
    assert len(asked) == 1
    assert grants.sign_issue(key, "demo", WANT, now_ms=T0)["sig"] and len(asked) == 1, "the line that was shown needs no second question"
    with pytest.raises(KeyStoreError):
        grants.sign_issue(key, "demo", WANT, now_ms=T0)
    assert len(asked) == 2, "and it covered one signature"
    with pytest.raises(KeyStoreError):
        key.approve([grants.want_line("demo", other, T0)], "Refused:")


def test_the_person_key_asks_every_time_whatever_its_record_says_and_the_pool_key_is_not_changed(person_key_at, tmp_path):
    keys, asked = person_key_at(False, False)
    assert keys.meta()["confirm"] is True, "a new person key records that it asks"
    record = tmp_path / "person" / "signing.json"
    record.write_text(json.dumps({**json.loads(record.read_text()), "confirm": False}))
    with pytest.raises(KeyStoreError, match="confirmation"):
        grants.sign_issue(keys, "demo", WANT, now_ms=T0)
    assert len(asked) == 1
    pool = SigningKeys(tmp_path / "pool", ks=NoKeystore(), passphrase=lambda _prompt: PASSPHRASE)
    assert pool.meta()["confirm"] is False and len(pool.sign_bytes(b"data")) == 64, "the pool's signing key signs without asking, as before"


def test_a_confirm_passed_by_the_caller_answers_nothing_and_the_question_is_still_put(person_key_at):
    keys, asked = person_key_at(False)
    spy = Capture()
    grants.sign_issue(spy, "demo", WANT, now_ms=T0)
    with pytest.raises(KeyStoreError, match="confirmation"):
        keys.sign_bytes(spy.data, confirm=lambda _summary: True)
    assert len(asked) == 1 and "Type yes to sign it: " in asked[0], "the caller's answer was not used: the person was asked"
    keys, asked = person_key_at(True)
    assert len(keys.sign_bytes(spy.data, confirm=lambda _summary: False)) == 64 and len(asked) == 1, "and the caller's no is not the person's"


def test_the_person_key_signs_no_manifest_whoever_confirms(person_key_at):
    keys, asked = person_key_at(True, True)
    with pytest.raises(KeyStoreError, match="not manifests"):
        keys.sign([], serial=1, confirm=lambda _summary: True)
    with pytest.raises(KeyStoreError, match="not manifests"):
        keys.attest([], serial=1, namespace="x", confirm=lambda _summary: True)
    assert asked == []


def test_an_approval_names_the_whole_node_the_key_and_when_it_ends(person_key_at):
    keys, asked = person_key_at(True, True)
    other = Capture()
    carried = grants.enrolment(other, "a second device", now_ms=T0)
    grants.approve(carried, keys, grants.Where("cd" * 32, WHERE.anchor), now_ms=T0)
    assert f"let the key {other.public.hex()} (a second device) act as a person key on the node {'cd' * 32}" in asked[-1]
    assert f"approved by the key {grants.fingerprint(keys.public)}" in asked[-1] and f"until {grants.when(T0 + 300_000)}" in asked[-1]
    assert "in the terminal whose shell is process 4242" in asked[-1], asked[-1]
    grants.sign_rebind(keys, "demo", "claude-abc123", grants.Where("ab" * 32, WHERE.anchor), now_ms=T0)
    assert "(the shell is process 4242)" in asked[-1] and f"on the node {'ab' * 32}" in asked[-1] and "end the tokens it holds" in asked[-1], asked[-1]


def test_the_question_of_the_person_key_is_the_terminal_question_and_nothing_else_answers_it(tmp_path, monkeypatch):
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(person, "confirm", lambda text, **kw: seen.append((text, kw)) or False)
    keys = person_keys(tmp_path / "person")
    spy = Capture()
    grants.sign_issue(spy, "demo", WANT, now_ms=T0)
    with pytest.raises(KeyStoreError):
        keys.sign_bytes(spy.data)
    with pytest.raises(KeyStoreError):
        keys.approve(["a line"], "Header:")
    assert [text.rsplit("\n", 1)[-1] for text, _ in seen] == ["Type yes to sign it: ", "Type yes to sign it: "] and seen[0][1] == {}


def test_a_carried_key_whose_name_is_not_plain_text_is_refused_before_the_person_is_asked(person_key_at):
    keys, asked = person_key_at(True)
    carried = {"public": "ab" * 32, "at_ms": T0, "proof": "00" * 64}
    for about in ("\x1b[2J\x1b[Hlet the key 0000 act", "line\nbreak", "caf\u00e9"):
        with pytest.raises(ValueError, match="plain ASCII"):
            grants.approve({**carried, "about": about}, keys, grants.Where("ab" * 32, WHERE.anchor))
    assert asked == []


def test_the_person_keys_passphrase_comes_from_the_terminal_and_never_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_SIGNING_PASSPHRASE", "the wrong passphrase from the environment")
    ConfirmedKeys(tmp_path / "person", ks=NoKeystore(), passphrase=lambda _prompt: PASSPHRASE).meta()
    asked: list[str] = []
    monkeypatch.setattr(person_key, "tty_passphrase", lambda prompt: asked.append(prompt) or PASSPHRASE)
    monkeypatch.setattr(person, "confirm", lambda _text, **_kw: True)
    key = person_key.keys(tmp_path)
    assert key.directory == tmp_path / "person"
    assert len(grants.sign_issue(key, "demo", WANT)["sig"]) == 128 and len(asked) == 1 and "passphrase" in asked[0]
    assert len(grants.sign_issue(key, "demo", WANT)["sig"]) == 128 and len(asked) == 1, "the passphrase is typed once for the life of the process"


def test_the_passphrase_prompt_needs_a_terminal_and_the_environment_does_not_stand_in_for_one(monkeypatch):
    monkeypatch.setenv("POOLHOUSE_SIGNING_PASSPHRASE", PASSPHRASE)
    monkeypatch.setattr(person, "controlling_terminal", lambda: contextlib.nullcontext(None))
    with pytest.raises(KeyStoreError, match="terminal"):
        person_key.tty_passphrase("signing key passphrase: ")


def test_a_process_with_a_terminal_and_the_passphrase_in_its_environment_signs_nothing_until_the_person_types_yes(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    env = {"POOLHOUSE_SIGNING_PASSPHRASE": "wrong: the person key never reads this"}
    argv = ["grant", "note_verify", "--to", lead.name, "--until", "1h", "--json"]
    code, out, err = team.terminal(*argv, who=lead, env=env, typed={"Type yes": "no"})
    assert code == 3 and "Type yes" in out and "confirmation" in out + err and grants.listing(team.session(lead)).grants == [], (out, err)
    code, out, err = team.terminal(*argv, who=lead, env=env, typed={"Type yes": ""})
    assert code == 3 and grants.listing(team.session(lead)).grants == [], "Enter is not yes"
    code, out, err = team.terminal(*argv, who=lead, env=env, typed=TYPED)
    assert code == 0 and "signing key passphrase: " in out, (out, err)
    assert [g.action for g in grants.listing(team.session(lead)).grants] == ["note_verify"]
