# ruff: noqa: RUF001
"""Nobody is asked about a model. A pull a person or the app asked for fetches from any publisher and the pin notes what
was unusual; a pull an agent started is refused at once for what is unusual unless the model is installed or its owner
is in the allow list; bytes other than a commit was pulled with are refused and the file kept stays. Real fake Hub on a
loopback socket, real sealed sentinel store and real request inbox under the test's state root."""

from __future__ import annotations

import contextlib
import dataclasses

import pytest
from model_pins import pinned
from net_site import gguf_bytes

from poolhouse import caller, http, hub, requests, sentinel
from poolhouse.hub import admission, listing, publishers, remote
from poolhouse.serve import guarded
from poolhouse.serve.backend import LlamaServerBackend, ServerFailed, ServerSpec
from poolhouse.serve.manager import ServerManager
from poolhouse.testing.fakehub import fake_hub
from poolhouse.testing.fakes import fake_binary

FILE = "thing-Q4_K_M.gguf"
UNKNOWN = "This model is from maker, who Poolhouse does not know."


def repos(name: str = "unsloth/thing-GGUF", seed: int = 1, file: str = FILE):
    return {name: {file: gguf_bytes(extra=4096 + seed)}}


@pytest.fixture(autouse=True)
def a_person_started_this(monkeypatch):
    """The process is a person's unless a test says otherwise, whoever runs the suite."""
    for name in caller.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def an_agent(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")


@pytest.fixture
def hub_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(http, "check", lambda url: url)


@pytest.fixture
def stand(monkeypatch, hub_cache):
    """``stand(repos, official=True)``: a fake Hub the shipped publisher list applies to."""
    with contextlib.ExitStack() as stack:
        def make(held, *, official: bool = True):
            server = stack.enter_context(fake_hub(held))
            server.point(monkeypatch, known=False, official=official)
            return server

        yield make


def pins():
    return sentinel.default().manifest.pins()


def allow(*lines: str) -> None:
    where = admission.allow_file()
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


# -- a pull a person asked for fetches from any publisher and says what was unusual --------
def test_a_person_pulls_from_an_unknown_publisher_with_no_question_and_the_pin_says_so(stand):
    server = stand(repos("maker/thing-GGUF"))
    got = hub.pull(f"hf:maker/thing-GGUF/{FILE}")
    assert got.name == FILE and server.downloads
    assert requests.list_requests() == []
    commit = server.commit("maker/thing-GGUF")
    (pin,) = pins().values()
    assert (pin.publisher, pin.commit, pin.source, pin.source_ref) == (
        "maker", commit, "pull", f"maker/thing-GGUF/{FILE}")
    assert pin.note == UNKNOWN and len(pin.commit) == 40 and pin.commit != "main"
    (event,) = sentinel.default().bus.recent(kind="model.pinned_at_pull")
    assert event.evidence["note"] == UNKNOWN


def test_a_known_publisher_is_fetched_with_nothing_to_note(stand):
    server = stand(repos("unsloth/thing-GGUF"))
    got = hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    assert got.name == FILE and server.downloads and requests.list_requests() == []
    (pin,) = pins().values()
    assert pin.publisher == "unsloth" and pin.commit == server.commit("unsloth/thing-GGUF") and pin.note == ""


def test_a_lookalike_of_a_known_publisher_is_fetched_for_a_person_and_noted(stand):
    server = stand({"Unsloth/thing-GGUF": {FILE: gguf_bytes(extra=4100)}})
    hub.pull(f"hf:Unsloth/thing-GGUF/{FILE}")
    assert server.downloads
    (pin,) = pins().values()
    assert pin.note == "This model is from Unsloth, who Poolhouse does not know."


def test_a_name_that_says_the_limits_were_removed_is_fetched_for_a_person_and_noted(stand):
    server = stand({**repos("unsloth/thing-abliterated-GGUF"),
                    **repos("google/thing-GGUF", file="thing-uncensored-Q4_K_M.gguf")})
    hub.pull(f"hf:unsloth/thing-abliterated-GGUF/{FILE}")
    hub.pull("hf:google/thing-GGUF/thing-uncensored-Q4_K_M.gguf")
    assert len(server.downloads) == 2
    said = "Its name says it was changed to answer what the original would refuse."
    assert {p.publisher: p.note for p in pins().values()} == {"unsloth": said, "google": said}


def test_a_mirror_is_fetched_from_for_a_person_and_the_host_is_noted(stand):
    server = stand(repos("unsloth/thing-GGUF"), official=False)
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    (pin,) = pins().values()
    assert server.downloads and pin.host == server.url.split("//", 1)[1]
    assert pin.note == f"This model is from unsloth on {pin.host}, a Hub Poolhouse does not know."


def test_the_app_names_a_person_whatever_marker_the_process_carries(stand, an_agent):
    server = stand(repos("maker/thing-GGUF"))
    assert hub.pull(f"hf:maker/thing-GGUF/{FILE}", trust=hub.PERSON).name == FILE
    assert server.downloads and requests.list_requests() == []


def test_a_trust_is_a_trust_and_not_a_word(stand):
    server = stand(repos("maker/thing-GGUF"))
    for word in ("library", "person", True, 1):
        with pytest.raises(TypeError, match="PERSON or AGENT"):
            hub.pull(f"hf:maker/thing-GGUF/{FILE}", trust=word)
    assert server.downloads == {}


# -- a pull an agent started is refused for what is unusual, at once, and asks nobody -----------
def test_an_agents_pull_from_an_unknown_publisher_is_refused_with_the_reason_and_files_no_request(stand):
    server = stand(repos("maker/thing-GGUF"))
    with pytest.raises(admission.NotAllowed) as raised:
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", trust=hub.AGENT)
    said = str(raised.value)
    assert said.startswith(f"Not fetched for an agent: {UNKNOWN} It is maker/thing-GGUF at "
                           f"{server.commit('maker/thing-GGUF')[:12]}.")
    assert str(admission.allow_file()) in said
    assert server.downloads == {} and pins() == {} and requests.list_requests() == []


def test_a_process_carrying_an_agent_marker_pulls_as_an_agent_unless_a_person_is_named(stand, monkeypatch):
    server = stand(repos("maker/thing-GGUF"))
    for marker in ("CLAUDECODE", "POOLHOUSE_AGENT", "CODEX_THREAD_ID"):
        monkeypatch.setenv(marker, "1")
        with pytest.raises(admission.NotAllowed, match="Not fetched for an agent"):
            hub.pull(f"hf:maker/thing-GGUF/{FILE}")
        monkeypatch.delenv(marker)
    assert server.downloads == {}
    assert hub.pull(f"hf:maker/thing-GGUF/{FILE}").name == FILE


def test_an_agent_is_refused_the_same_way_by_every_surface(stand, an_agent):
    server = stand({**repos("maker/thing-GGUF"),
                    "maker/base": {"config.json": b"{}", "model.safetensors": b"x" * 16}})
    with pytest.raises(admission.NotAllowed):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}")
    with pytest.raises(listing.FetchError, match="Not fetched for an agent"):
        hub.fetch(f"hf:maker/thing-GGUF/{FILE}")
    with pytest.raises(admission.NotAllowed):
        hub.snapshot("maker/base")
    with pytest.raises(admission.NotAllowed):
        hub.base_folder("hf:maker/base")
    assert server.downloads == {}


def test_a_known_publisher_is_fetched_for_an_agent(stand, an_agent):
    server = stand(repos("unsloth/thing-GGUF"))
    assert hub.pull(f"hf:unsloth/thing-GGUF/{FILE}").name == FILE and server.downloads


@pytest.mark.parametrize("repo,file", [("unsloth/thing-abliterated-GGUF", FILE),
                                       ("google/thing-GGUF", "thing-uncensored-Q4_K_M.gguf")])
def test_an_agent_is_refused_a_name_that_says_the_limits_were_removed_even_from_a_known_publisher(
        stand, an_agent, repo, file):
    server = stand(repos(repo, file=file))
    with pytest.raises(admission.NotAllowed, match="changed to answer what the original would refuse"):
        hub.pull(f"hf:{repo}/{file}")
    assert server.downloads == {} and pins() == {}


def test_an_agent_is_refused_a_mirror_even_for_a_known_publisher(stand, an_agent):
    server = stand(repos("unsloth/thing-GGUF"), official=False)
    with pytest.raises(admission.NotAllowed, match=r"on 127\.0\.0\.1.*a Hub Poolhouse does not know"):
        hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    assert server.downloads == {}


def test_a_pull_request_of_a_known_publisher_is_noted_for_a_person_and_refused_for_an_agent(stand, monkeypatch):
    server = stand(repos())
    pull_request = "ab" * 20
    server.prs["unsloth/thing-GGUF"] = {"refs/pr/7": pull_request}
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(admission.NotAllowed, match=r"not from unsloth's own branch"):
        hub.pull(f"hf:unsloth/thing-GGUF/{FILE}@refs/pr/7")
    assert server.downloads == {} and pins() == {}
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}@{pull_request}", trust=hub.PERSON)
    (pin,) = pins().values()
    assert pin.commit == pull_request and pin.note == "This file is not from unsloth's own branch."


def test_an_older_commit_of_the_branch_is_the_publishers_own_for_an_agent(stand, an_agent):
    server = stand(repos())
    first = server.commit("unsloth/thing-GGUF")
    server.moved["unsloth/thing-GGUF"] = 1
    assert hub.pull(f"hf:unsloth/thing-GGUF/{FILE}@{first}").name == FILE


def test_an_agent_may_pull_what_a_person_listed_by_owner_or_by_repository(stand, an_agent):
    server = stand({**repos("maker/thing-GGUF"), **repos("other/thing-GGUF"), **repos("third/thing-GGUF")})
    allow("# models the agents of this machine may pull", "maker   # a whole publisher", "other/thing-GGUF")
    assert hub.pull(f"hf:maker/thing-GGUF/{FILE}").name == FILE
    assert hub.pull(f"hf:other/thing-GGUF/{FILE}").name == FILE
    with pytest.raises(admission.NotAllowed, match="third"):
        hub.pull(f"hf:third/thing-GGUF/{FILE}")
    assert {p.publisher for p in pins().values()} == {"maker", "other"}
    assert "third/thing-GGUF/" + FILE not in server.downloads


def test_a_line_names_a_repository_or_an_owner_and_nothing_near_it(stand, an_agent):
    stand({**repos("maker/thing-GGUF"), **repos("maker2/thing-GGUF"), **repos("maker/other-GGUF")})
    allow("maker/thing-GGUF", "Maker", "mak", "maker/thing")
    assert hub.pull(f"hf:maker/thing-GGUF/{FILE}").name == FILE
    for repo in ("maker2/thing-GGUF", "maker/other-GGUF"):
        with pytest.raises(admission.NotAllowed):
            hub.pull(f"hf:{repo}/{FILE}")


def test_the_allow_list_is_read_when_a_pull_is_checked(stand, an_agent):
    stand(repos("maker/thing-GGUF"))
    with pytest.raises(admission.NotAllowed):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}")
    allow("maker")
    assert hub.pull(f"hf:maker/thing-GGUF/{FILE}").name == FILE


def test_a_model_already_on_this_machine_is_no_pull_for_an_agent_to_refuse(stand, monkeypatch):
    server = stand(repos("maker/thing-GGUF"))
    hub.pull(f"hf:maker/thing-GGUF/{FILE}")
    before = dict(server.downloads)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert hub.pull(f"hf:maker/thing-GGUF/{FILE}").name == FILE
    assert server.downloads == before


def test_the_command_line_pulls_for_a_person_and_refuses_an_agent_with_the_reason(stand, monkeypatch, capsys):
    from poolhouse.serve import models_cli

    server = stand({"maker/thing": {"config.json": b'{"model_type": "llama"}', FILE: gguf_bytes(extra=4096)}})
    monkeypatch.setenv("CLAUDECODE", "1")
    assert models_cli.run(["snapshot", "maker/thing"]) != 0
    assert "Not fetched for an agent" in capsys.readouterr().err and server.downloads == {}
    monkeypatch.delenv("CLAUDECODE")
    assert models_cli.run(["snapshot", "maker/thing"]) == 0
    assert server.downloads and "maker, who Poolhouse does not know" in next(iter(pins().values())).note


# -- a floating revision is resolved to its commit before anything is listed or fetched ------
def test_a_floating_revision_is_resolved_and_every_request_names_the_commit(stand):
    server = stand(repos("unsloth/thing-GGUF"))
    commit = server.commit("unsloth/thing-GGUF")
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    paths = [path for _, path, _, _ in server.hub_seen]
    assert any(f"/resolve/{commit}/" in path for path in paths)
    assert not any("/main" in path.split("?")[0] for path in paths if "/resolve/" in path or "/tree/" in path)
    assert any(f"/tree/{commit}" in path for path in paths)
    assert all(pin.commit == commit for pin in pins().values())


def test_a_reference_names_a_commit_as_written(stand):
    server = stand(repos("unsloth/thing-GGUF"))
    commit = server.commit("unsloth/thing-GGUF")
    hub.pull(f"hf:unsloth/thing-GGUF/{FILE}@{commit}")
    assert not any("/revision/" in path for _, path, _, _ in server.hub_seen)
    (pin,) = pins().values()
    assert pin.commit == commit


# -- the same file, other bytes ---------------------------------------------------------------
def test_other_bytes_for_a_commit_already_pulled_are_refused_and_the_file_kept_stays_usable(stand, tmp_path):
    held = repos("unsloth/thing-GGUF")
    server = stand(held)
    first = hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    kept = first.read_bytes()
    before = dict(server.downloads)
    held["unsloth/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    for _ in range(2):
        with pytest.raises(admission.DigestChanged, match="the file you have is unchanged"):
            hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    assert server.downloads == before
    assert first.read_bytes() == kept and len(pins()) == 1
    (pin,) = pins().values()
    assert guarded.verify(first, state_file=tmp_path / "servers.json", stop=lambda _p: None) == ""
    assert sentinel.default().store.state_of("model", pin.path) != sentinel.State.QUARANTINED
    assert requests.list_requests() == [] and not (tmp_path / "machine-state" / ".poolhouse-quarantine").exists()
    assert sentinel.default().bus.recent(kind="model.hub_digest_changed")


def test_a_stale_first_listing_is_replaced_by_listing_the_commit_again(stand, monkeypatch):
    server = stand(repos("unsloth/thing-GGUF"))
    first = hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    before = dict(server.downloads)
    listed, answered = remote.listing, []

    def stale_once(repo, commit, **options):
        rows = listed(repo, commit, **options)
        if answered:
            return rows
        answered.append(1)
        return [dataclasses.replace(one, sha256="0" * 64) if one.path == FILE else one for one in rows]

    monkeypatch.setattr(remote, "listing", stale_once)
    assert hub.pull(f"hf:unsloth/thing-GGUF/{FILE}") == first
    assert len(answered) == 1 and server.downloads == before and len(pins()) == 1


def test_a_removed_file_lets_the_next_pull_take_the_hubs_bytes(stand):
    held = repos("unsloth/thing-GGUF")
    server = stand(held)
    first = hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    (old,) = pins().values()
    held["unsloth/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    with pytest.raises(admission.DigestChanged):
        hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    first.resolve().unlink()
    got = hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    assert got.read_bytes() == held["unsloth/thing-GGUF"][FILE] and server.downloads
    assert any(p.sha256 != old.sha256 and p.commit == old.commit for p in pins().values())


def test_other_bytes_under_a_new_commit_are_a_normal_update_and_the_old_file_stays(stand):
    held = repos("unsloth/thing-GGUF")
    server = stand(held)
    first = hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    kept = first.read_bytes()
    server.moved["unsloth/thing-GGUF"] = 1
    assert hub.pull(f"hf:unsloth/thing-GGUF/{FILE}").name == FILE
    server.moved["unsloth/thing-GGUF"] = 2
    held["unsloth/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    updated = hub.pull(f"hf:unsloth/thing-GGUF/{FILE}")
    assert updated.read_bytes() == held["unsloth/thing-GGUF"][FILE] and first.read_bytes() == kept
    assert requests.list_requests() == []
    newest = max(pins().values(), key=lambda p: p.pinned_at)
    assert newest.commit == server.commit("unsloth/thing-GGUF") and newest.source_ref.endswith(FILE)


def test_a_file_name_from_a_listing_is_shown_bounded(stand):
    long_name = "n" * 200 + ".gguf"
    held = {"unsloth/thing-GGUF": {long_name: gguf_bytes(extra=4100)}}
    stand(held)
    hub.pull(f"hf:unsloth/thing-GGUF/{long_name}")
    held["unsloth/thing-GGUF"][long_name] = gguf_bytes(extra=9000)
    with pytest.raises(admission.DigestChanged) as raised:
        hub.pull(f"hf:unsloth/thing-GGUF/{long_name}")
    assert "n" * 100 not in str(raised.value)


# -- llama-server downloads nothing ----------------------------------------------------------
def test_a_draft_from_an_unknown_publisher_is_fetched_for_a_person_and_refused_to_an_agent(stand, tmp_path, monkeypatch):
    stand({**repos("unsloth/thing-GGUF"), **repos("maker/head-GGUF", file="head-Q8_0.gguf")})
    spec = ServerSpec(context=4096, model="/m/w.gguf", draft="hf:maker/head-GGUF")
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(ServerFailed, match=r"could not fetch the draft.*Not fetched for an agent"):
        LlamaServerBackend.resolved_draft(spec)
    monkeypatch.delenv("CLAUDECODE")
    fetched = LlamaServerBackend.resolved_draft(spec)
    assert fetched.draft.endswith("head-Q8_0.gguf")
    argv = LlamaServerBackend(binary=fake_binary(tmp_path, help_text="")).command(fetched)
    assert "-hfd" not in argv and argv[argv.index("-md") + 1] == fetched.draft


def test_an_unfetched_reference_never_reaches_the_command_line(tmp_path):
    backend = LlamaServerBackend(binary=fake_binary(tmp_path, help_text=""))
    for spec in (ServerSpec(context=4096, model="hf:maker/x-GGUF/w.gguf"),
                 ServerSpec(context=4096, model="/m/w.gguf", draft="hf:maker/x-GGUF"),
                 ServerSpec(context=4096, model="/m/w.gguf", draft="hf:maker/x-GGUF/h.gguf"),
                 ServerSpec(context=4096, model="/m/w.gguf", mmproj="hf:maker/x-GGUF/mmproj-F16.gguf")):
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
def test_a_projector_that_is_not_pinned_is_pinned_as_it_is(tmp_path):
    projector = tmp_path / "mmproj-F16.gguf"
    projector.write_bytes(b"GGUF" + b"\x01" * 64)
    assert guarded.verify(projector, state_file=tmp_path / "servers.json", stop=lambda _p: None) == ""
    assert pins()[str(projector)].source == "first-use" and requests.list_requests() == []


def test_a_projector_that_no_longer_matches_its_pin_is_refused_at_load(tmp_path):
    from poolhouse.serve.ports import free_port
    from poolhouse.testing.canned import canned_binary

    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * 64)
    projector = tmp_path / "mmproj-F16.gguf"
    projector.write_bytes(b"GGUF" + b"\x01" * 64)
    pinned(model)
    pinned(projector)
    projector.write_bytes(b"GGUF" + b"\x02" * 64)
    manager = ServerManager(backend=LlamaServerBackend(binary=canned_binary(tmp_path)),
                            state_file=tmp_path / "servers.json")
    spec = ServerSpec(context=4096, model=model, mmproj=projector, port=free_port())
    with pytest.raises(guarded.SentinelRefused, match="does not match its pin"):
        manager.lease(spec, roam=False, timeout=30.0, check_flags=False, preflight=False,
                      warmup_request=False)
