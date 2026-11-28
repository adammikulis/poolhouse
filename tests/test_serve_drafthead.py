"""A server start takes the newest draft head its llama.cpp loads, falls back to one that works when that one cannot
be had or will not start, and records the head served; serving a model makes the day's upload check. Real GGUF headers,
a local Hub and a Hub cache in ``tmp_path``; the server process is the canned one, which loads no model."""

import json
import stat
import threading
from dataclasses import replace
from http.client import IncompleteRead
from pathlib import Path

import gguf
import numpy as np
import pytest
from test_serve_mtp import MTP_HELP, after, argv_of, cache, leased, stop  # noqa: F401 - fixtures

from poolhouse import harnessing, hub
from poolhouse.hub import uploads
from poolhouse.ownership import lease_file, recorded_servers
from poolhouse.serve import (
    LlamaServerBackend,
    ServerManager,
    ServerSpec,
    binary as builds,
    drafthead,
    free_port,
    mtp,
    profile,
)
from poolhouse.serve.preflight import PreflightFailed
from poolhouse.testing.canned import canned_binary
from poolhouse.testing.fakehub import fake_hub

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")

REPO = "unsloth/thing-GGUF"
MODEL, HEAD = "thing-Q4_K_M.gguf", "MTP/mtp-thing-Q4_K_M.gguf"
SHARED = "MTP/mtp-thing-shared-Q4_K_M.gguf"
REFUSED = "refuse-this-head"


def a_qwen4(path: Path, ratios: list[int], *, ahead: int, arch: str = "qwen4exp", name: str = "") -> Path:
    """A real, tiny GGUF with the keys that say whether a draft block is a sparse attention layer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = gguf.GGUFWriter(str(path), arch)
    if name:
        writer.add_name(name)
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
    for name in ("LLAMA_CPP_SERVER", "LLAMA_CPP_DIR", "POOLHOUSE_LLAMA_BUILD", mtp.ENV):
        monkeypatch.delenv(name, raising=False)
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


def offline(monkeypatch, error: Exception | None = None) -> None:
    def unreachable(*_a, **_k):
        raise error or OSError("no network")

    monkeypatch.setattr("poolhouse.hub.remote.listing", unreachable)
    for name in ("fetch", "pull"):
        monkeypatch.setattr(hub, name, unreachable)


def a_server(where: Path) -> Path:
    """The canned server behind a launcher that exits when a file it is handed says it must be refused, in the
    directory ``where``; answers the canned server, whose directory holds what it was started with."""
    inner = where / "canned"
    inner.mkdir(parents=True)
    real = canned_binary(inner, help_text=MTP_HELP)
    outer = where / "llama-server"
    outer.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = --help ]; then cat {inner / 'help.txt'}; exit 0; fi\n"
        f"for a in \"$@\"; do case \"$a\" in *.gguf) grep -q {REFUSED} \"$a\" && exit 1;; esac; done\n"
        f"exec {real} \"$@\"\n")
    outer.chmod(outer.stat().st_mode | stat.S_IXUSR)
    return real


@pytest.fixture
def machine(shelf):
    """The manager over the managed ``current`` build, as a lease that names no build gets it."""
    real = a_server(builds.managed_current())
    return real, ServerManager(LlamaServerBackend(), state_file=lease_file())


def a_fork() -> Path:
    return a_server(builds.managed_named() / "forkname")


def record(manager: ServerManager, info) -> dict:
    return json.loads(manager.state_file.read_text())[str(info.port)]


def coding(model: Path, **asked) -> ServerSpec:
    """A lease with the window a coding session needs of a server it takes as it stands."""
    return ServerSpec(model=model, port=free_port(), context=harnessing.MIN_WINDOW, **asked)


def up(manager: ServerManager, spec: ServerSpec):
    return manager.lease(spec, timeout=60, preflight=False, warmup_request=False)


@pytest.mark.parametrize("case", [([0, 4, 0], 1, "qwen4exp", True), ([0, 4, 4], 1, "qwen4exp", False),
                                  ([0, 0, 0], 1, "qwen4exp", False), ([0, 4, 0], 0, "qwen4exp", False),
                                  ([0, 4, 0], 1, "deepseek4", False)])
def test_only_a_draft_block_left_dense_beside_sparse_layers_is_outdated(tmp_path, case):
    ratios, ahead, arch, expected = case
    head = a_qwen4(tmp_path / "mtp-thing.gguf", ratios, ahead=ahead, arch=arch)
    assert bool(drafthead.outdated(head)) is expected


def test_a_start_fetches_the_current_head_records_it_and_the_same_lease_finds_it(shelf, machine, tmp_path, stop):  # noqa: F811
    there, held, new = shelf
    program, manager = machine
    publish(there, new)
    uploads.check()
    assert there.cdn_seen == [], "noticing an upload downloads nothing"
    info = leased(manager, held[MODEL], draft=str(held[HEAD]), spec_type=mtp.KIND)
    stop(manager, info)
    served = Path(after(argv_of(program), "-md"))
    assert served != held[HEAD] and served.read_bytes() == new
    assert held[HEAD].is_file() and held[HEAD].read_bytes() != new, "a server holding the old copy keeps it"
    kept = record(manager, info)
    assert kept["draft"] == str(served) and kept["asked"]["draft"] == str(held[HEAD])
    again = leased(manager, held[MODEL], draft=str(held[HEAD]), spec_type=mtp.KIND)
    stop(manager, again)
    assert again.port == info.port, "the same lease takes the server already up"


def test_offline_with_an_old_head_and_a_borrowing_one_the_fork_drafts(shelf, machine, tmp_path, monkeypatch, stop):  # noqa: F811
    _there, held, _new = shelf
    _program, manager = machine
    shared = a_qwen4(held[HEAD].parent / Path(SHARED).name, [0, 4, 0], ahead=1)
    forked = a_fork()
    offline(monkeypatch)
    info = leased(manager, held[MODEL], draft=str(held[HEAD]), spec_type=mtp.KIND)
    stop(manager, info)
    assert after(argv_of(forked), "-md") == str(shared)
    assert record(manager, info)["draft"] == str(shared)
    assert "fork build forkname" in info.mtp_note


@pytest.mark.parametrize("draft", [True, False])
def test_offline_with_no_head_that_loads_the_model_is_served_without_drafting(shelf, machine, monkeypatch,
                                                                              stop, draft):  # noqa: F811
    """``draft`` False is a record whose head this machine lacks and cannot look up: a method and no file."""
    _there, held, _new = shelf
    program, manager = machine
    offline(monkeypatch)
    asked = {"draft": str(held[HEAD])} if draft else {}
    info = leased(manager, held[MODEL], spec_type=mtp.KIND, **asked)
    stop(manager, info)
    argv = argv_of(program)
    assert "-md" not in argv and "--spec-type" not in argv
    assert "WITHOUT DRAFTING" in info.mtp_note
    assert record(manager, info)["draft"] == ""


def test_a_head_that_cannot_be_fetched_by_reference_is_left_out(shelf, machine, monkeypatch, stop):  # noqa: F811
    _there, held, _new = shelf
    program, manager = machine
    held[HEAD].unlink()
    offline(monkeypatch)
    info = leased(manager, held[MODEL], draft=f"hf:{REPO}/{HEAD}", spec_type=mtp.KIND)
    stop(manager, info)
    assert "--spec-type" not in argv_of(program) and "WITHOUT DRAFTING" in info.mtp_note


def test_a_replacement_the_server_will_not_start_with_gives_way_to_the_held_head(shelf, machine, tmp_path, stop):  # noqa: F811
    there, held, new = shelf
    program, manager = machine
    publish(there, new)
    good = hub.fetch(f"hf:{REPO}/{HEAD}")
    publish(there, a_qwen4(tmp_path / "made" / "bad.gguf", [0, 4, 4], ahead=1, name=REFUSED).read_bytes())
    for asked in (good, hub.located(good.name)):
        info = leased(manager, held[MODEL], draft=str(asked), spec_type=mtp.KIND)
        assert Path(after(argv_of(program), "-md")).read_bytes() == new
        assert Path(record(manager, info)["draft"]).read_bytes() == new
        manager.release(info)


def test_the_head_served_is_the_one_verified(shelf, machine, tmp_path, stop):  # noqa: F811
    """A fetched head whose bytes no longer match its pin is not served; the one asked for is."""
    there, held, new = shelf
    program, manager = machine
    publish(there, new)
    good = hub.fetch(f"hf:{REPO}/{HEAD}")
    publish(there, a_qwen4(tmp_path / "made" / "newer.gguf", [0, 4, 8], ahead=1).read_bytes())
    newer = hub.fetch(f"hf:{REPO}/{HEAD}")
    data = bytearray(newer.read_bytes())
    data[-1] ^= 0xFF
    newer.resolve().write_bytes(bytes(data))
    info = leased(manager, held[MODEL], draft=str(good), spec_type=mtp.KIND)
    stop(manager, info)
    assert Path(after(argv_of(program), "-md")).read_bytes() == new


def test_a_listing_cut_short_leaves_the_held_head_served(shelf, tmp_path, monkeypatch):
    there, held, new = shelf
    publish(there, new)
    good = Path(next(drafthead.copies(str(held[HEAD]))))
    offline(monkeypatch, IncompleteRead(b""))
    assert next(drafthead.copies(str(good))) == str(good)


def test_a_check_that_is_off_fetches_nothing_even_for_a_head_that_cannot_load(shelf):
    there, held, new = shelf
    publish(there, new)
    uploads.switch(False)
    assert list(drafthead.copies(str(held[HEAD]))) == []
    assert there.hub_seen == [] and there.cdn_seen == []


def test_a_named_build_of_mainline_is_not_a_fork(tmp_path):
    where = builds.managed_named() / "patched"
    where.mkdir(parents=True)
    (where / "BUILD.json").write_text(json.dumps({"name": "patched", "repo": "ggml-org/llama.cpp"}))
    assert builds.borrows(where / "llama-server") is False
    assert builds.borrows(builds.managed_named() / "forkname" / "llama-server") is True


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


@pytest.mark.parametrize("case", ["replaced", "forked", "undrafted"])
def test_a_warm_server_is_taken_by_the_head_it_runs_and_by_the_head_asked(shelf, machine, monkeypatch, stop, case):  # noqa: F811
    """A coding session asks for exactly what the server up serves (`harnessing.adopted`); the lease that started it
    asks again for what it asked before. Both take it."""
    there, held, new = shelf
    _program, manager = machine
    if case == "replaced":
        publish(there, new)
    else:
        offline(monkeypatch)
    if case == "forked":
        a_qwen4(held[HEAD].parent / Path(SHARED).name, [0, 4, 0], ahead=1)
        a_fork()
    asked = coding(held[MODEL], draft=str(held[HEAD]), spec_type=mtp.KIND)
    first = up(manager, asked)
    stop(manager, first)
    served = record(manager, first)["draft"]
    assert served != str(held[HEAD]) and bool(served) is (case != "undrafted")
    said: list[str] = []
    config = harnessing.adopted(str(held[MODEL]), harnessing.Want(), None, said.append)
    assert config is not None, said
    for spec in (ServerSpec(model=held[MODEL], **config.lease()), replace(asked, port=first.port),
                 replace(asked, port=free_port())):
        again = up(manager, spec)
        stop(manager, again)
        assert (again.port, again.pid) == (first.port, first.pid)


@pytest.mark.parametrize("case", ["served", "asked", "chosen otherwise"])
def test_a_start_waiting_on_a_loading_server_takes_it_when_it_is_up(shelf, machine, stop, case):  # noqa: F811
    there, held, new = shelf
    _program, manager = machine
    publish(there, new)
    asked = coding(held[MODEL], draft=str(held[HEAD]), spec_type=mtp.KIND)
    first = up(manager, asked)
    stop(manager, first)
    running = manager._mine[str(first.port)]
    chosen = {"served": running["draft"], "asked": asked.draft, "chosen otherwise": "/elsewhere/mtp-thing.gguf"}[case]
    manager._mine[str(first.port)] = {**running, "pid": None, "pending": True}
    manager._save()

    def finish() -> None:
        manager._mine[str(first.port)] = running
        manager._save()

    later = threading.Timer(1.0, finish)
    later.start()
    second = ServerManager(LlamaServerBackend(), state_file=lease_file())
    try:
        got = second._admitted(replace(asked, port=free_port(), draft=chosen), reuse=True, load_s=30,
                               asked=replace(asked, port=free_port()))
    finally:
        later.join()
    assert got.port == first.port


def refusing(monkeypatch, refused) -> None:
    started = LlamaServerBackend.start

    def start(self, spec, **kw):
        if refused(spec):
            raise PreflightFailed("preflight refused this start")
        return started(self, spec, **kw)

    monkeypatch.setattr(LlamaServerBackend, "start", start)


def test_a_head_preflight_refuses_gives_way_to_the_next_attempt(shelf, machine, monkeypatch, stop):  # noqa: F811
    _there, held, _new = shelf
    program, manager = machine
    offline(monkeypatch)
    a_qwen4(held[HEAD].parent / Path(SHARED).name, [0, 4, 0], ahead=1)
    a_fork()
    refusing(monkeypatch, lambda spec: bool(spec.draft))
    info = up(manager, coding(held[MODEL], draft=str(held[HEAD]), spec_type=mtp.KIND))
    stop(manager, info)
    assert "--spec-type" not in argv_of(program) and "WITHOUT DRAFTING" in info.mtp_note


def test_a_start_preflight_refuses_every_way_leaves_no_record(shelf, machine, monkeypatch):
    _there, held, _new = shelf
    _program, manager = machine
    offline(monkeypatch)
    refusing(monkeypatch, lambda _spec: True)
    with pytest.raises(Exception, match="preflight refused this start"):
        up(manager, coding(held[MODEL], draft=str(held[HEAD]), spec_type=mtp.KIND))
    assert recorded_servers(lease_file()) == {}
