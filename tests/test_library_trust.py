"""A pull asks a person about a publisher nobody listed unless the program names a `Trust` that allows it: the
default of every pull is `PERSON`, and `trust=LIBRARY` or a narrower `Trust` is an explicit choice; what a `Trust`
let through is asked about when the file or folder is first served or loaded. Real fake Hub on a loopback socket,
real sealed sentinel store and real request inbox under the test's state root."""

from __future__ import annotations

import contextlib
import dataclasses
import inspect
import urllib.parse

import keyring
import pytest
from memory_keys import MemoryRing
from model_approval import approve
from net_site import gguf_bytes, safetensors_bytes

import poolhouse as ph
from poolhouse import http, hub, requests, sentinel
from poolhouse.api import models as api_models
from poolhouse.hub import admission, hf_cache, listing, publishers, transfer
from poolhouse.hub.admission import LIBRARY, PERSON, Trust
from poolhouse.serve import guarded, models_cli
from poolhouse.serve.backend import LlamaServerBackend, ServerFailed, ServerSpec
from poolhouse.serve.leases import recorded_servers
from poolhouse.serve.manager import ServerManager
from poolhouse.serve.ports import free_port
from poolhouse.serve.process import kill_process_tree
from poolhouse.testing.fakehub import fake_hub
from poolhouse.testing.fakes import fake_llama_binary

FILE = "thing-Q4_K_M.gguf"


def repos(name: str = "maker/thing-GGUF", seed: int = 1, file: str = FILE):
    return {name: {file: gguf_bytes(extra=4096 + seed)}}


class CountingRing(MemoryRing):
    """A keyring that counts every use of it."""

    def __init__(self) -> None:
        super().__init__()
        self.used: list[str] = []

    def get_password(self, service, username):
        self.used.append(f"get {service}/{username}")
        return super().get_password(service, username)

    def set_password(self, service, username, password):
        self.used.append(f"set {service}/{username}")
        super().set_password(service, username, password)


@pytest.fixture
def ring():
    before = keyring.get_keyring()
    counting = CountingRing()
    keyring.set_keyring(counting)
    yield counting
    keyring.set_keyring(before)


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


# -- the default asks: nothing is fetched from a publisher nobody listed ------------------------
def test_every_pull_surface_defaults_to_the_person_trust():
    for fn in (api_models.pull, listing.fetch, transfer.pull, transfer.snapshot, transfer.base_folder, admission.admit,
               hf_cache.fetch):
        assert inspect.signature(fn).parameters["trust"].default is PERSON, fn.__qualname__
    for fn in (api_models.pull, listing.fetch, transfer.pull, transfer.snapshot, transfer.base_folder, hf_cache.fetch):
        assert inspect.signature(fn).parameters["trust"].kind is inspect.Parameter.KEYWORD_ONLY, fn.__qualname__
    assert inspect.signature(transfer._Run).parameters["trust"].default is PERSON
    assert PERSON != LIBRARY and ph.models.Trust is Trust and ph.models.Trust() == LIBRARY


def test_the_default_pull_from_a_publisher_nobody_listed_asks_and_fetches_nothing(stand, ring):
    server = stand(repos())
    with pytest.raises(ph.Error, match="This model is from maker, who Poolhouse does not know") as raised:
        ph.models.pull("maker/thing-GGUF", FILE)
    said = str(raised.value)
    assert 0 < said.index("trust=") < said.index("requests inbox")
    assert server.downloads == {} and pins() == {} and ring.used
    (waiting,) = requests.list_requests(state="pending", kind="model_publisher")
    assert waiting.human_only
    assert approve("model_publisher") == 1
    assert ph.models.pull("maker/thing-GGUF", FILE).name == FILE


def test_the_default_pull_from_a_mirror_asks(stand, ring):
    server = stand(repos(), official=False)
    with pytest.raises(ph.Error, match="a Hub Poolhouse does not know"):
        ph.models.pull("maker/thing-GGUF", FILE)
    assert server.downloads == {} and ring.used
    assert len(requests.list_requests(state="pending", kind="model_publisher")) == 1


@pytest.mark.parametrize("repo,file", [("maker/thing-abliterated-GGUF", FILE),
                                       ("maker/thing-GGUF", "thing-uncensored-Q4_K_M.gguf")])
def test_the_default_pull_of_a_name_that_says_the_model_was_altered_asks(stand, ring, repo, file):
    server = stand(repos(repo, file=file))
    with pytest.raises(ph.Error, match="changed to answer what the original would refuse"):
        ph.models.pull(repo, file)
    assert server.downloads == {} and pins() == {}


def test_the_default_pull_and_fetch_and_snapshot_and_base_folder_ask_alike(stand):
    server = stand(repos())
    with pytest.raises(admission.NotApproved):
        hub.pull(ref(), members=False)
    with pytest.raises(ph.Error):
        hub.fetch(ref(), members=False)
    with pytest.raises(admission.NotApproved):
        hub.snapshot("maker/thing-GGUF")
    with pytest.raises(admission.NotApproved):
        hub.base_folder("maker/thing-GGUF")
    assert server.downloads == {}


def test_a_shipped_publisher_is_pulled_by_default_without_a_question(stand, ring):
    server = stand(repos("unsloth/thing-GGUF"))
    assert ph.models.pull("unsloth/thing-GGUF", FILE).name == FILE
    assert server.downloads and requests.list_requests() == []


# -- a program that says LIBRARY is not asked ---------------------------------------------------
def test_a_library_pull_from_a_publisher_nobody_listed_needs_no_person(stand, ring):
    server = stand(repos())
    got = ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    assert got.name == FILE and got.read_bytes() == server.repos["maker/thing-GGUF"][FILE]
    assert ring.used == [] and requests.list_requests() == []
    (pin,) = pins().values()
    assert (pin.publisher, pin.commit, pin.source_ref) == (
        "maker", server.commit("maker/thing-GGUF"), f"maker/thing-GGUF/{FILE}")


def test_a_library_pull_from_a_mirror_needs_no_person(stand, ring):
    server = stand(repos(), official=False)
    assert ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY).name == FILE
    assert server.downloads and ring.used == [] and requests.list_requests() == []


@pytest.mark.parametrize("repo,file", [("maker/thing-abliterated-GGUF", FILE),
                                       ("maker/thing-GGUF", "thing-uncensored-Q4_K_M.gguf")])
def test_a_library_pull_of_a_name_that_says_the_model_was_altered_needs_no_person(stand, ring, repo, file):
    stand(repos(repo, file=file))
    assert ph.models.pull(repo, file, trust=LIBRARY).name == file
    assert ring.used == [] and requests.list_requests() == []


def test_a_library_pull_keeps_the_check_that_the_hub_lists_the_same_bytes_for_a_commit(stand):
    held = repos()
    server = stand(held)
    ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    before = dict(server.downloads)
    held["maker/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    with pytest.raises(ph.Error, match="quarantined by sentinel"):
        ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    assert server.downloads == before and len(pins()) == 1
    commit = server.commit("maker/thing-GGUF")
    assert sentinel.default().store.state_of("model", f"hub:maker/thing-GGUF/{FILE}@{commit}") \
        == sentinel.State.QUARANTINED


def test_a_library_pull_of_other_bytes_under_a_new_commit_records_the_new_pin(stand, ring):
    held = repos()
    server = stand(held)
    ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    server.moved["maker/thing-GGUF"] = 1
    held["maker/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    assert ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY).name == FILE
    assert {p.commit for p in pins().values()} == {server.commit("maker/thing-GGUF", 0),
                                                  server.commit("maker/thing-GGUF", 1)}
    assert ring.used == [] and requests.list_requests() == []


# -- the program narrows it with the argument --------------------------------------------------
def test_a_trust_naming_publishers_refuses_the_others_and_names_the_argument(stand, ring):
    server = stand({**repos("acme/thing-GGUF"), **repos("maker/thing-GGUF")})
    only_acme = Trust(publishers=frozenset({"acme"}))
    assert ph.models.pull("acme/thing-GGUF", FILE, trust=only_acme).name == FILE
    with pytest.raises(ph.Error, match=r"maker.*trust=") as raised:
        ph.models.pull("maker/thing-GGUF", FILE, trust=only_acme)
    assert "requests inbox" not in str(raised.value) and "poolhouse-" not in str(raised.value)
    assert "maker/thing-GGUF" not in " ".join(server.downloads)
    assert ring.used == [] and requests.list_requests() == []


def test_a_trust_can_ask_about_the_names_and_the_hub_the_person_surfaces_ask_about(stand, ring):
    stand(repos("maker/thing-abliterated-GGUF"))
    with pytest.raises(ph.Error, match=r"changed to answer what the original would refuse.*trust="):
        ph.models.pull("maker/thing-abliterated-GGUF", FILE, trust=Trust(markers=True))
    assert ring.used == [] and requests.list_requests() == []


def test_a_trust_is_a_trust_and_not_a_word(stand):
    stand(repos())
    for word in ("library", "allow", True, 1, {"publishers": None}, None):
        with pytest.raises(TypeError, match="Trust"):
            ph.models.pull("maker/thing-GGUF", FILE, trust=word)
        with pytest.raises(TypeError, match="Trust"):
            hub.pull(ref(), members=False, trust=word)
    assert dataclasses.is_dataclass(Trust) and Trust.__dataclass_params__.frozen


def test_the_publishers_of_a_trust_are_a_collection_of_names_and_never_one_string():
    assert Trust(publishers=["acme", "acme"]).publishers == frozenset({"acme"})
    with pytest.raises(TypeError, match="collection"):
        Trust(publishers="acme")  # type: ignore[arg-type]


# -- the app and agent surfaces keep asking ----------------------------------------------------
def test_the_person_trust_asks_about_the_same_pull_and_names_the_argument_before_the_inbox(stand, ring):
    server = stand(repos())
    with pytest.raises(admission.NotApproved) as raised:
        hub.pull(ref(), members=False, trust=PERSON)
    said = str(raised.value)
    assert said.startswith("This model is from maker, who Poolhouse does not know. It is maker/thing-GGUF at ")
    assert 0 < said.index("trust=") < said.index("requests inbox")
    assert server.downloads == {} and pins() == {} and ring.used
    (waiting,) = requests.list_requests(state="pending", kind="model_publisher")
    assert waiting.human_only
    assert approve("model_publisher") == 1
    assert hub.pull(ref(), members=False, trust=PERSON).name == FILE


def test_the_command_line_asks_and_has_no_way_to_say_otherwise(stand, capsys):
    server = stand(repos())
    for flag in (["--trust", "library"], ["--trust=library"], ["--library"], ["--no-trust"]):
        with pytest.raises(SystemExit) as refused:
            models_cli.run(["pull", ref(), *flag])
        assert refused.value.code == 2
    capsys.readouterr()
    for command in ("pull", "fetch"):
        assert models_cli.run([command, ref(), "--no-members"]) != 0
        said = capsys.readouterr().err
        assert "This model is from maker" in said and 0 < said.index("trust=") < said.index("requests inbox")
    assert server.downloads == {}
    assert len(requests.list_requests(state="pending", kind="model_publisher")) == 1


def test_serving_a_reference_asks_and_a_spec_cannot_carry_a_trust(stand, tmp_path):
    server = stand({**repos("unsloth/thing-GGUF"), **repos("maker/head-GGUF", file="head-Q8_0.gguf")})
    assert "trust" not in {f.name for f in dataclasses.fields(ServerSpec)}
    with pytest.raises(TypeError):
        ServerSpec(model="/m/w.gguf", draft="hf:maker/head-GGUF", trust=LIBRARY)  # type: ignore[call-arg]
    with pytest.raises(ServerFailed, match=r"could not fetch the draft.*This model is from maker"):
        LlamaServerBackend.resolved_draft(ServerSpec(model="/m/w.gguf", draft="hf:maker/head-GGUF"))
    assert "maker/head-GGUF" not in " ".join(server.downloads)


def test_nothing_in_the_environment_or_a_tool_schema_widens_the_policy(stand, monkeypatch):
    from poolhouse import mcp

    server = stand(repos())
    for name in ("POOLHOUSE_TRUST", "POOLHOUSE_LIBRARY", "POOLHOUSE_PUBLISHERS", "POOLHOUSE_NO_TRUST",
                 "POOLHOUSE_MODEL_TRUST", "POOLHOUSE_AGENT", "CLAUDECODE"):
        monkeypatch.setenv(name, "library")
    with pytest.raises(admission.NotApproved):
        hub.pull(ref(), members=False, trust=PERSON)
    with pytest.raises(admission.NotApproved):
        hub.pull(ref(), members=False)
    assert server.downloads == {}
    for tool in mcp.TOOLS:
        assert not {"trust", "policy", "pipeline", "publishers"} & set(tool.schema().get("properties", {})), tool.name


def test_a_reference_that_says_trust_allows_nothing(stand):
    server = stand(repos())
    with pytest.raises((hub.NotFound, admission.NotApproved)):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}?trust=library", members=False, trust=PERSON)
    with pytest.raises((hub.NotFound, admission.NotApproved)):
        hub.pull(f"hf:maker/thing-GGUF/{FILE}#trust=library", members=False)
    assert server.downloads == {} and not hasattr(admission, "from_text") and not hasattr(Trust, "parse")


def test_the_shipped_list_is_read_when_a_pull_is_checked_so_a_publisher_added_to_it_is_not_asked_about(stand, monkeypatch):
    server = stand(repos())
    with pytest.raises(admission.NotApproved):
        hub.pull(ref(), members=False, trust=PERSON)
    monkeypatch.setattr(publishers, "TRUSTED", publishers.TRUSTED | {"maker"})
    assert hub.pull(ref(), members=False, trust=PERSON).name == FILE
    assert server.downloads


# -- a model pulled without a question is asked about once, when it is served ---------------------
def load(path, tmp_path):
    """What the Broker asks sentinel before a server starts for ``path``."""
    return guarded.verify(path, state_file=tmp_path / "servers.json", stop=lambda _p: None)


def pinned(path):
    return sentinel.default().manifest.pin_of(path)


def test_a_model_pulled_under_library_from_an_unlisted_publisher_is_asked_about_when_it_is_served(stand, tmp_path):
    server = stand(repos())
    got = ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    assert requests.list_requests() == []
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker, who Poolhouse does not know") as raised:
        load(got, tmp_path)
    assert "requests inbox" in str(raised.value)
    (waiting,) = requests.list_requests(state="pending", kind="model_publisher")
    assert waiting.human_only and f"maker/thing-GGUF at {server.commit('maker/thing-GGUF')[:12]}" in waiting.reason
    assert approve("model_publisher") == 1
    assert load(got, tmp_path) == "" and pinned(got).unvetted == ""


def test_the_question_at_serve_time_is_asked_once_and_not_again(stand, tmp_path, monkeypatch):
    stand(repos())
    got = ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    with pytest.raises(guarded.SentinelRefused):
        load(got, tmp_path)
    approve("model_publisher")
    load(got, tmp_path)

    def never(*_args, **_fields):
        raise AssertionError("a person was asked again")

    monkeypatch.setattr(guarded.approvals, "approved", never)
    monkeypatch.setattr(guarded.approvals, "ask", never)
    assert load(got, tmp_path) == "" and load(got, tmp_path) == ""


def test_an_approval_at_serve_time_answers_a_later_person_pull_of_the_same_repository_and_commit(stand, tmp_path):
    server = stand(repos())
    got = ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    with pytest.raises(guarded.SentinelRefused):
        load(got, tmp_path)
    assert approve("model_publisher") == 1
    load(got, tmp_path)
    (approval,) = requests.list_requests(state="approved", kind="model_publisher")
    assert approval.extra["host"] == urllib.parse.urlsplit(server.url).netloc
    assert ph.models.pull("maker/thing-GGUF", FILE) == got
    assert requests.list_requests(state="pending") == []


def test_a_model_from_a_shipped_publisher_pulled_under_library_is_served_without_a_question(stand, tmp_path):
    stand(repos("unsloth/thing-GGUF"))
    got = ph.models.pull("unsloth/thing-GGUF", FILE, trust=LIBRARY)
    assert pinned(got).unvetted == "" and load(got, tmp_path) == "" and requests.list_requests() == []


def test_a_model_the_person_surface_pulled_after_an_approval_is_not_asked_about_again(stand, tmp_path):
    stand(repos())
    with pytest.raises(ph.Error, match="This model is from maker"):
        ph.models.pull("maker/thing-GGUF", FILE)
    approve("model_publisher")
    got = ph.models.pull("maker/thing-GGUF", FILE)
    assert pinned(got).unvetted == "" and load(got, tmp_path) == ""
    assert requests.list_requests(state="pending") == []


@pytest.mark.parametrize("case", [
    ("maker/thing-GGUF", FILE, False, "a Hub Poolhouse does not know"),
    ("maker/thing-abliterated-GGUF", FILE, True, "changed to answer what the original would refuse"),
    ("unsloth/thing-GGUF", "thing-uncensored-Q4_K_M.gguf", True, "changed to answer what the original would refuse")])
def test_each_question_the_person_surface_would_have_asked_is_asked_at_serve_time(stand, tmp_path, case):
    repo, file, official, said = case
    stand(repos(repo, file=file), official=official)
    got = ph.models.pull(repo, file, trust=LIBRARY)
    with pytest.raises(guarded.SentinelRefused, match=said):
        load(got, tmp_path)


def test_a_file_that_moved_under_a_new_commit_is_asked_about_at_serve_time_when_pulled_under_library(stand, tmp_path):
    held = repos("unsloth/thing-GGUF")
    server = stand(held)
    first = ph.models.pull("unsloth/thing-GGUF", FILE, trust=LIBRARY)
    server.moved["unsloth/thing-GGUF"] = 1
    held["unsloth/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    second = ph.models.pull("unsloth/thing-GGUF", FILE, trust=LIBRARY)
    assert first.resolve() != second.resolve()
    assert load(first, tmp_path) == ""
    with pytest.raises(guarded.SentinelRefused, match="is not the file Poolhouse pulled from unsloth/thing-GGUF before"):
        load(second, tmp_path)


def test_a_file_that_moved_is_asked_about_when_the_pull_asks_a_person(stand):
    held = repos("unsloth/thing-GGUF")
    server = stand(held)
    ph.models.pull("unsloth/thing-GGUF", FILE)
    server.moved["unsloth/thing-GGUF"] = 1
    held["unsloth/thing-GGUF"][FILE] = gguf_bytes(extra=9000)
    with pytest.raises(ph.Error, match="is not the file Poolhouse pulled from unsloth/thing-GGUF before"):
        ph.models.pull("unsloth/thing-GGUF", FILE)
    with pytest.raises(ph.Error, match=r"differs from the file pulled before.*trust="):
        ph.models.pull("unsloth/thing-GGUF", FILE, trust=Trust(moved=True))


def test_a_trust_that_names_the_publishers_leaves_the_question_for_the_serve(stand, tmp_path):
    stand(repos("acme/thing-GGUF"))
    got = ph.models.pull("acme/thing-GGUF", FILE, trust=Trust(publishers=frozenset({"acme"})))
    with pytest.raises(guarded.SentinelRefused, match="This model is from acme, who Poolhouse does not know"):
        load(got, tmp_path)


def test_a_copy_of_a_model_pulled_without_a_question_is_not_vouched_for_by_the_pull(stand, tmp_path):
    stand(repos())
    got = ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    copy = tmp_path / "copy.gguf"
    copy.write_bytes(got.read_bytes())
    with pytest.raises(guarded.SentinelRefused, match="was not downloaded by Poolhouse"):
        load(copy, tmp_path)
    assert pinned(copy) is None


def test_a_copy_of_a_model_is_vouched_for_by_a_pull_that_nobody_needed_to_ask_about(stand, tmp_path):
    stand({**repos(), **repos("unsloth/thing-GGUF")})
    ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    kept = ph.models.pull("unsloth/thing-GGUF", FILE, trust=LIBRARY)
    copy = tmp_path / "copy.gguf"
    copy.write_bytes(kept.read_bytes())
    assert [p.publisher for p in sorted(sentinel.default().manifest.pins().values(), key=lambda p: p.path)] == ["maker", "unsloth"]
    assert load(copy, tmp_path) == "" and pinned(copy).source == "pinned-digest"


def test_serving_a_model_pulled_under_library_by_path_starts_no_server_until_a_person_answers(stand, tmp_path):
    stand(repos())
    got = ph.models.pull("maker/thing-GGUF", FILE, trust=LIBRARY)
    state = tmp_path / "servers.json"
    manager = ServerManager(backend=LlamaServerBackend(binary=fake_llama_binary(tmp_path)), state_file=state)
    try:
        with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
            manager.lease(ServerSpec(model=got, port=free_port()), roam=False, timeout=30.0, check_flags=False,
                          preflight=False, warmup_request=False)
    finally:
        for entry in recorded_servers(state).values():
            if entry.get("pid"):
                kill_process_tree(entry["pid"])
    assert recorded_servers(state) == {}


# -- a folder of safetensors files, and the in-process loaders that take a path ------------------------
SHARD = "model-00001-of-00002.safetensors"


def folder_repos(name: str = "maker/thing", seed: int = 1):
    return {name: {"config.json": b'{"model_type": "llama"}',
                   SHARD: safetensors_bytes(data=16 + seed),
                   "model-00002-of-00002.safetensors": safetensors_bytes(data=32 + seed)}}


def pull_folder(repo: str = "maker/thing", trust=LIBRARY, dest=None):
    return hub.pull(f"hf:{repo}/{SHARD}", dest, members=False, trust=trust)


def waiting_pins():
    return [p for p in pins().values() if p.unvetted]


# -- serving a folder ----------------------------------------------------------------------------
def test_a_safetensors_folder_pulled_under_library_is_asked_about_once_when_it_is_served(stand, tmp_path):
    stand(folder_repos())
    folder = pull_folder()
    assert folder.is_dir() and len(waiting_pins()) == 2 and requests.list_requests() == []
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker, who Poolhouse does not know") as raised:
        load(folder, tmp_path)
    assert "requests inbox" in str(raised.value)
    (waiting,) = requests.list_requests(state="pending", kind="model_publisher")
    assert waiting.human_only
    assert approve("model_publisher") == 1
    assert load(folder, tmp_path) == "" and waiting_pins() == []
    assert requests.list_requests(state="pending") == []


def test_the_question_about_a_folder_is_not_asked_again_once_a_person_answered(stand, tmp_path, monkeypatch):
    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(guarded.SentinelRefused):
        load(folder, tmp_path)
    approve("model_publisher")
    load(folder, tmp_path)

    def never(*_args, **_fields):
        raise AssertionError("a person was asked again")

    monkeypatch.setattr(guarded.approvals, "approved", never)
    monkeypatch.setattr(guarded.approvals, "ask", never)
    monkeypatch.setattr(admission, "approved", never)
    monkeypatch.setattr(admission, "ask", never)
    assert load(folder, tmp_path) == "" and load(folder, tmp_path) == ""


def test_a_folder_pulled_into_a_plain_directory_under_library_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    folder = pull_folder(dest=tmp_path / "plain")
    assert folder == tmp_path / "plain" and len(waiting_pins()) == 2
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(folder, tmp_path)


def test_a_link_to_a_folder_pulled_under_library_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    link = tmp_path / "linked"
    link.symlink_to(pull_folder(), target_is_directory=True)
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(link, tmp_path)


def test_a_folder_from_a_shipped_publisher_pulled_under_library_is_served_without_a_question(stand, tmp_path):
    stand(folder_repos("unsloth/thing"))
    folder = pull_folder("unsloth/thing")
    assert waiting_pins() == [] and load(folder, tmp_path) == "" and requests.list_requests() == []


def test_a_folder_the_person_surface_pulled_after_an_approval_is_not_asked_about_again(stand, tmp_path):
    stand(folder_repos())
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        pull_folder(trust=PERSON)
    approve("model_publisher")
    folder = pull_folder(trust=PERSON)
    assert waiting_pins() == [] and load(folder, tmp_path) == ""
    assert requests.list_requests(state="pending") == []


def test_an_approval_at_the_first_serve_of_a_folder_answers_a_later_person_pull(stand, tmp_path):
    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(guarded.SentinelRefused):
        load(folder, tmp_path)
    approve("model_publisher")
    load(folder, tmp_path)
    assert pull_folder(trust=PERSON) == folder and requests.list_requests(state="pending") == []


def test_a_folder_of_shards_of_two_commits_asks_about_each_repository_and_commit(stand, tmp_path):
    held = folder_repos()
    server = stand(held)
    first = pull_folder()
    server.moved["maker/thing"] = 1
    held["maker/thing"][SHARD] = safetensors_bytes(data=99)
    second = pull_folder()
    assert first != second
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(first, tmp_path)
    approve("model_publisher")
    assert load(first, tmp_path) == ""
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(second, tmp_path)


def test_a_directory_that_holds_no_pulled_file_is_not_asked_about(tmp_path):
    (tmp_path / "mlx").mkdir()
    (tmp_path / "mlx" / "weights.safetensors").write_bytes(safetensors_bytes())
    assert load(tmp_path / "mlx", tmp_path) == "" and requests.list_requests() == []


# -- the in-process loaders that take a path ----------------------------------------------------------
def test_a_base_folder_pulled_under_library_is_asked_about_before_a_trainer_gets_it(stand):
    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(admission.NotApproved, match="This model is from maker, who Poolhouse does not know"):
        hub.base_folder(str(folder), trust=PERSON)
    assert len(requests.list_requests(state="pending", kind="model_publisher")) == 1
    approve("model_publisher")
    assert hub.base_folder(str(folder), trust=PERSON) == str(folder) and waiting_pins() == []


def test_a_base_folder_that_is_not_a_pulled_one_is_returned_without_a_question(tmp_path):
    (tmp_path / "mine").mkdir()
    assert hub.base_folder(str(tmp_path / "mine"), trust=PERSON) == str(tmp_path / "mine")
    assert requests.list_requests() == []


def test_the_weights_a_speculative_decoder_loads_are_asked_about(stand):
    engine = pytest.importorskip("poolhouse.spec.engine")

    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        engine.weights(folder)
    approve("model_publisher")
    assert engine.weights(folder) == folder


def test_the_base_the_mlx_trainer_loads_is_asked_about(stand):
    from poolhouse.train.recipes import tool_calls_mlx

    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        tool_calls_mlx.local_base(str(folder))
    approve("model_publisher")
    assert tool_calls_mlx.local_base(str(folder)) == folder.resolve()


def test_the_base_a_decider_loads_is_asked_about_before_anything_is_read(stand):
    from poolhouse.decide import pointer

    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        pointer.load_torso(folder, None, "float32", "cpu")


def test_a_file_pulled_under_library_is_asked_about_by_the_shared_check(stand, tmp_path):
    stand({"maker/thing-GGUF": {"thing-Q4_K_M.gguf": gguf_bytes(extra=4096)}})
    got = hub.pull("hf:maker/thing-GGUF/thing-Q4_K_M.gguf", members=False, trust=LIBRARY)
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        admission.vetted(got)
    approve("model_publisher")
    admission.vetted(got)
    assert pinned(got).unvetted == ""


# -- `poolhouse-models snapshot` of a model repository is asked about like a pull --------------------
def snapshot_repos(name: str = "maker/thing", file: str = FILE):
    return {name: {"config.json": b'{"model_type": "llama"}', file: gguf_bytes(extra=4096)}}


def cached(server, repo: str = "maker/thing") -> bool:
    return any(key.startswith(f"{repo}/") for key in server.downloads)


def test_the_snapshot_command_asks_about_a_publisher_nobody_listed_and_fetches_nothing(stand, capsys, tmp_path):
    server = stand(snapshot_repos())
    assert models_cli.run(["snapshot", "maker/thing"]) != 0
    said = capsys.readouterr().err
    assert "This model is from maker, who Poolhouse does not know" in said and 0 < said.index("trust=") < said.index("requests inbox")
    assert server.downloads == {} and pins() == {} and not (tmp_path / "hub").exists()
    (waiting,) = requests.list_requests(state="pending", kind="model_publisher")
    assert waiting.human_only and f"maker/thing at {server.commit('maker/thing')[:12]}" in waiting.reason


def test_the_snapshot_command_fetches_and_pins_the_files_once_a_person_approved(stand, capsys, tmp_path):
    server = stand(snapshot_repos())
    assert models_cli.run(["snapshot", "maker/thing"]) != 0
    approve("model_publisher")
    assert models_cli.run(["snapshot", "maker/thing"]) == 0
    folder = tmp_path / "hub" / "models--maker--thing" / "snapshots" / server.commit("maker/thing")
    assert capsys.readouterr().out.strip() == str(folder) and (folder / FILE).is_file()
    (pin,) = pins().values()
    assert (pin.publisher, pin.commit, pin.source_ref, pin.source, pin.unvetted) == (
        "maker", server.commit("maker/thing"), f"maker/thing/{FILE}", "pull", "")
    assert pin.host == urllib.parse.urlsplit(server.url).netloc
    assert load(folder / FILE, tmp_path) == "" and requests.list_requests(state="pending") == []


def test_the_snapshot_command_fetches_a_shipped_publisher_without_a_question(stand, tmp_path):
    server = stand(snapshot_repos("unsloth/thing"))
    assert models_cli.run(["snapshot", "unsloth/thing"]) == 0
    assert cached(server, "unsloth/thing") and requests.list_requests() == []
    (pin,) = pins().values()
    assert (pin.publisher, pin.source_ref, pin.unvetted) == ("unsloth", f"unsloth/thing/{FILE}", "")
    assert load(tmp_path / "hub" / "models--unsloth--thing" / "snapshots" / pin.commit / FILE, tmp_path) == ""


@pytest.mark.parametrize("case", [
    ("maker/thing-abliterated", FILE, True, "changed to answer what the original would refuse"),
    ("unsloth/thing", "thing-uncensored-Q4_K_M.gguf", True, "changed to answer what the original would refuse"),
    ("unsloth/thing", FILE, False, "a Hub Poolhouse does not know")])
def test_the_snapshot_command_asks_about_the_names_and_the_hub_a_pull_asks_about(stand, capsys, case):
    repo, file, official, said = case
    server = stand(snapshot_repos(repo, file), official=official)
    assert models_cli.run(["snapshot", repo]) != 0
    assert said in capsys.readouterr().err and not cached(server, repo) and pins() == {}


def test_a_snapshot_pulled_under_library_is_asked_about_when_it_is_served(stand, tmp_path):
    server = stand(snapshot_repos())
    folder = hf_cache.fetch("maker/thing", trust=LIBRARY)
    assert cached(server) and requests.list_requests() == []
    (pin,) = pins().values()
    assert pin.unvetted and "maker, who Poolhouse does not know" in pin.unvetted
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(folder / FILE, tmp_path)


def test_a_snapshot_refuses_bytes_the_hub_lists_for_a_commit_it_listed_other_bytes_for(stand, capsys):
    held = snapshot_repos("unsloth/thing")
    server = stand(held)
    assert models_cli.run(["snapshot", "unsloth/thing"]) == 0
    before = dict(server.downloads)
    capsys.readouterr()
    held["unsloth/thing"][FILE] = gguf_bytes(extra=9000)
    assert models_cli.run(["snapshot", "unsloth/thing"]) != 0
    assert "quarantined by sentinel" in capsys.readouterr().err and server.downloads == before
