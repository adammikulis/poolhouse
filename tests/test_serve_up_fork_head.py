"""`poolhouse-serve up` takes a fork build only for a model whose heads all borrow its embeddings, which
a fork build alone loads; a head the mainline build loads keeps the model on that build."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from poolhouse import hub
from poolhouse.serve import binary, lifecycle_cli, mtp

MODEL = "Qwen3.8-Flash-Next-UD-IQ2_XS-00001-of-00003.gguf"
SHARED = "mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf"
OWN = "mtp-Qwen3.8-Flash-Next-Q8_0.gguf"


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A model with a borrowing head and its own head beside it, and a mainline `current` build."""
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.delenv(mtp.ENV, raising=False)
    monkeypatch.delenv("LLAMA_CPP_SERVER", raising=False)
    monkeypatch.delenv("LLAMA_CPP_DIR", raising=False)
    monkeypatch.delenv("POOLHOUSE_LLAMA_BUILD", raising=False)
    folder = tmp_path / "hub" / "models--maker--Flash-GGUF" / "snapshots" / "abc"
    (folder / "MTP").mkdir(parents=True)
    for path in (folder / MODEL, folder / "MTP" / SHARED, folder / "MTP" / OWN):
        path.write_bytes(b"GGUF" + b"\0" * 64)
    current = binary.managed_current()
    current.mkdir(parents=True)
    (current / "llama-server").write_text("#!/bin/sh\nexit 0\n")
    (current / "llama-server").chmod(0o755)
    return folder / MODEL


def a_fork(name: str = "unsloth") -> None:
    where = binary.managed_named() / name
    where.mkdir(parents=True)
    (where / "llama-server").write_text("#!/bin/sh\nexit 0\n")
    (where / "llama-server").chmod(0o755)


def asked() -> argparse.Namespace:
    return argparse.Namespace(binary="", build="", draft="", spec="", spec_n_max=None, mtp=True)


def test_a_head_only_a_fork_loads_is_served_on_that_fork_build(machine, capsys):
    a_fork()
    (machine.parent / "MTP" / OWN).unlink()
    args = asked()
    lifecycle_cli.fork_head(args, str(machine))
    assert args.build == "unsloth"
    assert Path(args.draft).name == SHARED and args.spec == mtp.KIND and args.spec_n_max
    assert "MTP on" in capsys.readouterr().err


@pytest.mark.parametrize("given", [{"build": "spec"}, {"binary": "/x/llama-server"}, {"draft": "none"},
                                   {"spec": "none"}, {"mtp": False}])
def test_what_the_person_asked_for_is_left_alone(machine, given):
    a_fork()
    args = asked()
    for key, value in given.items():
        setattr(args, key, value)
    before = dict(vars(args))
    lifecycle_cli.fork_head(args, str(machine))
    assert vars(args) == before


def test_a_head_mainline_loads_keeps_the_model_off_the_fork(machine):
    """The borrowing head is the smaller one, and the model still stays on the mainline build."""
    a_fork()
    (machine.parent / "MTP" / OWN).write_bytes(b"GGUF" + b"\0" * 128)
    args = asked()
    lifecycle_cli.fork_head(args, str(machine))
    assert (args.build, args.draft, args.spec) == ("", "", "")
    assert [(one.name, one.build) for one in hub.heads_for(str(machine))] == [(OWN, ""), (SHARED, "unsloth")]


def test_a_machine_with_no_fork_build_keeps_the_mainline_choice(machine):
    args = asked()
    lifecycle_cli.fork_head(args, str(machine))
    assert (args.build, args.draft, args.spec) == ("", "", "")
