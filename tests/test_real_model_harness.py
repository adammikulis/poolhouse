"""The real-model harness fails loudly and never skips, and its marker and flag select the way they say.

No model is loaded here: these tests point the harness at an empty hub, a bare PATH and a busy or GPU-less machine and
watch each of them fail the test, naming the cause and the command that fixes it. The harness itself is driven with a
real model in tests/test_real_model_uses.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import testreuse_key as keys
import testselectors

from poolhouse.serve import ServerSpec
from tests import real_model
from tests.real_model import USES, Real, gpu_problem, locate, stop

REPO = Path(__file__).resolve().parents[1]
FAILED = pytest.fail.Exception


@contextmanager
def fails(match: str = "") -> Iterator[SimpleNamespace]:
    """Expects the body to fail the test, matching ``match`` in the failure; a skip, or no failure, fails this test."""
    got = SimpleNamespace(message="")
    try:
        yield got
    except pytest.skip.Exception as exc:
        pytest.fail(f"the harness SKIPPED instead of failing: {exc}", pytrace=False)
    except FAILED as exc:
        got.message = str(exc)
        assert re.search(match, got.message), f"the failure does not say {match!r}: {got.message}"
        return
    pytest.fail("the harness did not fail", pytrace=False)


def hub_with(root: Path, file: str, repo: str, size: int) -> Path:
    """A hub cache holding ``file`` of ``size`` bytes (sparse) in the layout the Hugging Face client leaves."""
    path = root / f"models--{repo.replace('/', '--')}" / "snapshots" / "abc123" / file
    path.parent.mkdir(parents=True)
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


def harness(tmp_path: Path) -> Real:
    return Real(tmp_path, None, None, roots=[tmp_path / "hub"])


@pytest.fixture(autouse=True)
def _no_overrides(monkeypatch):
    for name in ("POOLHOUSE_TEST_GGUF", "POOLHOUSE_TEST_LLAMA_SERVER"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("use", sorted(USES))
def test_a_missing_model_fails_the_test_and_names_the_fetch_command(use, tmp_path):
    with fails("REAL MODEL MISSING") as raised:
        harness(tmp_path).model(use)
    message = raised.message
    assert USES[use].pin.fetch in message and USES[use].pin.fetch.startswith("poolhouse-models fetch hf:")
    assert USES[use].why in message


def test_a_missing_vision_projector_fails_naming_its_own_fetch_command(tmp_path):
    seer = USES["vision"]
    hub_with(tmp_path / "hub", seer.pin.file, seer.pin.repo, seer.pin.size)
    assert harness(tmp_path).model("vision").name == seer.pin.file
    with fails("REAL MODEL MISSING") as raised:
        harness(tmp_path).projector("vision")
    assert seer.companion.fetch in raised.message


def test_a_partial_download_fails_instead_of_loading_half_a_model(tmp_path):
    chat = USES["chat"].pin
    hub_with(tmp_path / "hub", chat.file, chat.repo, chat.size - 1)
    with fails("REAL MODEL WRONG") as raised:
        harness(tmp_path).model("chat")
    assert chat.fetch in raised.message


def test_the_pinned_file_is_found_in_the_hub_layout(tmp_path):
    chat = USES["chat"].pin
    made = hub_with(tmp_path / "hub", chat.file, chat.repo, chat.size)
    assert harness(tmp_path).model("chat") == made == locate(chat, [tmp_path / "hub"])


def test_a_named_gguf_that_is_not_there_fails_naming_the_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_TEST_GGUF", str(tmp_path / "nowhere.gguf"))
    with fails("POOLHOUSE_TEST_GGUF"):
        harness(tmp_path).model("chat")


def test_a_missing_llama_server_fails_naming_the_command_that_gets_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with fails("LLAMA-SERVER MISSING") as raised:
        harness(tmp_path).binary()
    assert "poolhouse-serve llama-cpp update" in raised.message


def test_a_named_llama_server_that_is_not_there_fails_naming_it(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_TEST_LLAMA_SERVER", str(tmp_path / "no-such-server"))
    with fails(r"POOLHOUSE_TEST_LLAMA_SERVER=.*no-such-server"):
        harness(tmp_path).binary()


def test_the_managed_llama_server_is_the_one_found_before_the_path(tmp_path, monkeypatch):
    managed = tmp_path / ".poolhouse" / "llama.cpp" / "current" / "llama-server"
    on_path = tmp_path / "bin" / "llama-server"
    for server in (managed, on_path):
        server.parent.mkdir(parents=True)
        server.write_text("#!/bin/sh\n")
        server.chmod(0o755)
    monkeypatch.setenv("PATH", str(on_path.parent))
    assert harness(tmp_path).binary() == managed.resolve()
    managed.unlink()
    assert harness(tmp_path).binary() == on_path.resolve()


def test_the_managed_build_is_returned_by_its_own_path_not_through_the_current_link(tmp_path, monkeypatch):
    """The sandbox refuses a command that passes through a symlink, and `current` is one."""
    root = tmp_path / ".poolhouse" / "llama.cpp"
    build = root / "builds" / "b1-abc" / "llama-server"
    build.parent.mkdir(parents=True)
    build.write_text("#!/bin/sh\n")
    (root / "current").symlink_to(build.parent, target_is_directory=True)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert harness(tmp_path).binary() == build.resolve()


def test_a_named_llama_server_outranks_the_managed_one(tmp_path, monkeypatch):
    managed = tmp_path / ".poolhouse" / "llama.cpp" / "current" / "llama-server"
    named = tmp_path / "mine" / "llama-server"
    for server in (managed, named):
        server.parent.mkdir(parents=True)
        server.write_text("#!/bin/sh\n")
    monkeypatch.setenv("POOLHOUSE_TEST_LLAMA_SERVER", str(named))
    assert harness(tmp_path).binary() == named.resolve()


def test_another_server_on_the_compute_fails_the_test_naming_it(tmp_path, monkeypatch):
    running = {"pid": 4242, "port": 54543, "model": "/hub/snapshots/abc/SmolVLM-256M-Instruct-Q8_0.gguf"}
    monkeypatch.setattr(real_model, "every_server", lambda: [running])
    with fails("COMPUTE BUSY") as raised:
        harness(tmp_path).idle(wait=0)
    assert "pid 4242 on port 54543 serving SmolVLM-256M-Instruct-Q8_0.gguf" in raised.message
    monkeypatch.setattr(real_model, "every_server", list)
    harness(tmp_path).idle(wait=0)


def test_a_defunct_server_computes_nothing_and_does_not_hold_the_test_back(tmp_path, monkeypatch):
    dead = {"pid": 4243, "port": 8080, "model": "", "defunct": True}
    monkeypatch.setattr(real_model, "every_server", lambda: [dead])
    harness(tmp_path).idle(wait=0)
    live = {"pid": 4244, "port": 54544, "model": "/hub/m.gguf"}
    monkeypatch.setattr(real_model, "every_server", lambda: [dead, live])
    with fails("COMPUTE BUSY") as raised:
        harness(tmp_path).idle(wait=0)
    assert "pid 4244" in raised.message and "pid 4243" not in raised.message


def test_a_server_that_finishes_while_the_test_waits_lets_it_go_on(tmp_path, monkeypatch):
    left = iter([[{"pid": 1, "port": 2, "model": "m.gguf"}], []])
    monkeypatch.setattr(real_model, "every_server", lambda: next(left, []))
    monkeypatch.setattr(real_model.time, "sleep", lambda _s: None)
    harness(tmp_path).idle(wait=30)


def test_a_machine_without_the_gpu_fails_the_test_and_says_there_is_none(tmp_path, monkeypatch):
    assert "NO GPU" in gpu_problem("Linux", "x86_64") and "Linux" in gpu_problem("Linux", "x86_64")
    assert gpu_problem("Darwin", "arm64") == ""
    monkeypatch.setattr(real_model.platform, "system", lambda: "Windows")
    with fails(r"NO GPU.*Windows"):
        harness(tmp_path).require_metal_gpu()


def test_a_problem_fails_the_test_and_no_problem_does_nothing():
    assert stop("") is None
    with fails("boom"):
        stop("boom")


@pytest.mark.parametrize("how", ["serving", "leased"])
def test_asking_for_a_server_without_the_model_fails_before_anything_starts(how, tmp_path, monkeypatch):
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("POOLHOUSE_TEST_LLAMA_SERVER", str(binary))
    monkeypatch.setattr(real_model, "every_server", lambda: [{"pid": 1, "port": 2, "model": "m.gguf"}])
    with fails("REAL MODEL MISSING"), getattr(harness(tmp_path), how)("chat"):
        pytest.fail("a server started without a model", pytrace=False)


@pytest.mark.parametrize("how", ["serving", "leased"])
def test_asking_for_a_server_beside_another_one_fails_before_anything_starts(how, tmp_path, monkeypatch):
    chat = USES["chat"].pin
    hub_with(tmp_path / "hub", chat.file, chat.repo, chat.size)
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("POOLHOUSE_TEST_LLAMA_SERVER", str(binary))
    monkeypatch.setattr(real_model, "every_server", lambda: [{"pid": 1, "port": 2, "model": "m.gguf"}])
    waiting = Real(tmp_path, None, None, roots=[tmp_path / "hub"], busy_wait=0)
    with fails("COMPUTE BUSY"), getattr(waiting, how)("chat"):
        pytest.fail("a server started beside another", pytrace=False)


def test_every_use_names_a_pin_the_server_spec_accepts():
    for use in USES.values():
        ServerSpec(model="x.gguf", **use.spec)
        assert use.pin.ref == f"hf:{use.pin.repo}/{use.pin.file}" and use.pin.size > 0 and use.why


def test_a_file_marked_real_model_throughout_is_refused_without_the_flag(tmp_path):
    marked = tmp_path / "test_marked.py"
    marked.write_text("import pytest\npytestmark = [pytest.mark.slow, pytest.mark.real_model]\n")
    with pytest.raises(ValueError, match="--real-model"):
        testselectors.refuse_unrequested(marked, "tests/test_marked.py", [])
    testselectors.refuse_unrequested(marked, "tests/test_marked.py", ["--real-model"])


def test_a_file_that_marks_one_test_or_none_is_not_refused(tmp_path):
    mixed = tmp_path / "test_mixed.py"
    mixed.write_text("\n".join(["import pytest", "", "@pytest.mark.real_model", "def test_a():", "    pass", ""]))
    plain = tmp_path / "test_plain.py"
    plain.write_text("pytestmark = []\n")
    for target in (mixed, plain, tmp_path):
        testselectors.refuse_unrequested(target, "tests/x.py", [])


def test_the_five_real_files_are_refused_without_the_flag():
    for name in ("test_serve_real_llama", "test_serve_llamacpp_real", "test_redteam_judge_real",
                 "test_sentinel_real_model", "test_sandbox_gpu", "test_real_model_uses"):
        with pytest.raises(ValueError, match="--real-model"):
            testselectors.refuse_unrequested(REPO / "tests" / f"{name}.py", f"tests/{name}.py", [])


def test_a_real_model_test_file_is_never_served_from_the_reuse_store():
    assert "real_model" in keys.NEVER_MARKS
    barred = keys.lookup(REPO, "tests/test_serve_real_llama.py", []).barred
    assert "real_model" in barred and "--real-model" in barred


@pytest.mark.slow
def test_the_flag_selects_real_model_tests_and_leaves_them_out_without_it():
    def collected(*options: str) -> str:
        done = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                               "-n", "0", *options, "tests/test_serve_real_llama.py"],
                              cwd=REPO, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "PYTHONPATH": str(REPO / "src")})
        return done.stdout
    assert "test_a_real_server_is_leased_queued_and_stopped" not in collected("--slow")
    both = collected("--slow", "--real-model")
    assert "test_a_real_server_is_leased_queued_and_stopped" in both
    assert "test_a_decision_model_is_served_only_through_the_broker" in both
