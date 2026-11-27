"""The vision projector a model ships is loaded with it by default, from the model's own revision, counted alike in every estimate, and left out rather than
refusing a lease. The server here is the canned binary: plumbing that is NOT a model."""

# ruff: noqa: F811  (the fixtures of the MTP tests are imported by name and asked for by name)
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse import hub
from poolhouse.serve import LlamaServerBackend, ServerManager, ServerSpec, admission, estimate, ops
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


def seeing(cache: Path, repo: str = "owner--seeing") -> tuple[Path, Path]:
    """A model and the projector its revision ships."""
    model = a_gguf(snapshot(cache, repo) / "Seeing-Q4_K_M.gguf")
    projector = snapshot(cache, repo) / "mmproj-F16.gguf"
    projector.write_bytes(gguf(SIZE))
    return model, projector


def recorded(tmp_path: Path, port: int) -> dict:
    return json.loads((tmp_path / "leases.json").read_text())[str(port)]


def test_a_shipped_projector_is_loaded_without_being_asked(cache, tmp_path, stop):
    model, projector = seeing(cache)
    binary, manager = server(tmp_path)
    info = leased(manager, model)
    stop(manager, info)
    assert after(argv_of(binary), "--mmproj") == str(projector)
    assert info.mmproj == projector.name and recorded(tmp_path, info.port)["mmproj"] is True


def test_a_projector_that_does_not_fit_is_left_out_and_the_server_starts(cache, tmp_path, stop, monkeypatch):
    model, _ = seeing(cache)
    binary, manager = server(tmp_path)
    said: list[str] = []
    manager.say = said.append
    alone = admission.estimate_bytes(ServerSpec(model=model, context=512))
    monkeypatch.setattr(hub, "room", lambda: int((alone + SIZE) / THRESHOLDS.red_at) + 1)   # the admission budget holds the model, not the pair
    info = leased(manager, model)
    stop(manager, info)
    assert "--mmproj" not in argv_of(binary) and recorded(tmp_path, info.port)["mmproj"] is False
    assert any("without the vision projector mmproj-F16.gguf" in line for line in said), said


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


def test_every_estimate_counts_the_projector_at_its_loaded_size_and_not_when_declined(cache):
    model, projector = seeing(cache)
    spec = ServerSpec(model=model, context=512)
    loaded = int(projector.stat().st_size * 1.5)
    assert admission.estimate_bytes(replace(spec, mmproj=projector)) - admission.estimate_bytes(spec) == loaded
    assert estimate.estimate(model, context=512).mmproj_bytes == loaded
    assert estimate.estimate(model, context=512, vision=False).mmproj_bytes == 0


def test_a_projector_comes_from_the_models_own_revision_and_none_declines_it(cache, tmp_path):
    model = a_gguf(snapshot(cache) / "Q4" / "Deep-Q4_K_M.gguf")
    elsewhere = snapshot(cache).parent / "rev2" / "mmproj-F16.gguf"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(gguf())
    assert ops.alongside(str(model), "auto", "mmproj-", best=True) == ""
    own = snapshot(cache) / "mmproj-BF16.gguf"
    own.write_bytes(gguf())
    assert ops.alongside(str(model), "auto", "mmproj-", best=True) == str(own)
    _, manager = server(tmp_path)
    declined = ops.resolve_spec(ServerSpec(model=str(model), context=512, mmproj="none"), manager=manager).spec
    assert declined.mmproj is None and declined.vision is False


def test_a_server_up_without_the_projector_says_how_it_differs(cache):
    from poolhouse.serve import projector

    model, _ = seeing(cache)
    spec = ServerSpec(model=str(model), context=512)
    said = projector.without(spec, {"mmproj": False}, adopted=True)
    assert "mmproj-F16.gguf" in said and "already up" in said
    assert projector.without(spec, {"mmproj": True}, adopted=True) == ""
    assert projector.without(replace(spec, vision=False), {"mmproj": False}, adopted=True) == ""


def test_a_pull_brings_the_projector_of_the_same_commit(cache, monkeypatch):
    held = {"Seeing-Q4_K_M.gguf": gguf(41), "mmproj-F16.gguf": gguf(12)}
    with fake_hub({"maker/seeing-GGUF": dict(held)}) as site:
        site.point(monkeypatch)
        got = hub.pull("hf:maker/seeing-GGUF/Seeing-Q4_K_M.gguf")
    assert got.name == "Seeing-Q4_K_M.gguf" and (got.parent / "mmproj-F16.gguf").read_bytes() == held["mmproj-F16.gguf"]
