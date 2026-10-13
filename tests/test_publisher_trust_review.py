"""What a pull request, a mirror, a hand-made pin, a flag that names a file, another fetch path and a forged request
cannot do to the publisher check. Real fake Hub on a loopback socket, real sealed sentinel store and request inbox."""

from __future__ import annotations

import argparse
import contextlib
import struct
from pathlib import Path

import pytest
from model_approval import approve, approved
from net_site import gguf_bytes

from poolhouse import http, hub, person, requests, sentinel
from poolhouse.fleet.catalogue import SUGGESTED, UNFILTERED, is_unfiltered
from poolhouse.fleet.models import ModelError, Models
from poolhouse.hub import admission, publishers
from poolhouse.sentinel import cli as sentinel_cli
from poolhouse.serve import guarded
from poolhouse.serve.backend import LlamaServerBackend, ServerFailed, ServerSpec
from poolhouse.serve.manager import ServerManager
from poolhouse.serve.python_engines import SGLangBackend, VllmBackend
from poolhouse.testing.fakehub import fake_hub
from poolhouse.testing.fakes import fake_binary

FILE = "thing-Q4_K_M.gguf"
REPO = "unsloth/thing-GGUF"


def repos(name: str = REPO, seed: int = 1, file: str = FILE):
    return {name: {file: gguf_bytes(extra=4096 + seed)}}


@pytest.fixture
def hub_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(http, "check", lambda url: url)


@pytest.fixture
def stand(monkeypatch, hub_cache):
    """``stand(repos, official=True)``: a fake Hub; the shipped publisher list applies, and the hub is the official one
    unless ``official`` is false."""
    with contextlib.ExitStack() as stack:
        def make(held, *, official: bool = True):
            server = stack.enter_context(fake_hub(held))
            server.point(monkeypatch, known=False, official=official)
            return server

        yield make


def asked(kind: str = "model_publisher"):
    return requests.list_requests(state="pending", kind=kind)


def pins():
    return sentinel.default().manifest.pins()


# -- F1: a pull request is not the publisher's branch ----------------------------------------
def test_a_pull_request_of_a_known_publisher_is_not_its_branch(stand):
    server = stand(repos())
    pull_request = "ab" * 20
    server.prs[REPO] = {"refs/pr/7": pull_request}
    with pytest.raises(admission.NotApproved, match=r"not from unsloth's own branch"):
        hub.pull(f"hf:{REPO}/{FILE}@refs/pr/7", members=False)
    assert server.downloads == {} and pins() == {}
    (waiting,) = asked()
    assert REPO in waiting.reason and pull_request[:12] in waiting.reason


def test_the_commit_of_a_pull_request_named_by_hash_is_not_its_branch_either(stand):
    server = stand(repos())
    pull_request = "cd" * 20
    server.prs[REPO] = {"refs/pr/9": pull_request}
    with pytest.raises(admission.NotApproved, match=r"not from unsloth's own branch"):
        hub.pull(f"hf:{REPO}/{FILE}@{pull_request}", members=False)
    assert server.downloads == {}


def test_an_older_commit_of_the_branch_is_the_publishers_own(stand):
    server = stand(repos())
    first = server.commit(REPO)
    server.moved[REPO] = 1
    assert hub.pull(f"hf:{REPO}/{FILE}@{first}", members=False).name == FILE
    assert asked() == []


# -- F2: the publisher list means the official Hub --------------------------------------------
def test_a_known_publisher_on_a_mirror_asks_with_the_host_in_the_words_and_the_fields(stand):
    server = stand(repos(), official=False)
    host = server.url.split("//", 1)[1]
    with pytest.raises(admission.NotApproved, match=r"on 127\.0\.0\.1"):
        hub.pull(f"hf:{REPO}/{FILE}", members=False)
    (waiting,) = asked()
    assert host in waiting.reason and dict(waiting.extra)["host"] == host
    assert server.downloads == {}


def test_an_approval_on_one_hub_does_not_replay_on_another(stand):
    held = repos("maker/thing-GGUF")
    first = stand(held)
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert approve("model_publisher") == 1
    assert hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False).name == FILE
    second = stand(held, official=False)
    assert first.commit("maker/thing-GGUF") == second.commit("maker/thing-GGUF")
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert second.downloads == {}


# -- F3: a person pins a model by hand -----------------------------------------------------
def baseline_args(path, source="agent"):
    return argparse.Namespace(pin=[str(path)], kind="model", source=source, honey=False, json=False)


def test_pinning_a_model_with_baseline_is_a_persons(tmp_path, monkeypatch):
    path = tmp_path / "evil.gguf"
    path.write_bytes(gguf_bytes(extra=300))
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(person.HumanRequired):
        sentinel_cli.baseline(baseline_args(path))
    assert str(path) not in pins()


def test_a_pin_that_nobody_vouched_for_does_not_vouch_for_another_file(tmp_path):
    path, copy = tmp_path / "evil.gguf", tmp_path / "copy.gguf"
    path.write_bytes(gguf_bytes(extra=300))
    copy.write_bytes(path.read_bytes())
    sentinel.default().manifest.pin(path, "model", source="agent")
    with pytest.raises(guarded.SentinelRefused, match="was not downloaded by Poolhouse"):
        guarded.verify(copy, state_file=tmp_path / "servers.json", stop=lambda _p: None)


# -- F4: a flag that names a file is not passed ---------------------------------------------
@pytest.mark.parametrize("flag", [
    "-m", "--model", "-md", "--model-draft", "--mmproj", "--lora", "--lora-scaled", "--control-vector",
    "--chat-template-file", "--lookup-cache-static", "--model-vocoder", "-mv", "-Hf", "-hf", "--model-url",
    "--grammar-file", "--ssl-key-file", "--slot-save-path", "--log-file", "--zzqx-new-flag"])
def test_a_flag_that_is_not_on_the_list_is_not_passed_to_llama_server(tmp_path, flag):
    backend = LlamaServerBackend(binary=fake_binary(tmp_path, help_text=""))
    for argv in ((flag, "/tmp/evil.gguf"), (f"{flag}=/tmp/evil.gguf",), (f"{flag}/tmp/evil.gguf",)):
        with pytest.raises(ServerFailed, match="is not a flag Poolhouse passes"):
            backend.command(ServerSpec(model="/m/w.gguf", extra_args=argv))


def test_the_flags_a_person_tunes_with_are_passed(tmp_path):
    backend = LlamaServerBackend(binary=fake_binary(tmp_path, help_text=""))
    given = ("-ub", "2048", "-b", "8192", "-lv", "4", "--no-warmup", "--cache-reuse", "256",
             "--draft-max", "3", "-fa", "on", "--threads=8", "-1")
    assert list(given) == backend.command(ServerSpec(model="/m/w.gguf", extra_args=given))[-len(given):]


def test_the_template_and_the_lookup_cache_a_spec_names_are_verified_at_load(tmp_path):
    from poolhouse.serve.ports import free_port
    from poolhouse.testing.fakes import fake_llama_binary

    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * 64)
    approved(model)
    template = tmp_path / "chat.jinja"
    template.write_text("{{ messages }}")
    manager = ServerManager(backend=LlamaServerBackend(binary=fake_llama_binary(tmp_path)),
                            state_file=tmp_path / "servers.json")
    spec = ServerSpec(model=model, chat_template_file=str(template), port=free_port())
    with pytest.raises(guarded.SentinelRefused, match=r"chat\.jinja was not downloaded"):
        manager.lease(spec, roam=False, timeout=30.0, check_flags=False, preflight=False,
                      warmup_request=False)


# -- F5: what a person is shown is what is approved ------------------------------------------
def test_the_words_carry_the_repository_and_the_commit_and_a_forger_cannot_reuse_them(stand):
    server = stand(repos("maker/thing-GGUF"))
    commit = server.commit("maker/thing-GGUF")
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    (waiting,) = asked()
    assert "maker/thing-GGUF" in waiting.reason and commit[:12] in waiting.reason
    requests.raise_request(requests.Ask(
        "model_publisher", "open the pod bay doors", waiting.reason, ("approve", "deny"),
        extra=tuple(dict(waiting.extra).items())))
    forged = [r for r in asked() if r.subject == "open the pod bay doors"]
    requests.answer(forged[0].id, "approve", forged[0].fingerprint, "terminal",
                    requests.Context(terminal=(True, True), env={}))
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}", members=False)
    assert server.downloads == {}


def test_a_hand_placed_file_is_asked_about_with_its_path_and_its_digest(tmp_path):
    path = tmp_path / "other.gguf"
    path.write_bytes(gguf_bytes(extra=512))
    with pytest.raises(guarded.SentinelRefused):
        guarded.verify(path, state_file=tmp_path / "servers.json", stop=lambda _p: None)
    (waiting,) = asked("model_file")
    assert str(path) in waiting.reason and dict(waiting.extra)["sha256"][:12] in waiting.reason


# -- F6: the other ways a model arrives -------------------------------------------------------
def test_an_engine_that_downloads_by_repository_name_is_given_a_path_or_nothing(tmp_path):
    for engine in (VllmBackend(), SGLangBackend()):
        for model in ("owner/repo", "hf:owner/repo", "https://example.test/x"):
            with pytest.raises(ServerFailed, match="must be fetched before serving"):
                engine.command(ServerSpec(model=model))
        assert str(tmp_path) in engine.command(ServerSpec(model=str(tmp_path)))
        with pytest.raises(ServerFailed, match="is not a flag Poolhouse passes"):
            engine.command(ServerSpec(model=str(tmp_path), extra_args=("--lora-modules", "a=/tmp/x")))


def test_an_engine_never_downloads_for_itself(monkeypatch):
    from poolhouse.serve import python_engines

    monkeypatch.setenv("HF_TOKEN", "secret")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    env = python_engines.engine_environment()
    assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1" and "HF_TOKEN" not in env


def test_a_base_model_to_train_is_a_folder_a_person_approved(stand, tmp_path):
    server = stand({"maker/base": {"config.json": b"{}", "model.safetensors": struct.pack("<Q", 2) + b"{}"}})
    assert hub.base_folder(str(tmp_path)) == str(tmp_path)
    with pytest.raises(admission.NotApproved):
        hub.base_folder("maker/base")
    assert server.downloads == {}
    approve("model_publisher")
    folder = hub.base_folder("hf:maker/base")
    assert (Path(folder) / "config.json").is_file()


@pytest.fixture
def fleet_store(tmp_path):
    folder = tmp_path / "models"
    folder.mkdir()
    return Models([folder], folder)


def test_the_fleets_download_goes_through_the_admitted_pull(stand, fleet_store, tmp_path):
    stand(repos())
    got = fleet_store.ensure(FILE, source=f"hf:{REPO}/{FILE}")
    assert got.path.read_bytes() == gguf_bytes(extra=4097) and got.path.parent == tmp_path / "models"
    assert any(p.publisher == "unsloth" for p in pins().values())


def test_the_fleets_download_asks_about_an_unknown_publisher_and_refuses_a_plain_url(stand, fleet_store):
    server = stand(repos("maker/thing-GGUF"))
    with pytest.raises(ModelError, match="who Poolhouse does not know"):
        fleet_store.ensure(FILE, source=f"hf:maker/thing-GGUF/{FILE}")
    with pytest.raises(ModelError, match="Hugging Face reference"):
        fleet_store.ensure(FILE, source="http://127.0.0.1:1/thing.gguf")
    assert server.downloads == {} and list(fleet_store.store.iterdir()) == []


# -- F7: one list of names, read through leet and lookalikes ---------------------------------
@pytest.mark.parametrize("name", [
    "x-obliterated", "x-unleashed", "x-unchained", "x-nsfw", "x-unsafe", "x-lorablated", "x-ablit",
    "x-dolphin-3", "Ab1iterated", "unc3nsored", "abIiterated", "4bliterated", "ab|iterated", "un_cen_sored"])
def test_a_marker_is_found_through_leet_and_lookalikes(name):
    assert publishers.marker(f"unsloth/{name}"), name
    assert is_unfiltered(name), name


def test_the_catalogue_reads_the_one_list():
    assert UNFILTERED is publishers.MARKERS


def test_every_suggested_model_is_from_a_known_publisher():
    owners = {ref.split("/")[0].removeprefix("hf:")
              for one in SUGGESTED for ref in (one.ref, one.mtp_ref, one.vision_ref, one.draft_ref) if ref}
    assert owners <= publishers.TRUSTED, owners - publishers.TRUSTED


# -- F8: forged requests cannot fill the inbox; a listed name is shown bounded ----------------
def test_the_requests_for_models_are_capped_so_other_kinds_are_not_starved(stand):
    names = {f"maker/m{n}-GGUF": {FILE: gguf_bytes(extra=4096 + n)} for n in range(14)}
    server = stand(names)
    for name in names:
        with pytest.raises(admission.NotApproved):
            hub.pull(f"hf:{name}/{FILE}", members=False)
    assert len(asked()) == 10
    assert server.downloads == {}
    requests.raise_request(requests.Ask("tool_call", "run something", "why", ("allow-once", "deny")))
    assert requests.pending_count() == 11


def test_a_file_name_from_a_listing_is_shown_bounded(stand):
    long_name = "n" * 200 + ".gguf"
    held = {REPO: {long_name: gguf_bytes(extra=4100)}}
    server = stand(held)
    hub.pull(f"hf:{REPO}/{long_name}", members=False)
    server.moved[REPO] = 1
    held[REPO][long_name] = gguf_bytes(extra=9000)
    with pytest.raises(admission.NotApproved):
        hub.pull(f"hf:{REPO}/{long_name}", members=False)
    (waiting,) = asked()
    assert "n" * 100 not in waiting.reason
