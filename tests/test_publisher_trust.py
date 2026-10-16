# ruff: noqa: RUF001
"""A model is fetched only from a publisher Poolhouse knows, under a name that does not say it was altered,
and as the same bytes it was before; anything else waits for a person. Real fake Hub on a loopback socket,
real sealed sentinel store and real request inbox under the test's state root."""

from __future__ import annotations

import contextlib

import pytest
from model_approval import approve, approved
from net_site import gguf_bytes

from poolhouse import authority, http, hub, person, requests, sentinel
from poolhouse.hub import admission, publishers, remote
from poolhouse.serve import guarded
from poolhouse.serve.backend import LlamaServerBackend, ServerFailed, ServerSpec
from poolhouse.serve.manager import ServerManager
from poolhouse.testing.fakehub import fake_hub
from poolhouse.testing.fakes import fake_binary

FILE = "thing-Q4_K_M.gguf"
SENTENCE = ("This model is from {who}, who Poolhouse does not know. It is {repo} at {commit}. "
            "Models can hide behaviour that tests do not show. Use it anyway?")


def repos(name: str = "unsloth/thing-GGUF", seed: int = 1, file: str = FILE):
    return {name: {file: gguf_bytes(extra=4096 + seed)}}


@pytest.fixture
def hub_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(http, "check", lambda url: url)


@pytest.fixture
def stand(monkeypatch, hub_cache):
    """``stand(repos)``: a fake Hub the shipped publisher list applies to."""
    with contextlib.ExitStack() as stack:
        def make(held):
            server = stack.enter_context(fake_hub(held))
            server.point(monkeypatch, known=False)
            return server

        yield make


def pins():
    return sentinel.default().manifest.pins()


def asked(kind: str = "model_publisher"):
    return requests.list_requests(state="pending", kind=kind)


# -- the rule that matches a repository to a publisher -------------------------------------
def test_the_known_publishers_match_by_their_whole_owner():
    for repo in ("Qwen/Qwen3.8-27B-GGUF", "unsloth/Qwen3.8-27B-GGUF", "ggml-org/gemma-GGUF",
                 "google/gemma-GGUF", "bartowski/x-GGUF", "lmstudio-community/x-GGUF",
                 "mlx-community/Qwen3.8-Flash-Next-4bit", "litert-community/gemma-4-E2B-it-litert-lm"):
        assert publishers.trusted(repo), repo
    assert set(publishers.ORDER) == set(publishers.TRUSTED)


@pytest.mark.parametrize("repo", [
    "qwen/Qwen3-8B", "QWEN/Qwen3-8B", "Unsloth/x", "UNSLOTH/x",
    "Qwen./x", ".Qwen/x", "Qwen-/x", "Qwen_/x", "Qwen /x", " Qwen/x", "Qwen\n/x",
    "Qwen​/x", "Qwеn/x", "Qwen․/x", "Ｑwen/x", "unsłoth/x",
    "unsloth-fork/x", "unsloth.org/x", "google-research/x", "ggml-org2/x",
    "evil/Qwen3-8B", "evil/unsloth", "evil/google", "Qwen",
])
def test_a_namespace_that_is_not_exactly_a_known_publisher_is_not_trusted(repo):
    assert not publishers.trusted(repo)


@pytest.mark.parametrize("ref", ["hf:Qwen/../evil/x.gguf", "hf:Qwen/./x", "hf:Qwen​/x/y.gguf",
                                 "hf:Qwеn/x/y.gguf", "hf:Qwen./x", "hf:Qwen/x..y/z.gguf"])
def test_a_reference_that_is_not_a_hub_name_is_not_parsed(ref):
    with pytest.raises(ValueError):
        remote.parse(ref)


@pytest.mark.parametrize("name", [
    "Qwen3-8B-abliterated-GGUF", "model-Abliterated", "x-uncensored", "x_UNCENSORED_x", "x-decensored",
    "No-Refusal-x", "no refusal", "x-NoRefusals", "x-refusal-free", "x-without-refusal", "x-unfiltered",
    "x-jailbroken", "x-unaligned", "x-heretic", "x-orthogonalized",
    "ａｂｌｉｔｅｒａｔｅｄ",
    "аbliterated", "ab­lit​erated", "A.B.L.I.T.E.R.A.T.E.D",
])
def test_a_name_that_says_the_limits_were_removed_is_marked(name):
    assert publishers.marker(f"unsloth/{name}"), name
    assert publishers.marker("unsloth/x", f"dir/{name}.gguf"), name


@pytest.mark.parametrize("name", ["Qwen3.8-27B-GGUF", "gemma-4-E4B-it-qat", "thing-Q4_K_M.gguf",
                                  "mmproj-F16.gguf", "MTP/mtp-head-BF16.gguf"])
def test_an_ordinary_name_is_not_marked(name):
    assert publishers.marker(f"unsloth/{name}", name) == ""


# -- an unlisted publisher waits for a person ------------------------------------------------
def test_a_model_from_an_unlisted_publisher_is_refused_and_asks_a_person(stand):
    server = stand(repos("maker/thing-GGUF"))
    with pytest.raises(admission.NotApproved) as raised:
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    said = SENTENCE.format(who="maker", repo="maker/thing-GGUF", commit=server.commit("maker/thing-GGUF")[:12])
    assert str(raised.value).startswith(said)
    assert server.downloads == {} and pins() == {}
    (waiting,) = asked()
    assert waiting.human_only and waiting.reason == said
    assert dict(waiting.extra) == {"publisher": "maker", "repo": "maker/thing-GGUF",
                                   "commit": server.commit("maker/thing-GGUF"),
                                   "host": server.url.split("//", 1)[1]}
    assert [c.id for c in waiting.choices] == ["approve", "deny"]


def test_a_refusal_is_not_lifted_by_asking_again_or_by_a_denial(stand):
    server = stand(repos("maker/thing-GGUF"))
    for _ in range(2):
        with pytest.raises(admission.NotApproved):
            hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert len(asked()) == 1
    (waiting,) = asked()
    requests.answer(waiting.id, "deny", waiting.fingerprint, "terminal",
                    requests.Context(terminal=(True, True), env={}))
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert server.downloads == {}


def test_a_person_approves_one_repository_at_one_commit_and_the_pin_records_both(stand):
    server = stand(repos("maker/thing-GGUF"))
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert approve("model_publisher") == 1
    got = hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert got.name == FILE and server.downloads
    commit = server.commit("maker/thing-GGUF")
    (pin,) = pins().values()
    assert (pin.publisher, pin.commit, pin.source, pin.source_ref) == (
        "maker", commit, "pull", f"maker/thing-GGUF/{FILE}")
    assert len(pin.commit) == 40 and pin.commit != "main"


def test_an_approval_covers_neither_another_commit_nor_another_repository_of_the_publisher(stand):
    held = {**repos("maker/thing-GGUF"), **repos("maker/other-GGUF")}
    server = stand(held)
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    approve("model_publisher")
    hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/other-GGUF/{FILE}", members=False)
    server.moved["maker/thing-GGUF"] = 1
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    approve("model_publisher")
    assert hub.pull(f"hf:maker/other-GGUF/{FILE}", members=False).name == FILE


def test_an_agent_cannot_approve_a_publisher_even_where_answers_are_delegated(stand, monkeypatch):
    server = stand(repos("maker/thing-GGUF"))
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    assert authority.state_of("requests.answer") == authority.DELEGATED
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    (waiting,) = asked()
    for env in ({"CLAUDECODE": "1"}, {"POOLHOUSE_AGENT": "claude-x"}):
        with pytest.raises(person.HumanRequired):
            requests.answer(waiting.id, "approve", waiting.fingerprint, "terminal",
                            requests.Context(terminal=(True, True), env=env))
    with pytest.raises(person.HumanRequired):
        requests.answer(waiting.id, "approve", waiting.fingerprint, "ui",
                        requests.Context(env={"CLAUDECODE": "1"}))
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert server.downloads == {}


def test_a_request_in_other_words_is_not_an_approval_of_the_pull(stand):
    """A process can raise a request of the same kind with the right fields and other words; a person who
    answers those words has not been asked about the model."""
    server = stand(repos("maker/thing-GGUF"))
    commit = server.commit("maker/thing-GGUF")
    requests.raise_request(requests.Ask(
        "model_publisher", f"maker/thing-GGUF@{commit[:12]}", "Allow a harmless background task?",
        ("approve", "deny"),
        extra=(("publisher", "maker"), ("repo", "maker/thing-GGUF"), ("commit", commit),
               ("host", server.url.split("//", 1)[1]))))
    assert approve("model_publisher") == 1
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert server.downloads == {}
    assert approve("model_publisher") == 1
    assert hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False).name == FILE


def test_a_known_publisher_is_fetched_without_asking(stand):
    server = stand(repos("unsloth/thing-GGUF"))
    got = hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False)
    assert got.name == FILE and server.downloads
    assert asked() == [] and requests.list_requests() == []
    (pin,) = pins().values()
    assert pin.publisher == "unsloth" and pin.commit == server.commit("unsloth/thing-GGUF")


def test_a_lookalike_of_a_known_publisher_is_asked_about(stand):
    held = {"Unsloth/thing-GGUF": {FILE: gguf_bytes(extra=4100)}}
    server = stand(held)
    with pytest.raises(admission.NotApproved, match="This model is from Unsloth"):
        hub.pull(f"hf:Unsloth/thing-GGUF/{FILE}", members=False)
    assert server.downloads == {}


# -- a name that says the limits were removed asks, whoever published it ---------------------
def test_an_abliterated_repository_of_a_known_publisher_asks(stand):
    server = stand(repos("unsloth/thing-abliterated-GGUF"))
    with pytest.raises(admission.NotApproved, match="changed to answer what the original would refuse"):
        hub.pull(f"hf:unsloth/thing-abliterated-GGUF/{FILE}", members=False)
    assert server.downloads == {}
    (waiting,) = asked()
    assert waiting.human_only and waiting.reason.endswith("Use it anyway?")
    assert approve("model_publisher") == 1
    assert hub.pull(f"hf:unsloth/thing-abliterated-GGUF/{FILE}", members=False).name == FILE


def test_an_uncensored_file_in_an_ordinary_repository_asks(stand):
    name = "thing-uncensored-Q4_K_M.gguf"
    server = stand(repos("google/thing-GGUF", file=name))
    with pytest.raises(admission.NotApproved, match="changed to answer"):
        hub.pull(f"hf:google/thing-GGUF/{name}", members=False)
    assert server.downloads == {}


# -- a floating revision is resolved to its commit before anything is listed or fetched ------
def test_a_floating_revision_is_resolved_and_every_request_names_the_commit(stand):
    server = stand(repos("unsloth/thing-GGUF"))
    commit = server.commit("unsloth/thing-GGUF")
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False)
    paths = [path for _, path, _, _ in server.hub_seen]
    assert any(f"/resolve/{commit}/" in path for path in paths)
    assert not any("/main" in path.split("?")[0] for path in paths if "/resolve/" in path or "/tree/" in path)
    assert any(f"/tree/{commit}" in path for path in paths)
    assert all(pin.commit == commit for pin in pins().values())


def test_a_reference_names_a_commit_as_written(stand):
    server = stand(repos("unsloth/thing-GGUF"))
    commit = server.commit("unsloth/thing-GGUF")
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}@{commit}", members=False)
    assert not any("/revision/" in path for _, path, _, _ in server.hub_seen)
    (pin,) = pins().values()
    assert pin.commit == commit


# -- the same file, other bytes --------------------------------------------------------------
def test_other_bytes_for_a_commit_already_pulled_are_refused_and_quarantined(stand):
    held = repos("unsloth/thing-GGUF")
    server = stand(held)
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False)
    before = dict(server.downloads)
    held["unsloth/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    with pytest.raises(admission.DigestChanged, match="quarantined by sentinel"):
        hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False)
    assert server.downloads == before
    commit = server.commit("unsloth/thing-GGUF")
    key = f"hub:unsloth/thing-GGUF/{FILE}@{commit}"
    assert sentinel.default().store.state_of("model", key) == sentinel.State.QUARANTINED
    with pytest.raises(admission.DigestChanged):
        hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False)
    assert len(pins()) == 1


def test_other_bytes_under_a_new_commit_ask_a_person_and_the_same_bytes_do_not(stand):
    held = repos("unsloth/thing-GGUF")
    server = stand(held)
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False)
    server.moved["unsloth/thing-GGUF"] = 1
    assert hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False).name == FILE
    assert asked() == []
    server.moved["unsloth/thing-GGUF"] = 2
    held["unsloth/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    before = dict(server.downloads)
    with pytest.raises(admission.NotApproved, match="is not the file Poolhouse pulled from"):
        hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False)
    assert server.downloads == before
    assert approve("model_publisher") == 1
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}", members=False)
    newest = max(pins().values(), key=lambda p: p.pinned_at)
    assert newest.commit == server.commit("unsloth/thing-GGUF") and newest.source_ref.endswith(FILE)


# -- llama-server downloads nothing ----------------------------------------------------------
def test_a_draft_from_an_unlisted_publisher_is_refused_before_the_server_is_started(stand, tmp_path):
    stand({**repos("unsloth/thing-GGUF"), **repos("maker/head-GGUF", file="head-Q8_0.gguf")})
    spec = ServerSpec(model="/m/w.gguf", draft="hf:maker/head-GGUF")
    with pytest.raises(ServerFailed, match=r"could not fetch the draft.*This model is from maker"):
        LlamaServerBackend.resolved_draft(spec)
    approve("model_publisher")
    fetched = LlamaServerBackend.resolved_draft(spec)
    assert fetched.draft.endswith("head-Q8_0.gguf")
    argv = LlamaServerBackend(binary=fake_binary(tmp_path, help_text="")).command(fetched)
    assert "-hfd" not in argv and argv[argv.index("-md") + 1] == fetched.draft


def test_an_unfetched_reference_never_reaches_the_command_line(tmp_path):
    backend = LlamaServerBackend(binary=fake_binary(tmp_path, help_text=""))
    for spec in (ServerSpec(model="hf:maker/x-GGUF/w.gguf"),
                 ServerSpec(model="/m/w.gguf", draft="hf:maker/x-GGUF"),
                 ServerSpec(model="/m/w.gguf", draft="hf:maker/x-GGUF/h.gguf"),
                 ServerSpec(model="/m/w.gguf", mmproj="hf:maker/x-GGUF/mmproj-F16.gguf")):
        with pytest.raises(ServerFailed, match="must be fetched before serving"):
            backend.command(spec)


def test_the_download_flags_and_variables_of_llama_server_are_never_passed(monkeypatch):
    from poolhouse.serve import downloads

    for flag in ("-hf", "-hfd", "--hf-repo", "--hf-file=x", "-mu", "--model-url", "--mmproj-url",
                 "--hf-token", "-dr"):
        assert downloads.unlisted_flag(["--ctx-size", "8", flag, "owner/x"], downloads.LLAMA_FLAGS) == flag.split("=")[0]
    assert downloads.unlisted_flag(["--ctx-size", "8", "--threads", "4", "-1"], downloads.LLAMA_FLAGS) == ""
    kept = downloads.clean_env({"LLAMA_ARG_HF_REPO": "a/b", "LLAMA_ARG_HFD_REPO": "a/b",
                                "LLAMA_ARG_MODEL_URL": "http://x", "LLAMA_ARG_MMPROJ_URL": "u",
                                "LLAMA_ARG_N_GPU_LAYERS": "99", "PATH": "/bin"})
    assert kept == {"LLAMA_ARG_N_GPU_LAYERS": "99", "PATH": "/bin"}


# -- the projector is checked at load like the model and the draft ----------------------------
def test_a_projector_that_is_not_pinned_waits_for_a_person_at_load(tmp_path):
    from poolhouse.serve.ports import free_port
    from poolhouse.testing.fakes import fake_llama_binary

    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * 64)
    approved(model)
    projector = tmp_path / "mmproj-F16.gguf"
    projector.write_bytes(b"GGUF" + b"\x01" * 64)
    manager = ServerManager(backend=LlamaServerBackend(binary=fake_llama_binary(tmp_path)),
                            state_file=tmp_path / "servers.json")
    spec = ServerSpec(model=model, mmproj=projector, port=free_port())
    with pytest.raises(guarded.SentinelRefused, match=r"mmproj-F16\.gguf was not downloaded by Poolhouse"):
        manager.lease(spec, roam=False, timeout=30.0, check_flags=False, preflight=False,
                      warmup_request=False)
    assert str(projector) not in pins()


def test_a_projector_that_no_longer_matches_its_pin_is_refused_at_load(tmp_path):
    from poolhouse.serve.ports import free_port
    from poolhouse.testing.fakes import fake_llama_binary

    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * 64)
    projector = tmp_path / "mmproj-F16.gguf"
    projector.write_bytes(b"GGUF" + b"\x01" * 64)
    approved(model)
    approved(projector)
    projector.write_bytes(b"GGUF" + b"\x02" * 64)
    manager = ServerManager(backend=LlamaServerBackend(binary=fake_llama_binary(tmp_path)),
                            state_file=tmp_path / "servers.json")
    spec = ServerSpec(model=model, mmproj=projector, port=free_port())
    with pytest.raises(guarded.SentinelRefused, match="does not match its pin"):
        manager.lease(spec, roam=False, timeout=30.0, check_flags=False, preflight=False,
                      warmup_request=False)
