"""A long coding session keeps the model server's prompt cache: a request that rewrites history the server had read is seen and said, a delegate's
status says how far the server is in reading, the server's prompt cache holds more than one conversation, and nothing a stop does loses what the
run had spent.

PLUMBING ONLY: no model is served and no harness runs. The bridge, the HTTP between it and the server, the record and the state folder are real; the
server is the suite's canned one, which says it is not a model, and the requests are written here.
"""
from __future__ import annotations

import contextlib
import json
import time
from types import SimpleNamespace

import pytest

from poolhouse import (
    delegate_confined,
    delegate_notices,
    delegate_record,
    delegate_recorder,
    delegate_resume,
    delegate_review,
    delegate_store as store,
    harnessing,
    http,
)
from poolhouse.isolation import Lease, ModelBridge, prompt_watch
from poolhouse.testing import canned

CHAT = "/v1/chat/completions"
LONG = "PLUMBING " * 8_000
"""A text of 72,000 characters, far past what is worth saying when it has to be read again."""
SUMMARIZE = "You MUST summarize the conversation above into a structured summary that will be given to another agent to resume the work."
WORK = delegate_review.Pass("work", 0, "task", "brief", ("shell",), "guarded", None)


def tool(name):
    return {"type": "function", "function": {"name": name, "description": "PLUMBING", "parameters": {"type": "object"}}}


def asked(*messages, tools=("glob", "read")):
    return {"model": canned.CANNED_MODEL_ID, "tools": [tool(each) for each in tools],
            "messages": [{"role": "system", "content": "PLUMBING system"}, *messages]}


def said(role, content):
    return {"role": role, "content": content}


FIRST = (said("user", "PLUMBING task"),)
GROWN = (*FIRST, said("assistant", LONG), said("tool", LONG))


def seen(watch, body, route=CHAT):
    return watch.sent(route, json.dumps(body).encode())


def test_a_conversation_that_only_grows_is_never_reported_and_a_summary_of_all_of_it_is_named_a_compaction():
    watch = prompt_watch.Watch()
    for body in (asked(*FIRST), asked(*GROWN), asked(*GROWN, said("assistant", "more"), said("tool", LONG))):
        got = seen(watch, body)
        assert not got.rewrite and not got.compaction, got
    got = seen(watch, asked(*GROWN, said("assistant", "more"), said("tool", LONG), said("user", SUMMARIZE)))
    assert got.compaction and not got.rewrite, got


def test_a_request_with_another_tool_list_is_said_with_where_it_differs_and_how_much_is_read_again():
    watch = prompt_watch.Watch()
    seen(watch, asked(*GROWN))
    got = seen(watch, asked(*GROWN, said("user", SUMMARIZE), tools=("glob", "opencode_worktree_create", "read")))
    assert got.compaction and "tool 2 (opencode_worktree_create where read was)" in got.rewrite, got
    assert "PLUMBING system" not in got.rewrite and "PLUMBING PLUMBING" not in got.rewrite, "it names places, never text"
    assert not seen(watch, asked(*GROWN, said("assistant", "next"))).rewrite, "the conversation it came from goes on unreported"


def test_a_history_cut_or_edited_is_said_and_a_short_request_of_its_own_is_not():
    watch = prompt_watch.Watch()
    seen(watch, asked(*GROWN))
    assert not seen(watch, asked(said("user", "PLUMBING: name this conversation"), tools=())).rewrite, "a title costs next to nothing to read"
    pruned = (*FIRST, said("assistant", LONG), said("tool", "[cleared]"), said("assistant", LONG))
    got = seen(watch, asked(*pruned))
    assert "message 4 (tool)" in got.rewrite, got


def test_what_a_hosted_style_harness_moves_between_requests_without_changing_the_prompt_is_not_a_rewrite():
    watch = prompt_watch.Watch()

    def messages(cch, *said_):
        return {"system": [{"type": "text", "text": f"x-anthropic-billing-header: cc_version=2.1; cch={cch};PLUMBING"}], "messages": list(said_)}

    marked = {"type": "text", "text": LONG, "cache_control": {"type": "ephemeral"}}
    seen(watch, messages("a5145", {"role": "user", "content": [marked]}), "/v1/messages")
    got = seen(watch, messages("0b9f2", {"role": "user", "content": LONG}, {"role": "assistant", "content": "next"}), "/v1/messages")
    assert not got.rewrite, got


@pytest.mark.parametrize("slots,found", [
    ([{"is_processing": True, "n_prompt_tokens": 40_960, "n_prompt_tokens_cache": 8, "next_token": [{"n_decoded": 0}]}], (40_960, 8, 0)),
    ([{"is_processing": True, "n_prompt_tokens": 900, "n_prompt_tokens_cache": 0, "next_token": {"n_decoded": 12}}], (900, 0, 12)),
    ([{"is_processing": False, "n_prompt_tokens": 900}], None),
    ([{"is_processing": True}], None),
    ({"error": "PLUMBING"}, None),
])
def test_progress_is_read_from_the_counters_of_the_one_slot_at_work(slots, found):
    got = prompt_watch.progress(slots)
    assert (None if got is None else (got.read, got.cached, got.written)) == found


def until(holds, seconds=10.0):
    over = time.monotonic() + seconds
    while not (found := holds()) and time.monotonic() < over:
        time.sleep(0.02)
    return found


def test_the_bridge_says_what_it_saw_and_how_far_the_server_is_while_a_request_is_with_it():
    with canned.canned_server() as server:
        server.api_key = "PLUMBING-key"
        server.slots = [{"id": 0, "is_processing": True, "n_prompt_tokens": 4096, "n_prompt_tokens_cache": 0, "next_token": [{"n_decoded": 0}],
                         "prompt": "PLUMBING prompt text the bridge must not keep"}]
        looks = []

        def slow(_body):
            looks.append(until(lambda: (look := bridge.look()).progress and look))
            return "PLUMBING"

        bridge = ModelBridge(server.base_url, Lease.of(canned.CANNED_MODEL_ID, 262_144, 1), key=server.api_key)
        try:
            url = f"http://127.0.0.1:{bridge.start()}{CHAT}"
            options = http.Options(headers={"Authorization": f"Bearer {bridge.token}"}, timeout=30)
            http.request_json(url, payload=asked(*GROWN), options=options)
            server.canned = canned.Canned(answer=slow)
            http.request_json(url, payload=asked(*GROWN, said("user", SUMMARIZE), tools=("opencode_x",)), options=options)
            [during] = looks
            after = bridge.look()
        finally:
            bridge.stop()
        assert during.compacting and during.progress.read == 4096 and not during.waiting, during
        assert "opencode_x" in during.rewrites[0] and after.rewrites == during.rewrites and not after.compacting and after.progress is None, after
        assert "PLUMBING prompt text" not in repr(during)


def a_run(name="reuse-1"):
    store.save(store.Delegate(name, "lead-1", "board", "task", "/tmp/work", "Plumbing-Q4.gguf", edit=True, permissions="guarded"))
    store.set_status(name, "working", "alias")
    return name


def look(read=0, cached=0, written=0, **known):
    """What a bridge knows: the server's counts, and ``waiting``, ``compacting`` or ``rewrites`` where a test names them."""
    progress = None if known.get("waiting") else prompt_watch.Progress(read, cached, written)
    return prompt_watch.Look(progress=progress, **known)


def finished(going, input_tokens, cache_read, output):
    stream = delegate_confined.Stream(record=going)
    stream.line(b'{"type": "step_start", "part": {}}')
    used = {"input": input_tokens, "output": output, "cache": {"read": cache_read}}
    stream.line(json.dumps({"type": "step_finish", "part": {"reason": "tool-calls", "tokens": used}}).encode())


def doing(name):
    return delegate_record.doing("working", store.status(name), time.time())


def test_the_status_says_how_much_of_the_prompt_is_read_when_it_is_generating_and_when_it_compacts():
    name = a_run()
    going = delegate_recorder.of(name).of_pass(WORK)
    finished(going, 600, 164_000, 2_000)
    going.beat(True, look(40_960, 8))
    assert "reading its prompt: 40,960 of about 166,600 tokens (24%)" in doing(name), doing(name)
    going.beat(True, look(166_600, 164_000, 1_234))
    assert "generating: 1,234 tokens written" in doing(name), doing(name)
    going.beat(True, look(waiting=True))
    assert "waiting for the model server's slot" in doing(name), doing(name)
    going.beat(True, look(8_192, compacting=True))
    assert "compacting the conversation, reading its prompt: 8,192" in doing(name), doing(name)
    [event] = [each for each in delegate_record.read(name)[0] if each["kind"] == "compaction"]
    assert event["turn"] == 1
    going.beat(False)
    assert store.status(name)["now"]["doing"] == "idle" and "reading" not in doing(name)


def test_a_rewritten_history_and_a_history_read_again_are_recorded_and_told_to_the_lead():
    name = a_run("reuse-2")
    told = []
    teller = delegate_notices.Teller(name, lambda about, text: told.append((about, text)))
    going = delegate_recorder.of(name).of_pass(WORK)
    finished(going, 30_000, 0, 100)   # the first step reads all of its prompt, as every first one does
    finished(going, 200, 30_000, 100)
    going.beat(True, look(10, rewrites=["PLUMBING: differs at tool 2"]))
    going.beat(True, look(20, rewrites=["PLUMBING: differs at tool 2"]))
    finished(going, 160_000, 4_000, 100)
    teller.look()
    kinds = [each["kind"] for each in delegate_record.read(name)[0]]
    assert kinds.count("rewrite") == 1 and kinds.count("reread") == 1, kinds
    assert [about for about, _ in told] == ["history rewritten", "prompt read again"], told
    assert "differs at tool 2" in told[0][1] and "160,000" in told[1][1], told


def test_a_lease_that_cannot_be_given_up_leaves_the_stop_that_was_on_its_way(monkeypatch):
    """A runtime upgrade replaces the broker while a run is being stopped: giving the lease up then fails, and the run still ends as stopped."""

    @contextlib.contextmanager
    def leased():
        try:
            yield "http://127.0.0.1:1", SimpleNamespace(serving=SimpleNamespace(slot_context=32_768)), "m.gguf"
        finally:
            raise RuntimeError("PLUMBING: no broker is running on this machine")

    monkeypatch.setattr(delegate_resume.claude, "alias_of", lambda *_: canned.CANNED_MODEL_ID)
    with pytest.raises(SystemExit) as stopped, delegate_resume.Model(leased, lambda _model: None):
        raise SystemExit(143)
    assert stopped.value.code == 143
    with delegate_resume.Model(leased, lambda _model: None):
        pass


@pytest.mark.parametrize("total_gib,kv_per_token,expected", [(128, 26_000, 26_000 * 262_144 * 4 // 2**20), (64, 26_000, 16_384),
                                                              (32, 26_000, None), (128, 0, None)])
def test_the_prompt_cache_of_a_coding_session_holds_two_conversations_where_the_machine_has_room(total_gib, kv_per_token, expected):
    assert harnessing.prompt_cache_mb(kv_per_token * 262_144, total_gib * 2**30) == expected


def test_a_coding_session_asks_for_that_prompt_cache_when_it_starts_the_server(monkeypatch):
    monkeypatch.setattr(harnessing.profile, "profile_for", lambda *_a, **_k: None)
    monkeypatch.setattr(harnessing, "drafted", lambda config, *_a, **_k: config)
    monkeypatch.setattr(harnessing.estimate, "read_meta", lambda _path: {"PLUMBING": 1})
    monkeypatch.setattr(harnessing.estimate, "estimate_meta", lambda *_a, **_k: SimpleNamespace(kv_cache_bytes=26_000 * 262_144))
    monkeypatch.setattr(harnessing.hub, "total_memory", lambda: 128 * 2**30)
    got = harnessing.config_for("/PLUMBING/m.gguf", harnessing.Want(ctx=262_144), lambda _line: None)
    assert got.serving.cache_ram_mb == 26_000 * 262_144 * 4 // 2**20
