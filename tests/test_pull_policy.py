"""Every surface that pulls a model says who asked the same way (the process, unless the app names a person), and
nothing in the environment, a reference, a flag or a tool schema turns an agent's pull into a person's. What a pull
let through is served without a question. Real fake Hub on a loopback socket, real sealed sentinel store and real
request inbox under the test's state root."""

from __future__ import annotations

import contextlib
import inspect
import struct
import urllib.parse
from pathlib import Path

import pytest
from net_site import gguf_bytes

import poolhouse as ph
from poolhouse import http, hub, person, requests, sentinel
from poolhouse.api import models as api_models
from poolhouse.hub import admission, hf_cache, listing, publishers, transfer
from poolhouse.hub.admission import Trust
from poolhouse.serve import guarded, models_cli
from poolhouse.serve.backend import LlamaServerBackend, ServerSpec
from poolhouse.serve.leases import recorded_servers
from poolhouse.serve.manager import ServerManager
from poolhouse.serve.ports import free_port
from poolhouse.serve.process import kill_process_tree
from poolhouse.testing.canned import canned_binary
from poolhouse.testing.fakehub import fake_hub

pytest_plugins = ["serve_node"]

FILE = "thing-Q4_K_M.gguf"


def repos(name: str = "maker/thing-GGUF", seed: int = 1, file: str = FILE):
    return {name: {file: gguf_bytes(extra=4096 + seed)}}


@pytest.fixture(autouse=True)
def a_person_started_this(monkeypatch):
    for name in person.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def an_agent(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")


@pytest.fixture
def stand(monkeypatch, tmp_path):
    """``stand(repos, official=True)``: a fake Hub the shipped publisher list applies to."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(http, "check", lambda url: url)
    with contextlib.ExitStack() as stack:
        def make(held, *, official: bool = True):
            server = stack.enter_context(fake_hub(held))
            server.point(monkeypatch, known=False, official=official)
            return server

        yield make


def pins():
    return sentinel.default().manifest.pins()


def ref(repo: str = "maker/thing-GGUF", file: str = FILE) -> str:
    return f"hf:{repo}/{file}"


def load(path, tmp_path):
    """What the Broker asks sentinel before a server starts for ``path``."""
    return guarded.verify(path, state_file=tmp_path / "servers.json", stop=lambda _p: None)


# -- who asked is the process's to say, and only the app's call names a person ------------------
def test_every_pull_surface_takes_who_asked_as_a_keyword_that_defaults_to_the_process():
    surfaces = (api_models.pull, listing.fetch, transfer.pull, transfer.snapshot, transfer.base_folder,
                hf_cache.fetch)
    for fn in surfaces:
        parameter = inspect.signature(fn).parameters["trust"]
        assert parameter.default is None and parameter.kind is inspect.Parameter.KEYWORD_ONLY, fn.__qualname__
    assert inspect.signature(transfer._Run).parameters["trust"].default is None
    assert inspect.signature(admission.admit).parameters["trust"].default is None
    assert ph.models.Trust is Trust and {one.name for one in Trust} == {"PERSON", "AGENT"}
    assert hub.PERSON is Trust.PERSON and hub.AGENT is Trust.AGENT
    assert not hasattr(ph.models, "LIBRARY") and not hasattr(hub, "LIBRARY") and not hasattr(admission, "LIBRARY")


def test_the_process_is_a_person_without_an_agent_marker_and_an_agent_with_one(monkeypatch):
    assert admission.checked(None) is Trust.PERSON
    for marker in person.AGENT_MARKERS:
        monkeypatch.setenv(marker, "1")
        assert admission.checked(None) is Trust.AGENT, marker
        assert admission.checked(Trust.PERSON) is Trust.PERSON
        monkeypatch.delenv(marker)


def test_the_default_pull_from_a_publisher_nobody_listed_fetches_for_a_person_and_nothing_is_asked(stand):
    server = stand(repos())
    got = ph.models.pull("maker/thing-GGUF", FILE)
    assert got.name == FILE and got.read_bytes() == server.repos["maker/thing-GGUF"][FILE]
    assert requests.list_requests() == []
    (pin,) = pins().values()
    assert (pin.publisher, pin.commit, pin.source_ref) == (
        "maker", server.commit("maker/thing-GGUF"), f"maker/thing-GGUF/{FILE}")
    assert pin.note.startswith("This model is from maker, who Poolhouse does not know")


def test_the_default_pull_and_fetch_and_snapshot_and_base_folder_refuse_an_agent_alike(stand, an_agent):
    server = stand(repos())
    with pytest.raises(ph.Error, match="This model is from maker, who Poolhouse does not know"):
        ph.models.pull("maker/thing-GGUF", FILE)
    with pytest.raises(admission.NotAllowed):
        hub.pull(ref())
    with pytest.raises(ph.Error):
        hub.fetch(ref())
    with pytest.raises(admission.NotAllowed):
        hub.snapshot("maker/thing-GGUF")
    with pytest.raises(admission.NotAllowed):
        hub.base_folder("maker/thing-GGUF")
    assert server.downloads == {} and pins() == {} and requests.list_requests() == []


def test_the_command_line_has_no_way_to_say_who_asked(stand, an_agent, capsys):
    server = stand(repos())
    for flag in (["--trust", "library"], ["--trust=library"], ["--library"], ["--no-trust"], ["--trust", "person"]):
        with pytest.raises(SystemExit) as refused:
            models_cli.run(["pull", ref(), *flag])
        assert refused.value.code == 2
    capsys.readouterr()
    for command in ("pull", "fetch"):
        assert models_cli.run([command, ref()]) != 0
        assert "Not fetched for an agent" in capsys.readouterr().err
    assert server.downloads == {}


def test_serving_a_reference_fetches_as_the_process_does_and_a_spec_cannot_carry_a_trust(stand, an_agent):
    import dataclasses

    server = stand({**repos("unsloth/thing-GGUF"), **repos("maker/head-GGUF", file="head-Q8_0.gguf")})
    assert "trust" not in {f.name for f in dataclasses.fields(ServerSpec)}
    with pytest.raises(TypeError):
        ServerSpec(context=4096, model="/m/w.gguf", draft="hf:maker/head-GGUF", trust=Trust.PERSON)  # type: ignore[call-arg]
    from poolhouse.serve.backend import ServerFailed

    with pytest.raises(ServerFailed, match=r"could not fetch the draft.*Not fetched for an agent"):
        LlamaServerBackend.resolved_draft(ServerSpec(context=4096, model="/m/w.gguf", draft="hf:maker/head-GGUF"))
    assert "maker/head-GGUF" not in " ".join(server.downloads)


def test_nothing_in_the_environment_or_a_tool_schema_makes_an_agent_a_person(stand, monkeypatch):
    from poolhouse import mcp

    server = stand(repos())
    for name in ("POOLHOUSE_TRUST", "POOLHOUSE_LIBRARY", "POOLHOUSE_PUBLISHERS", "POOLHOUSE_NO_TRUST",
                 "POOLHOUSE_MODEL_TRUST", "POOLHOUSE_AGENT", "CLAUDECODE"):
        monkeypatch.setenv(name, "library")
    with pytest.raises(admission.NotAllowed):
        hub.pull(ref())
    assert server.downloads == {}
    for tool in mcp.TOOLS:
        assert not {"trust", "policy", "pipeline", "publishers"} & set(tool.schema().get("properties", {})), tool.name


def test_a_reference_that_says_trust_allows_nothing(stand, an_agent):
    server = stand(repos())
    with pytest.raises((hub.NotFound, admission.NotAllowed)):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}?trust=library")
    with pytest.raises((hub.NotFound, admission.NotAllowed)):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}#trust=library")
    assert server.downloads == {} and not hasattr(admission, "from_text") and not hasattr(Trust, "parse")


def test_the_shipped_list_is_read_when_a_pull_is_checked(stand, an_agent, monkeypatch):
    server = stand(repos())
    with pytest.raises(admission.NotAllowed):
        hub.pull(ref())
    monkeypatch.setattr(publishers, "TRUSTED", publishers.TRUSTED | {"maker"})
    assert hub.pull(ref()).name == FILE
    assert server.downloads


# -- a model pulled for a person is served without a question ----------------------------------------
@pytest.mark.usefixtures("serve_leases")
def test_a_model_pulled_from_an_unlisted_publisher_is_served_by_path_without_a_question(stand, tmp_path):
    stand(repos())
    got = ph.models.pull("maker/thing-GGUF", FILE)
    state = tmp_path / "servers.json"
    manager = ServerManager(backend=LlamaServerBackend(binary=canned_binary(tmp_path)), state_file=state)
    try:
        grant = manager.lease(ServerSpec(context=4096, model=got, port=free_port()), roam=False, timeout=30.0,
                              check_flags=False, preflight=False, warmup_request=False)
        assert grant is not None
    finally:
        for entry in recorded_servers(state).values():
            if entry.get("pid"):
                kill_process_tree(entry["pid"])
    assert requests.list_requests() == []
    assert pins()[str(got.resolve())].note.startswith("This model is from maker")


# -- `poolhouse-models snapshot` of a model repository ------------------------------------------------
def snapshot_repos(name: str = "maker/thing", file: str = FILE):
    return {name: {"config.json": b'{"model_type": "llama"}', file: gguf_bytes(extra=4096)}}


def test_the_snapshot_command_fetches_and_pins_the_files_for_a_person(stand, capsys, tmp_path):
    server = stand(snapshot_repos())
    assert models_cli.run(["snapshot", "maker/thing"]) == 0
    folder = tmp_path / "hub" / "models--maker--thing" / "snapshots" / server.commit("maker/thing")
    assert capsys.readouterr().out.strip() == str(folder) and (folder / FILE).is_file()
    (pin,) = pins().values()
    assert (pin.publisher, pin.commit, pin.source_ref, pin.source) == (
        "maker", server.commit("maker/thing"), f"maker/thing/{FILE}", "pull")
    assert pin.host == urllib.parse.urlsplit(server.url).netloc and "maker, who Poolhouse does not know" in pin.note
    assert load(folder / FILE, tmp_path) == "" and requests.list_requests() == []


def test_the_snapshot_command_fetches_a_shipped_publisher_with_nothing_to_note(stand, tmp_path):
    stand(snapshot_repos("unsloth/thing"))
    assert models_cli.run(["snapshot", "unsloth/thing"]) == 0
    (pin,) = pins().values()
    assert (pin.publisher, pin.source_ref, pin.note) == ("unsloth", f"unsloth/thing/{FILE}", "")


@pytest.mark.parametrize("case", [
    ("maker/thing-abliterated", FILE, True, "changed to answer what the original would refuse"),
    ("unsloth/thing", "thing-uncensored-Q4_K_M.gguf", True, "changed to answer what the original would refuse"),
    ("unsloth/thing", FILE, False, "a Hub Poolhouse does not know")])
def test_the_snapshot_command_notes_the_names_and_the_hub_for_a_person_and_refuses_them_to_an_agent(
        stand, monkeypatch, capsys, case):
    repo, file, official, said = case
    server = stand(snapshot_repos(repo, file), official=official)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert models_cli.run(["snapshot", repo]) != 0
    assert said in capsys.readouterr().err and server.downloads == {} and pins() == {}
    monkeypatch.delenv("CLAUDECODE")
    assert models_cli.run(["snapshot", repo]) == 0
    assert any(said in p.note for p in pins().values())


def test_a_snapshot_refuses_bytes_the_hub_lists_for_a_commit_it_listed_other_bytes_for(stand, capsys):
    held = snapshot_repos("unsloth/thing")
    server = stand(held)
    assert models_cli.run(["snapshot", "unsloth/thing"]) == 0
    before = dict(server.downloads)
    capsys.readouterr()
    held["unsloth/thing"][FILE] = gguf_bytes(extra=9000)
    assert models_cli.run(["snapshot", "unsloth/thing"]) != 0
    said = capsys.readouterr().err
    assert "the file you have is unchanged" in said and "quarantined" not in said and server.downloads == before


# -- a folder of safetensors, and the loaders that take one ---------------------------------------------
def folder_repos(name: str = "maker/base"):
    return {name: {"config.json": b"{}", "model.safetensors": struct.pack("<Q", 2) + b"{}"}}


def test_a_base_folder_is_fetched_for_a_person_and_a_folder_of_mine_is_returned_as_it_is(stand, tmp_path):
    server = stand(folder_repos())
    (tmp_path / "mine").mkdir()
    assert hub.base_folder(str(tmp_path / "mine")) == str(tmp_path / "mine")
    folder = hub.base_folder("hf:maker/base")
    assert (tmp_path / "hub" / "models--maker--base" / "snapshots").is_dir() and server.downloads
    assert {p.publisher for p in pins().values()} == {"maker"} and requests.list_requests() == []
    assert Path(folder) == hub.held_snapshot("maker/base")


def test_the_loaders_that_take_a_path_ask_nobody_about_a_pulled_folder(stand):
    from poolhouse.train.recipes import tool_calls_mlx

    stand(folder_repos())
    folder = hub.snapshot("maker/base")
    assert tool_calls_mlx.local_base(str(folder)) == folder.resolve()
    engine = pytest.importorskip("poolhouse.spec.engine")
    assert engine.weights(folder) == folder
    assert requests.list_requests() == []
