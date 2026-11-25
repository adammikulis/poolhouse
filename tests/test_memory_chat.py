"""Memory in the chat: the first message carries what is recalled from both scopes, remember writes at
once to the scope the model named (and the memory store's own checks refuse what it must), every role
may recall and only the acting ones may remember, and a poisoned project memory changes nothing. The
model is scripted; the loop, the rails and the stores are real."""

from __future__ import annotations

import io

import pytest

from poolhouse import chat, do, memory
from tests import memory_keys
from tests.test_chat import Model, registry
from tests.test_memory import inject
from tests.test_memory_scopes import repo

ring = memory_keys.ring


@pytest.fixture(autouse=True)
def released():
    """Every memory opened by ``make`` is closed after the test."""
    opened.clear()
    yield
    for each in opened:
        each.close()


opened: list[memory.Memory] = []


def make(tmp_path, script, stdin="", *, role="approve-first", plant=()):
    proj = repo(tmp_path, "alpha")
    mem = memory.Memory.open(explicit=proj)
    opened.append(mem)
    mem.user.add("Prefers short answers with no filler", "preference")
    mem.project.add("Run the staging flag before every release", "note")
    for text in plant:
        inject(mem.project, text)
    out = io.StringIO()
    person = do.Person(io.StringIO(stdin), out)
    seen: list = []
    model = Model(script)
    tools = chat.tools_for_chat(person=person, registry=registry(seen))
    session = chat.Chat(model, person, tools=tools, role=role,
                        extension=chat.extensions(person, mem))
    return session, model, mem, out, seen


def first_user(model) -> str:
    return next(m["content"] for m in model.seen[0] if m["role"] == "user")


def test_the_first_message_carries_both_scopes_and_the_system_message_the_guidance(tmp_path):
    session, model, _, _, _ = make(tmp_path, [])
    session.turn("how do I do the staging release?")
    body = first_user(model)
    assert body.startswith("<untrusted source='memory'>")
    assert "scope user" in body and "scope project" in body and "staging flag" in body
    assert body.endswith("how do I do the staging release?")
    system = model.seen[0][0]["content"]
    assert system.startswith(chat.SYSTEM[:20]) and "Two notebooks" in system and "alpha" in system
    assert "<untrusted source" not in system and "staging flag" not in system
    session.turn("and then?")
    assert "<untrusted" not in model.seen[1][-1]["content"]


def test_a_new_conversation_recalls_again(tmp_path):
    session, model, _, _, _ = make(tmp_path, [])
    session.turn("staging release")
    session.new()
    session.turn("staging release again")
    assert "<untrusted source='memory'>" in model.seen[1][1]["content"]


def test_remember_writes_at_once_to_the_scope_the_model_named_and_shows_what_it_stored(tmp_path):
    call = ("remember", {"fact": "CI needs the staging flag", "scope": "project", "kind": "note"})
    session, _, mem, out, _ = make(tmp_path, [call], stdin="")
    session.turn("note that for later")
    assert "! remembered: Remember for this project (alpha): CI needs the staging flag" in out.getvalue()
    assert "CI needs the staging flag" in [f.text for f in mem.project.facts()]
    assert "CI needs the staging flag" not in [f.text for f in mem.user.facts()]


def test_a_secret_in_a_remembered_fact_is_refused_at_once_by_the_secrets_rail_and_not_stored(tmp_path):
    secret = "the token is hf_" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3z"
    call = ("remember", {"fact": secret, "scope": "user", "kind": "note"})
    session, model, mem, _, _ = make(tmp_path, [call], stdin="y\n")
    session.turn("remember it")
    assert not [f for f in mem.user.facts() + mem.project.facts() if "hf_" in f.text]
    assert "blocked by the secrets rail" in model.told()


def test_no_rule_can_cover_remember_and_a_session_may_add_only_so_many_facts(tmp_path):
    from poolhouse import rules as saved
    from poolhouse.memory.tools import SESSION_WRITES

    with pytest.raises(ValueError, match="classifier labelled it unsure"):
        saved.Rules().add("remember", {"fact": "x", "scope": "user"}, "always", "")
    _session, _model, mem, _out, _ = make(tmp_path, [])
    person = do.Person(io.StringIO(""), io.StringIO())
    remember = {s["function"]["name"]: fn for s, fn in
                memory.tools(confirm=person.choose, store=mem.user, project=mem.project)}["remember"]
    told = [remember(fact=f"the build number was {n} on day {n}", scope="user", kind="note")
            for n in range(SESSION_WRITES + 1)]
    assert [t["stored"] for t in told] == [True] * SESSION_WRITES + [False]
    assert "facts were added this session; stop" in told[-1]["error"]
    assert len([f for f in mem.user.facts() if "build number" in f.text]) == SESSION_WRITES


@pytest.mark.parametrize("role", ["read-only", "approve-first", "plan-and-go"])
def test_every_role_may_recall(tmp_path, role):
    session, model, _, _, _ = make(tmp_path, [("recall", {"query": "staging"})], role=role)
    session.turn("what do I know about staging?")
    assert "recall" in model.offered[0]
    told = model.told()
    assert "scope project" in told and "staging flag" in told


def test_the_reader_role_is_not_offered_remember_and_the_acting_roles_are(tmp_path):
    reader, model, mem, _, _ = make(tmp_path, [("remember", {"fact": "x fact", "scope": "user"})], stdin="1\n",
                                    role="read-only")
    reader.turn("remember something")
    assert "remember" not in model.offered[0] and mem.user.facts()[0].text.startswith("Prefers")
    assert len(mem.user.facts()) == 1
    for role in ("approve-first", "plan-and-go"):
        acting, model, _, _, _ = make(tmp_path / role, [], role=role)
        acting.turn("hi")
        assert {"recall", "remember"} <= model.offered[0]


def test_the_memory_command_shows_each_fact_with_its_scope(tmp_path):
    session, _, _, out, _ = make(tmp_path, [])
    chat._slash(session, "/memory", "staging release", out, None)
    shown = out.getvalue()
    assert "project  p.m0001" in shown and "staging flag" in shown
    assert "<untrusted" not in shown
    chat._slash(session, "/memory", "", out, None)
    assert "user     u.m0001" in out.getvalue()


def test_dry_run_prints_the_guidance_for_the_project_given(tmp_path):
    args = chat.COMMAND.parser().parse_args(["--dry-run", "--project", str(repo(tmp_path, "alpha"))])
    out = io.StringIO()
    assert chat.serve(args, io.StringIO(), out) == 0
    assert "Two notebooks" in out.getvalue() and "alpha" in out.getvalue()
    assert "remember" in out.getvalue() and "recall" in out.getvalue()


def test_a_project_that_is_not_a_directory_is_refused(tmp_path):
    args = chat.COMMAND.parser().parse_args(["--dry-run", "--project", str(tmp_path / "nope")])
    out = io.StringIO()
    assert chat.serve(args, io.StringIO(), out) == 2 and "not a directory" in out.getvalue()


class Counting(memory_keys.MemoryRing):
    """A keystore that counts every call made to it."""

    calls = 0

    def get_password(self, service, username):
        Counting.calls += 1
        return super().get_password(service, username)

    def set_password(self, service, username, password):
        Counting.calls += 1
        super().set_password(service, username, password)


def test_fifty_sessions_and_an_empty_recall_touch_the_keystore_zero_times(tmp_path):
    import keyring

    before = keyring.get_keyring()
    keyring.set_keyring(Counting())
    Counting.calls = 0
    try:
        for n in range(50):
            person = do.Person(io.StringIO(""), io.StringIO())
            mem = memory.Memory.open(explicit=repo(tmp_path, f"p{n}"))
            opened.append(mem)
            ext = chat.extensions(person, mem)
            session = chat.Chat(Model([("recall", {"query": "anything"})]), person, extension=ext)
            session.turn("hello")
            ext.commands["memory"]("", session.messages)
        assert Counting.calls == 0
        mem.user.add("a first fact", "note")
        assert Counting.calls > 0
    finally:
        keyring.set_keyring(before)
