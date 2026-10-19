"""A pull asks a person about a publisher nobody listed unless the program names a `Trust` that allows it: the
default of every pull is `PERSON`, and `trust=LIBRARY` or a narrower `Trust` is an explicit choice. Real fake Hub on
a loopback socket, real sealed sentinel store and real request inbox under the test's state root."""

from __future__ import annotations

import contextlib
import dataclasses
import inspect

import keyring
import pytest
from memory_keys import MemoryRing
from model_approval import approve
from net_site import gguf_bytes

import poolhouse as ph
from poolhouse import http, hub, requests, sentinel
from poolhouse.api import models as api_models
from poolhouse.hub import admission, listing, publishers, transfer
from poolhouse.hub.admission import LIBRARY, PERSON, Trust
from poolhouse.serve import models_cli
from poolhouse.serve.backend import LlamaServerBackend, ServerFailed, ServerSpec
from poolhouse.testing.fakehub import fake_hub

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
    for fn in (api_models.pull, listing.fetch, transfer.pull, transfer.snapshot, transfer.base_folder, admission.admit):
        assert inspect.signature(fn).parameters["trust"].default is PERSON, fn.__qualname__
    for fn in (api_models.pull, listing.fetch, transfer.pull, transfer.snapshot, transfer.base_folder):
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
