"""A draft head the mainline llama.cpp cannot load is replaced when a server starts with it, and serving a model
makes the day's upload check. Real GGUF headers, a local Hub and a Hub cache in ``tmp_path``; the server process is
the canned one, which loads no model."""

from pathlib import Path

import gguf
import numpy as np
import pytest
from test_serve_mtp import after, argv_of, cache, leased, server, stop  # noqa: F401 - fixtures

from poolhouse import hub
from poolhouse.hub import uploads
from poolhouse.serve import binary as builds, drafthead, mtp, profile
from poolhouse.serve.backend import ServerSpec
from poolhouse.testing.fakehub import fake_hub

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")

REPO = "unsloth/thing-GGUF"
MODEL, HEAD = "thing-Q4_K_M.gguf", "MTP/mtp-thing-Q4_K_M.gguf"


def a_qwen4(path: Path, ratios: list[int], *, ahead: int, arch: str = "qwen4exp") -> Path:
    """A real, tiny GGUF with the keys that say whether a draft block is a sparse attention layer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = gguf.GGUFWriter(str(path), arch)
    writer.add_embedding_length(8)
    writer.add_block_count(len(ratios))
    writer.add_array(f"{arch}.attention.compress_ratios", ratios)
    if ahead:
        writer.add_uint32(f"{arch}.nextn_predict_layers", ahead)
    writer.add_tensor("token_embd.weight", np.zeros((8, 4), dtype=np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return path


@pytest.fixture
def shelf(cache, tmp_path, monkeypatch):  # noqa: F811
    """A local Hub whose model and older draft head are in the cache."""
    model = a_qwen4(tmp_path / "made" / "model.gguf", [0, 4], ahead=0).read_bytes()
    old = a_qwen4(tmp_path / "made" / "old.gguf", [0, 4, 0], ahead=1).read_bytes()
    new = a_qwen4(tmp_path / "made" / "new.gguf", [0, 4, 4], ahead=1).read_bytes()
    assert len(old) == len(new), "a re-export that only changes the ratio keeps the size"
    with fake_hub({REPO: {MODEL: model, HEAD: old}}) as there:
        there.point(monkeypatch)
        held = {name: hub.fetch(f"hf:{REPO}/{name}") for name in (MODEL, HEAD)}
        there.hub_seen.clear()
        there.cdn_seen.clear()
        yield there, held, new


def publish(there, body: bytes) -> None:
    there.repos[REPO][HEAD] = body
    there.moved[REPO] = there.moved.get(REPO, 0) + 1


@pytest.mark.parametrize("case", [([0, 4, 0], 1, "qwen4exp", True), ([0, 4, 4], 1, "qwen4exp", False),
                                  ([0, 0, 0], 1, "qwen4exp", False), ([0, 4, 0], 0, "qwen4exp", False),
                                  ([0, 4, 0], 1, "deepseek4", False)])
def test_only_a_draft_block_left_dense_beside_sparse_layers_is_outdated(tmp_path, case):
    ratios, ahead, arch, expected = case
    head = a_qwen4(tmp_path / "mtp-thing.gguf", ratios, ahead=ahead, arch=arch)
    assert bool(drafthead.outdated(head)) is expected


def test_a_server_start_fetches_the_current_head_and_nothing_fetches_it_before(shelf, tmp_path, stop):  # noqa: F811
    there, held, new = shelf
    publish(there, new)
    uploads.check()
    assert there.cdn_seen == [], "noticing an upload downloads nothing"
    program, manager = server(tmp_path)
    info = leased(manager, held[MODEL], draft=str(held[HEAD]), spec_type=mtp.KIND)
    stop(manager, info)
    served = Path(after(argv_of(program), "-md"))
    assert served != held[HEAD] and served.read_bytes() == new
    assert held[HEAD].is_file() and held[HEAD].read_bytes() != new, "a server holding the old copy keeps it"
    there.cdn_seen.clear()
    assert drafthead.loadable(held[HEAD], program) == str(served)
    assert there.cdn_seen == [], "the newer copy held here is not downloaded again"


def test_a_replaced_head_that_loads_is_fetched_when_a_server_starts_with_it(shelf, tmp_path):
    there, held, new = shelf
    publish(there, new)
    first = Path(drafthead.loadable(held[HEAD], None))
    newer = a_qwen4(tmp_path / "made" / "newer.gguf", [0, 4, 8], ahead=1).read_bytes()
    publish(there, newer)
    assert Path(drafthead.loadable(first, None)).read_bytes() == newer
    assert first.read_bytes() == new


def test_a_replacement_that_does_not_load_or_a_check_that_is_off_keeps_the_held_head(shelf, tmp_path):
    there, held, new = shelf
    publish(there, new)
    good = Path(drafthead.loadable(held[HEAD], None))
    publish(there, a_qwen4(tmp_path / "made" / "bad.gguf", [0, 8, 0], ahead=1).read_bytes())
    assert drafthead.loadable(good, None) == str(good)
    uploads.switch(False)
    there.hub_seen.clear()
    assert drafthead.loadable(good, None) == str(good)
    assert there.hub_seen == []


def test_a_hub_that_cannot_be_reached_leaves_the_held_head_served(shelf, monkeypatch):
    _there, held, _new = shelf

    def unreachable(*_a, **_k):
        raise OSError("no network")

    monkeypatch.setattr("poolhouse.hub.remote.listing", unreachable)
    monkeypatch.setattr(hub, "fetch", unreachable)
    assert drafthead.loadable(held[HEAD], None) == str(held[HEAD])


def test_a_fork_build_and_a_loadable_head_are_served_as_asked(shelf, tmp_path):
    there, held, _new = shelf
    fork = builds.managed_named() / "forkname" / "llama-server"
    assert drafthead.loadable(held[HEAD], fork) == str(held[HEAD])
    good = a_qwen4(tmp_path / "mtp-good.gguf", [0, 4, 4], ahead=1)
    assert drafthead.loadable(good, None) == str(good), "a file outside a Hub cache is served as asked"
    assert drafthead.fitting(ServerSpec(model=held[MODEL]), None).draft is None
    assert there.hub_seen == []


def test_a_head_the_profile_names_and_this_machine_lacks_is_the_repositorys(shelf):
    there, held, new = shelf
    there.repos[REPO]["MTP/mtp-thing-Q8_0.gguf"] = new
    there.moved[REPO] = 1
    head, _seeing = profile.resolved(str(held[MODEL]), "mtp-thing-Q8_0.gguf", "")
    assert head == f"hf:{REPO}/MTP/mtp-thing-Q8_0.gguf"
    assert profile.resolved(str(held[MODEL]), "mtp-nowhere.gguf", "")[0] == ""
    assert there.cdn_seen == [], "naming it downloads nothing; starting a server does"


def test_serving_makes_the_days_check_and_says_what_changed(shelf):
    there, held, new = shelf
    publish(there, new)
    said: list[str] = []
    thread = uploads.serving(held[MODEL], held[HEAD], say=said.append)
    assert thread is not None
    thread.join(timeout=20)
    assert said == [f"{REPO}: replaced upstream: {HEAD} (same size, other content)"]
    assert uploads.serving(held[MODEL], say=pytest.fail) is None, "checked already today"
    assert there.cdn_seen == []


def test_serving_another_publishers_file_asks_nothing(tmp_path):
    assert uploads.serving(tmp_path / "model.gguf", "", say=pytest.fail) is None
