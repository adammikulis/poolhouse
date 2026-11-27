"""The vision projector a model ships is loaded with it when it fits beside the memory servers are admitted against, from the model's own revision, and
never in a way that refuses a lease, interrupts a server or switches off prompt reuse unasked. The server here is the canned binary: plumbing that is
NOT a model."""

# ruff: noqa: F811  (the fixtures of the MTP tests are imported by name and asked for by name)
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import gguf as gguf_files
import numpy as np
import pytest

from poolhouse import hub
from poolhouse.serve import LlamaServerBackend, ServerManager, ServerSpec, admission, estimate, ops
from poolhouse.serve.preflight import PreflightFailed
from poolhouse.serve.process import pid_exists
from poolhouse.testing.canned import canned_binary
from poolhouse.testing.fakehub import fake_hub
from poolhouse.ui.verdict import THRESHOLDS
from tests.test_hub import gguf
from tests.test_serve_mtp import (  # noqa: F401  (the fixtures and helpers of the MTP tests)
    MTP_HELP,
    a_gguf,
    after,
    argv_of,
    cache,
    leased,
    server,
    snapshot,
    stop,
)

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")
SIZE = 4 << 20
MIB = 1 << 20


def seeing(cache: Path, repo: str = "owner--seeing", size: int = SIZE) -> tuple[Path, Path]:
    """A model and the projector its revision ships."""
    model = a_gguf(snapshot(cache, repo) / "Seeing-Q4_K_M.gguf")
    projector = snapshot(cache, repo) / "mmproj-F16.gguf"
    projector.write_bytes(gguf(size))
    return model, projector


def recorded(tmp_path: Path, port: int) -> dict:
    return json.loads((tmp_path / "leases.json").read_text())[str(port)]


def mib(size: int) -> int:
    return -(-size // MIB)


def test_a_shipped_projector_is_loaded_without_being_asked_and_held_beside_what_is_admitted(cache, tmp_path, stop):
    model, projector = seeing(cache)
    binary, manager = server(tmp_path)
    info = leased(manager, model)
    stop(manager, info)
    entry = recorded(tmp_path, info.port)
    assert after(argv_of(binary), "--mmproj") == str(projector) == info.mmproj == entry["projector"] and entry["mmproj"] is True
    assert entry["est_mb"] == mib(admission.estimate_bytes(ServerSpec(model=model, context=512))), "the next server has the room it had"
    assert entry["spare_mb"] == mib(int(projector.stat().st_size * 1.5))


def test_a_projector_with_no_memory_to_spare_is_left_out_and_the_server_starts(cache, tmp_path, stop, monkeypatch):
    model, _ = seeing(cache, size=128 * MIB)
    binary, manager = server(tmp_path)
    said: list[str] = []
    manager.say = said.append
    alone = admission.estimate_bytes(ServerSpec(model=model, context=512))
    monkeypatch.setattr(hub, "room", lambda: int(alone / THRESHOLDS.red_at) + 8 * MIB)   # the budget holds the model; what is beside it is too small
    info = leased(manager, model)
    stop(manager, info)
    assert "--mmproj" not in argv_of(binary) and recorded(tmp_path, info.port)["mmproj"] is False
    assert any("without the vision projector mmproj-F16.gguf" in line and "--mmproj auto" in line for line in said), said


def test_a_default_projector_counts_against_what_other_default_projectors_left(cache):
    import os

    from poolhouse.serve import projector

    model, _ = seeing(cache)
    spec = ServerSpec(model=str(model), context=512)
    assert projector.chosen(spec).picked
    taken = {9: {"spare_mb": 1 << 40, "pid": os.getpid()}}
    assert not projector.chosen(spec, taken).picked and projector.chosen(spec, {9: {**taken[9], "pid": None}}).picked


def test_a_lease_asking_prompt_reuse_gets_a_projector_only_when_it_asks_and_no_held_server_is_touched(cache, tmp_path, stop):
    model, projector = seeing(cache)
    binary, manager = server(tmp_path)
    said: list[str] = []
    manager.say = said.append
    plain = leased(manager, model, cache_reuse=256)
    stop(manager, plain)
    argv = argv_of(binary)
    assert "--mmproj" not in argv and after(argv, "--cache-reuse") == "256"
    assert any("asks for prompt reuse" in line for line in said), said
    spec = ServerSpec(model=model, port=plain.port, context=512, cache_reuse=256, mmproj="auto")
    asking = manager.lease(spec, timeout=60, preflight=False, warmup_request=False)
    stop(manager, asking)
    assert asking.port != plain.port and pid_exists(plain.pid), "the server already up is left running"
    assert after(argv_of(binary), "--mmproj") == str(projector) and any("context shift are off" in line for line in said), said
    assert recorded(tmp_path, asking.port)["spare_mb"] == 0, "a projector asked for is the server's own memory"


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in that refuses a projector is a shell script")
def test_a_server_that_will_not_start_with_the_default_projector_starts_without_it(cache, tmp_path, stop):
    model, _ = seeing(cache)
    (tmp_path / "inner").mkdir()
    inner = canned_binary(tmp_path / "inner", help_text=MTP_HELP)
    wrapper = tmp_path / "llama-server"
    wrapper.write_text(f"#!/bin/sh\ncase \"$*\" in *--mmproj\\ *) echo 'plumbing: no projector' >&2; echo tried >> {tmp_path}/tried; exit 1;; esac\n"
                       f"exec {inner} \"$@\"\n")
    wrapper.chmod(0o700)
    manager = ServerManager(LlamaServerBackend(binary=wrapper), state_file=tmp_path / "leases.json")
    said: list[str] = []
    manager.say = said.append
    info = leased(manager, model)
    stop(manager, info)
    assert (tmp_path / "tried").is_file() and info.mmproj == "" and recorded(tmp_path, info.port)["mmproj"] is False
    assert any("starting without it" in line for line in said), said


class Refusing(LlamaServerBackend):
    """The canned binary's backend, whose preflight refuses any start with a projector."""

    def start(self, spec, **how):
        if spec.mmproj:
            raise PreflightFailed("plumbing: the preflight refuses a projector")
        return super().start(spec, **how)


def test_a_preflight_that_refuses_the_default_projector_leaves_the_server_starting_without_it(cache, tmp_path, stop):
    model, _ = seeing(cache)
    binary, _unused = server(tmp_path)
    manager = ServerManager(Refusing(binary=binary), state_file=tmp_path / "leases.json")
    info = leased(manager, model)
    stop(manager, info)
    assert info.mmproj == "" and "--mmproj" not in argv_of(binary)


@pytest.mark.parametrize("asked", [None, "auto"])
def test_a_first_start_that_downloads_its_model_chooses_the_projector_as_any_start_does(cache, tmp_path, stop, monkeypatch, asked):
    built = a_gguf(tmp_path / "built" / "Seeing-Q4_K_M.gguf")
    held = {"Seeing-Q4_K_M.gguf": built.read_bytes(), "mmproj-F16.gguf": gguf(SIZE)}
    binary, manager = server(tmp_path)
    with fake_hub({"maker/seeing-GGUF": held}) as site:
        site.point(monkeypatch)
        info = leased(manager, "hf:maker/seeing-GGUF/Seeing-Q4_K_M.gguf", mmproj=asked)
    stop(manager, info)
    entry = recorded(tmp_path, info.port)
    assert entry["mmproj"] is True and (entry["spare_mb"] > 0) is (asked is None) and entry["projector"].endswith("mmproj-F16.gguf")
    assert after(argv_of(binary), "--mmproj") == entry["projector"] == info.mmproj


def test_a_relaunch_keeps_the_projector_a_server_runs_and_never_adds_one(cache):
    from poolhouse.serve import projector

    model, mmproj = seeing(cache)
    asked = ServerSpec(model=str(model), context=512, cache_reuse=256)
    again = projector.kept(asked, {"mmproj": True, "projector": str(mmproj), "spare_mb": 6})
    assert again.mmproj == str(mmproj) and again.cache_reuse is None
    assert projector.counted_as(again, projector.chosen(again), True).mmproj is None, "sized as it was admitted"
    blind = projector.kept(replace(asked, mmproj="auto"), {"mmproj": False})
    assert blind.mmproj is None and not blind.vision


def test_a_server_whose_pictures_differ_from_what_is_asked_is_not_shared(cache):
    model, mmproj = seeing(cache)
    spec = ServerSpec(model=str(model), context=512)
    held = {"cache_type_k": "q8_0", "cache_type_v": "q8_0"}
    running = {**held, "mmproj": True, "projector": str(mmproj)}
    assert admission.differences(replace(spec, mmproj="none"), running, []) == ["mmproj: none was asked for and the server runs a vision projector"]
    assert admission.differences(replace(spec, mmproj="auto"), {**held, "mmproj": False}, [])
    assert admission.differences(spec, running, []) == []
    blind = a_gguf(snapshot(cache, "owner--blind") / "Blind-Q4_K_M.gguf")
    assert admission.differences(replace(spec, model=str(blind), mmproj="auto"), {**held, "mmproj": False}, []) == []


def test_the_wire_names_vision_only_when_it_is_declined():
    from poolhouse.serve.broker_wire import spec_from, spec_to_json

    assert "vision" not in spec_to_json(ServerSpec(model="m")), "an older broker refuses a field it does not know"
    sent = spec_to_json(ServerSpec(model="m", vision=False))
    assert sent["vision"] is False and spec_from(sent).vision is False


def test_every_estimate_agrees_on_whether_a_projector_is_counted(cache):
    model, projector = seeing(cache)
    spec = ServerSpec(model=model, context=512)
    size = projector.stat().st_size
    assert admission.estimate_bytes(replace(spec, mmproj=projector)) - admission.estimate_bytes(spec) == size, "one asked for: its file"
    assert estimate.estimate(model, context=512).mmproj_bytes == int(size * 1.5)
    assert estimate.estimate(model, context=512, vision=False).mmproj_bytes == 0


def windowed(path: Path) -> Path:
    """A tiny GGUF that names its trained window, so a context can be fitted for it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = gguf_files.GGUFWriter(str(path), "llama")
    writer.add_context_length(4096)
    writer.add_embedding_length(8)
    writer.add_block_count(1)
    writer.add_head_count(2)
    writer.add_tensor("token_embd.weight", np.zeros((8, 4), dtype=np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return path


def test_a_context_fitted_for_a_server_without_pictures_leaves_no_room_for_them(cache):
    from poolhouse.serve import projector, suggest

    model = windowed(snapshot(cache) / "Wide-Q4_K_M.gguf")
    (snapshot(cache) / "mmproj-F16.gguf").write_bytes(gguf(SIZE))
    assert suggest.largest_fit(model).estimate.mmproj_bytes > 0 and suggest.largest_text_fit(model).estimate.mmproj_bytes == 0
    spec = ServerSpec(model=str(model), context=0)
    assert projector.counted(spec) and not projector.counted(replace(spec, mmproj="none"))
    assert not projector.counted(replace(spec, cache_reuse=256)) and projector.counted(replace(spec, cache_reuse=256, mmproj="auto"))


def test_a_projector_goes_only_with_the_model_it_belongs_to(cache, tmp_path):
    from poolhouse.serve import projector

    alpha = a_gguf(snapshot(cache) / "Alpha-7B-Q4_K_M.gguf")
    (snapshot(cache) / "mmproj-Beta-VL-7B-F16.gguf").write_bytes(gguf())
    assert projector.shipped(alpha) == "", "named for another model of the revision"
    own = snapshot(cache) / "mmproj-Alpha-7B-F16.gguf"
    own.write_bytes(gguf())
    assert projector.shipped(alpha) == str(own)
    flat = tmp_path / "flat"
    writer, seer = a_gguf(flat / "Writer-7B-Q4_K_M.gguf"), a_gguf(flat / "Seer-VL-7B-Q4_K_M.gguf")
    (flat / "mmproj-F16.gguf").write_bytes(gguf())
    assert ops.alongside(str(writer), "auto", "mmproj-", best=True) == "", "a plain projector beside two models is neither's"
    seer.unlink()
    assert ops.alongside(str(writer), "auto", "mmproj-", best=True) == str(flat / "mmproj-F16.gguf")


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need a privilege there")
def test_a_projector_link_whose_bytes_are_gone_is_not_picked(cache):
    from poolhouse.serve import projector

    model, mmproj = seeing(cache)
    (mmproj.parent / "mmproj-F32.gguf").symlink_to(mmproj.parent / "gone")
    assert projector.shipped(model) == str(mmproj)


def test_a_server_up_without_the_projector_says_how_it_differs(cache):
    from poolhouse.serve import projector

    model, _ = seeing(cache)
    spec = ServerSpec(model=str(model), context=512)
    said = projector.without(spec, {"mmproj": False}, adopted=True)
    assert "mmproj-F16.gguf" in said and "already up" in said and "--mmproj auto" in said
    assert projector.without(spec, {"mmproj": True}, adopted=True) == ""
    assert projector.without(replace(spec, vision=False), {"mmproj": False}, adopted=True) == ""
    assert "prompt reuse" in projector.said(replace(spec, cache_reuse=256), {"mmproj": False}, adopted=False)
    assert "context shift are off" in projector.said(replace(spec, cache_reuse=256, mmproj="auto"), {"mmproj": True, "projector": "/m/mmproj-F16.gguf"},
                                                     adopted=False)


def test_a_pull_brings_the_projector_of_the_same_commit_that_belongs_to_the_model(cache, monkeypatch):
    held = {"Seeing-Q4_K_M.gguf": gguf(41), "mmproj-F16.gguf": gguf(12)}
    other = {"Alpha-7B-Q4_K_M.gguf": gguf(43), "mmproj-Beta-VL-7B-F16.gguf": gguf(14)}
    with fake_hub({"maker/seeing-GGUF": dict(held), "maker/other-GGUF": dict(other)}) as site:
        site.point(monkeypatch)
        got = hub.pull("hf:maker/seeing-GGUF/Seeing-Q4_K_M.gguf")
        alone = hub.pull("hf:maker/other-GGUF/Alpha-7B-Q4_K_M.gguf")
    assert got.name == "Seeing-Q4_K_M.gguf" and (got.parent / "mmproj-F16.gguf").read_bytes() == held["mmproj-F16.gguf"]
    assert not (alone.parent / "mmproj-Beta-VL-7B-F16.gguf").exists()
