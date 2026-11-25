"""`poolhouse-agent` on a machine where the model is already served: it takes the server that is up.

The servers here are `CannedServer`s, loopback look-alikes that say in every reply that they are not a model
(`poolhouse.testing.canned`); what is under test is the decision about which server a session uses, never what a
model says.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from poolhouse import harness, harnessing
from poolhouse.serve import binary as binary_module
from poolhouse.serve.leases import lease_file
from poolhouse.serve.profile import record
from poolhouse.testing.canned import Canned, CannedServer
from poolhouse.testing.registry import record_server

MODEL = "/models/Qwen3.8-Flash-Next-UD-IQ2_XS-00001-of-00003.gguf"
PATCHED = Path("/templates/Qwen3.8-Flash-Next-UD-IQ2_XS-00001-of-00003.jinja")


@pytest.fixture
def standing():
    """A server already up on the model's weights, recorded the way `poolhouse-serve up` records one."""
    started: list[CannedServer] = []

    def up(*, context=256000, slots=1, template=PATCHED, model=MODEL, **fields):
        fake = CannedServer(Canned(served_path=model, context=context, slots=slots))
        started.append(fake)
        record_server(lease_file(), fake.port, model=model, context=context, parallel=slots,
                      chat_template_file=str(template or ""), **fields)
        return fake

    yield up
    for fake in started:
        fake.close()


def said_to(lines):
    return lines.append


def test_a_tracked_mainline_build_is_not_a_fork(tmp_path):
    """The tracker names every build it makes (`b11577-cbe28bb3c`); that is a label, not a choice of fork."""
    build = tmp_path / "b11577-cbe28bb3c"
    build.mkdir()
    server = build / "llama-server"
    server.write_bytes(b"x")
    (build / "BUILD.json").write_text(json.dumps(
        {"track": True, "source": "track", "name": "b11577-cbe28bb3c", "build": 11577}))
    assert binary_module.borrows(server) is False


def test_a_named_build_of_another_repository_is_a_fork(tmp_path):
    build = tmp_path / "unsloth-b10715"
    build.mkdir()
    server = build / "llama-server"
    server.write_bytes(b"x")
    (build / "BUILD.json").write_text(json.dumps({"name": "unsloth", "repo": "unslothai/llama.cpp"}))
    assert binary_module.borrows(server) is True


def test_the_record_of_a_server_that_started_without_its_head_names_no_head(tmp_path):
    """Driven 2026-11-24: the server fell back to no head and the lease record still named the head it
    had refused, so a later ask for that head was told a server without one was compatible."""
    from poolhouse.serve.backend import LlamaServerBackend, ServerInfo, ServerSpec
    from poolhouse.serve.leases import recorded_servers
    from poolhouse.serve.manager import ServerManager
    from poolhouse.testing.fakes import fake_binary

    state = tmp_path / "servers.json"
    manager = ServerManager(LlamaServerBackend(binary=fake_binary(tmp_path, help_text="")), state_file=state)
    spec = ServerSpec(model=MODEL, port=65001, context=4096, draft="/heads/mtp-shared.gguf",
                      spec_type="draft-mtp", mtp=True)
    info = ServerInfo(base_url="http://127.0.0.1:65001", port=65001, pid=os.getpid(), backend="llama.cpp",
                      mtp="", mtp_note="MTP off: the server would not start with it")
    manager._record(spec, info)
    entry = recorded_servers(state)[65001]
    assert entry["draft"] == "" and entry["spec_type"] == ""
    manager._record(spec, ServerInfo(base_url=info.base_url, port=65001, pid=os.getpid(),
                                     backend="llama.cpp", mtp="mtp-shared.gguf"))
    assert recorded_servers(state)[65001]["draft"] == "/heads/mtp-shared.gguf"


def test_a_server_running_with_a_head_is_the_server_a_lease_that_wants_one_asks_for():
    """The record keeps the head's name (or `embedded`) and a lease asks yes or no: `is` between the two
    refused every identical ask of a server that drafts."""
    from dataclasses import replace

    from poolhouse.serve import admission
    from poolhouse.serve.backend import ServerSpec

    kept = {"cache_type_k": "q8_0", "cache_type_v": "q8_0"}
    spec = ServerSpec(model=MODEL, port=1, context=4096, mtp=True)
    assert admission.compatible(spec, {**kept, "mtp": "embedded"}, [])
    assert not admission.compatible(spec, {**kept, "mtp": ""}, [])
    assert admission.compatible(replace(spec, mtp=False), {**kept, "mtp": ""}, [])
    assert not admission.compatible(replace(spec, mtp=False), {**kept, "mtp": "mtp-x.gguf"}, [])


def test_the_manager_adopts_what_the_session_asks_for(standing, tmp_path):
    from poolhouse.serve.backend import LlamaServerBackend, ServerSpec
    from poolhouse.serve.manager import ServerManager
    from poolhouse.testing.fakes import fake_binary

    fake = standing(mtp="")
    config = harnessing.adopted(MODEL, harnessing.Want(), PATCHED, said_to([]))
    assert config.lease()["mtp"] is False, "a server with no head is asked for as one with no head"
    manager = ServerManager(LlamaServerBackend(binary=fake_binary(tmp_path, help_text="")), state_file=lease_file())
    info = manager.adopt(ServerSpec(model=MODEL, **config.lease(), chat_template_file=PATCHED))
    assert info is not None and info.adopted and info.port == fake.port


def test_a_server_that_drafts_is_asked_for_without_a_say_on_the_head(standing):
    standing(mtp="embedded")
    config = harnessing.adopted(MODEL, harnessing.Want(), PATCHED, said_to([]))
    assert config is not None and "mtp" not in config.lease()


def test_a_record_of_another_quantisation_is_not_a_coding_sessions_settings():
    ask_on_iq4 = record("Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf", cache_type="q8_0",
                        draft="mtp-Qwen3.8-Flash-Next-shared-Q4_K_M.gguf", spec_type="draft-mtp")
    from poolhouse.serve import profile

    asked = "/models/Qwen3.8-Flash-Next-UD-IQ2_XS-00001-of-00003.gguf"
    assert profile.profile_for(asked, records=[ask_on_iq4]) is not None
    assert profile.profile_for(asked, records=[ask_on_iq4], same_quant=True) is None
    assert profile.profile_for(ask_on_iq4.model, records=[ask_on_iq4], same_quant=True) is not None


def test_the_server_up_on_these_weights_is_taken_as_it_serves_them(standing):
    fake = standing()
    lines: list[str] = []
    config = harnessing.adopted(MODEL, harnessing.Want(), PATCHED, said_to(lines))
    assert config is not None and config.serving.slots == 1
    assert config.port == fake.port, "the broker shares a lease only with an ask for the same port"
    assert config.serving.slot_context == 256000 and config.serving.cache_type == "q8_0"
    assert config.serving.draft == "" and config.serving.spec_type == "" and config.serving.build == ""
    assert config.lease()["context"] == 256000 and "chat_template_file" not in config.lease()
    assert lines == [] and fake.requests


def test_a_server_with_less_room_than_a_coding_session_needs_is_left_alone_and_the_sentence_says_why(standing):
    standing(context=harnessing.MIN_WINDOW // 2)
    lines: list[str] = []
    assert harnessing.adopted(MODEL, harnessing.Want(), PATCHED, said_to(lines)) is None
    assert len(lines) == 1 and f"{harnessing.MIN_WINDOW // 2:,}" in lines[0] and "leasing its own" in lines[0]


def test_a_context_the_person_asked_for_that_the_server_does_not_hold_is_not_adopted(standing):
    standing(context=256000)
    lines: list[str] = []
    want = harnessing.Want(ctx=262144)
    assert harnessing.adopted(MODEL, want, PATCHED, said_to(lines)) is None
    assert "262,144" in lines[0] and "256,000" in lines[0]
    assert harnessing.adopted(MODEL, harnessing.Want(ctx=131072), PATCHED, said_to([])) is not None


def test_a_server_on_the_models_own_refusing_template_is_not_adopted(standing):
    """Driven 2026-09-05: every Claude Code turn after the first was a 500 on such a server."""
    standing(template=None)
    lines: list[str] = []
    assert harnessing.adopted(MODEL, harnessing.Want(), PATCHED, said_to(lines)) is None
    assert "chat template" in lines[0] and "leasing its own" in lines[0]
    assert harnessing.adopted(MODEL, harnessing.Want(), None, said_to([])) is not None


def test_other_weights_and_a_server_that_does_not_answer_are_not_adopted(standing):
    other = standing(model="/models/Some-Other-Model-Q4_K_M.gguf")
    gone = standing()
    gone.close()
    assert other.port != gone.port
    assert harnessing.adopted(MODEL, harnessing.Want(), PATCHED, said_to([])) is None


def test_a_named_draft_head_is_not_something_a_server_without_it_serves(standing):
    standing(draft="", spec_type="")
    lines: list[str] = []
    named = harnessing.Want(draft="mtp-Qwen3.8-Flash-Next-shared-Q4_K_M.gguf")
    assert harnessing.adopted(MODEL, named, PATCHED, said_to(lines)) is None
    assert "draft head" in lines[0]
    assert harnessing.adopted(MODEL, harnessing.Want(draft="none"), PATCHED, said_to([])) is not None


def test_the_session_leases_what_is_up_and_not_its_own_variant(standing, monkeypatch):
    """The failure of 2026-11-24: 262,144 tokens, a draft head and a template asked of a server that held
    256,000 and none of them, and the broker waited 60s for memory and refused."""
    import contextlib

    from poolhouse import hub

    fake = standing()
    seen: dict = {}

    class Server:
        adopted = True
        base_url = fake.base_url
        port = fake.port

    @contextlib.contextmanager
    def serve(model, manager=None, **lease):
        seen["lease"] = lease
        yield Server()

    monkeypatch.setattr("poolhouse.serve.manager.serve", serve)
    monkeypatch.setattr(hub, "located", lambda *a, **k: Path(MODEL))
    monkeypatch.setattr(harness, "alias_of", lambda url, model: "flash")
    monkeypatch.setattr("poolhouse.serve.chat_template.written_beside", lambda model: PATCHED)
    lines: list[str] = []
    with harness.session("Qwen3.8-Flash-Next-UD-IQ2_XS", say=said_to(lines)) as agent:
        assert agent.base_url == fake.base_url
        assert agent.env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "256000"
    lease = seen["lease"]
    assert lease["context"] == 256000 and lease["parallel"] == 1 and lease["cache_type_k"] == "q8_0"
    assert "draft" not in lease and "spec_type" not in lease
    assert not any("scored best" in line or "automatically selected" in line for line in lines)
