"""A private model server: what its command line and environment may not hold, and that no other lease is given it."""

from __future__ import annotations

import os

import pytest

from poolhouse.serve import admission, backend as be, private
from poolhouse.serve.broker import Held
from poolhouse.serve.grant import broker_grant
from poolhouse.serve.policy import LocalPolicy


def test_a_private_server_is_started_with_nothing_that_keeps_content(monkeypatch, tmp_path):
    weights = tmp_path / "m.gguf"
    weights.write_bytes(b"GGUF")
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(be, "claim_port", lambda spec, lease: None)
    monkeypatch.setenv("LLAMA_ARG_LOG_FILE", str(tmp_path / "leak.log"))
    monkeypatch.setenv("LLAMA_ARG_SLOT_SAVE_PATH", str(tmp_path))
    seen = {}

    def launch(argv, lease, **how):
        seen.update(argv=list(argv), **how)
        raise be.ServerFailed("stop here")

    monkeypatch.setattr(be, "launch", launch)
    spec = be.ServerSpec(model=weights, port=8123, private="guest-1", slot_save_path=str(tmp_path / "slots"),
                         spec_type="ngram-mod", lookup_dynamic=str(tmp_path / "lookup.bin"), extra_args=("-v",))
    with broker_grant(), pytest.raises(be.ServerFailed):
        be.LlamaServerBackend(binary=binary, sandboxed=False, policy=LocalPolicy()).start(
            spec, lease=be.Lease(port=8123, owner_pid=os.getpid(), state_file="x"), check_flags=False, preflight=False)
    argv = seen["argv"]
    assert set(private.FLAGS) <= set(argv)
    assert not {"--slot-save-path", "--spec-type", "--lookup-cache-dynamic", "-v", "--metrics"} & set(argv)
    assert str(seen["log_path"]) == os.devnull
    assert not [name for name in seen["env"] if name.startswith("LLAMA_")]
    assert not (tmp_path / "slots").exists()


def test_a_private_server_is_never_shared_in_either_direction():
    mine = be.ServerSpec(model="m.gguf", private="guest-1")
    ordinary = be.ServerSpec(model="m.gguf")
    assert admission.differences(ordinary, {"private": "guest-1"}, [])
    assert admission.differences(mine, {}, [])
    assert admission.differences(mine, {"private": "guest-2"}, [])
    theirs = Held(port=9, model="m.gguf", pid=1, shape={"device": "gpu", "private": "guest-1"})
    open_one = Held(port=9, model="m.gguf", pid=1, shape={"device": "gpu", "private": ""})
    assert theirs.short_of({}) and theirs.short_of({"private": "guest-1"}) and open_one.short_of({"private": "guest-1"})
    assert not open_one.short_of({})


def test_a_private_server_takes_llama_cpp_only_and_counts_its_memory(tmp_path):
    with pytest.raises(ValueError, match=r"llama\.cpp"):
        private.sealed(be.ServerSpec(model="m", private="g", engine="vllm"))
    weights = tmp_path / "m.gguf"
    weights.write_bytes(b"GGUF" * 1000)
    small = admission.estimate_bytes(be.ServerSpec(model=weights, private="g", cache_ram_mb=0))
    assert admission.estimate_bytes(be.ServerSpec(model=weights, private="g", cache_ram_mb=100)) == small + 100 * admission.MIB
