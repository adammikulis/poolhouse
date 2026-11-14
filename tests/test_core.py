"""`poolhouse.core`: a model served for a program that has no pool, no node, no sentinel and no session.

The servers here are real processes on real loopback ports, and every one is the canned plumbing server
(`poolhouse.testing.canned`: its model id is `CANNED-NOT-A-MODEL` and nothing it says is inferred), because what is
under test is admission, the grant, the policy and the handle's lifecycle, not what a model says.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from poolhouse import core, serverkeys
from poolhouse.client.health import is_healthy
from poolhouse.files import sha256_file
from poolhouse.serve import admission, binary as binary_module
from poolhouse.serve.backend import Lease, LlamaServerBackend, ServerSpec
from poolhouse.serve.binary import BinaryTampered
from poolhouse.serve.grant import NoGrant
from poolhouse.serve.leases import recorded_servers
from poolhouse.serve.process import pid_exists
from poolhouse.testing.canned import CANNED_MODEL_ID, canned_binary

MIB = 1 << 20


@pytest.fixture
def current_build(tmp_path):
    """The managed ``current`` llama-server of this test's state root: the canned plumbing server and its BUILD.json."""
    here = tmp_path / "build"
    here.mkdir()
    canned_binary(here, name="llama-server")
    where = binary_module.managed_current()
    where.mkdir(parents=True)
    for one in here.iterdir():
        shutil.copy2(one, where / one.name)
    server = where / "llama-server"
    (where / "BUILD.json").write_text(json.dumps({"sha256": sha256_file(server)}))
    return server


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "models" / "plumbing-model.gguf"
    path.parent.mkdir()
    path.write_bytes(b"GGUF" + b"\x00" * 64)
    return path


@pytest.fixture(autouse=True)
def _nothing_left(request):
    yield
    core.stop_all()


def budget(monkeypatch, total_mb: int, each_mb: int) -> None:
    """Memory arithmetic with numbers a test can reason about: the machine allows ``total_mb``, a server takes ``each_mb``."""
    monkeypatch.setattr(admission, "budget_mb", lambda: total_mb)
    monkeypatch.setattr(admission, "estimate_bytes", lambda spec: each_mb * MIB)


def test_importing_the_core_loads_no_session_token_board_workspace_approval_or_app_code():
    code = ("import sys, poolhouse.core\n"
            "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('poolhouse'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    banned = ("board", "authority", "authority_caller", "person", "requests", "workspace", "node_app", "node_health",
              "leasetable", "sentinel", "keystore", "fleet", "desktop", "lan", "graph", "decide", "activity",
              "net", "serve.broker", "serve.manager", "serve.guarded", "hub.admission", "hub.remote", "mcp",
              "chat", "agent", "gate")
    found = [m for m in out if any(m == f"poolhouse.{b}" or m.startswith(f"poolhouse.{b}.") for b in banned)]
    assert not found, found


def test_a_path_to_an_installed_gguf_resolves_and_what_is_not_installed_says_so(model, tmp_path):
    assert core.resolve(model) == model
    with pytest.raises(core.ModelNotFound, match="poolhouse-models"):
        core.resolve("no-such-model-anywhere")


def test_a_server_is_started_healthy_recorded_and_ended_with_its_handle(current_build, model):
    with core.serve(model, 2048, preflight=False) as server:
        assert is_healthy(server.base_url, timeout=5.0)
        assert server.model_id == CANNED_MODEL_ID
        assert server.base_url == f"http://127.0.0.1:{server.port}" and server.api_key
        assert pid_exists(server.pid)
        record = recorded_servers()[server.port]
        assert record["pid"] == server.pid and record["owner_pid"] > 0 and record["context"] == 2048
    assert not pid_exists(server.pid)
    assert server.port not in recorded_servers()
    server.close()   # twice is harmless


def test_stop_all_stops_every_server_this_process_started(current_build, model, monkeypatch):
    budget(monkeypatch, 10_000, 100)
    first = core.serve(model, 2048, preflight=False)
    second = core.serve(model, 2048, preflight=False)
    assert first.port != second.port
    stopped = core.stop_all()
    assert sorted(stopped) == sorted([first.pid, second.pid])
    assert not pid_exists(first.pid) and not pid_exists(second.pid)
    assert recorded_servers() == {}


def test_a_start_that_would_overcommit_memory_waits_then_is_refused_and_leaves_nothing_behind(current_build, model, monkeypatch):
    budget(monkeypatch, 1000, 600)
    monkeypatch.setenv(admission.ENV_WAIT, "1")
    with core.serve(model, 2048, preflight=False) as first:
        with pytest.raises(core.AdmissionRefused, match="no memory came free"):
            core.serve(model, 2048, preflight=False)
        assert list(recorded_servers()) == [first.port]   # the refused start wrote nothing that stays
    with core.serve(model, 2048, preflight=False) as again:   # the memory is back when the first is closed
        assert pid_exists(again.pid)


def test_a_model_that_could_never_fit_is_refused_at_once(current_build, model, monkeypatch):
    budget(monkeypatch, 1000, 1500)
    monkeypatch.setenv(admission.ENV_WAIT, "30")
    with pytest.raises(core.AdmissionRefused, match="more than the"):
        core.serve(model, 2048, preflight=False)


def test_two_processes_asking_at_once_cannot_both_take_the_memory(current_build, model, monkeypatch, tmp_path):
    """The second process is a real child with its own interpreter: it sees the first's record in the machine's server file."""
    budget(monkeypatch, 1000, 600)
    with core.serve(model, 2048, preflight=False):
        child = (
            "import sys\nfrom poolhouse import core\nfrom poolhouse.serve import admission\n"
            "admission.budget_mb = lambda: 1000\nadmission.estimate_bytes = lambda spec: 600 << 20\n"
            "try:\n    core.serve(sys.argv[1], 2048, preflight=False)\nexcept core.AdmissionRefused as why:\n"
            "    print('REFUSED', why)\n")
        env = {**__import__("os").environ, admission.ENV_WAIT: "1"}
        out = subprocess.run([sys.executable, "-c", child, str(model)], capture_output=True, text=True, env=env, timeout=120)
        assert "REFUSED" in out.stdout, out.stdout + out.stderr


def test_a_binary_that_is_not_the_one_that_was_built_starts_nothing(current_build, model):
    current_build.write_bytes(current_build.read_bytes() + b"# swapped")
    with pytest.raises(BinaryTampered):
        core.serve(model, 2048, preflight=False)
    assert recorded_servers() == {}


def test_a_server_that_fails_to_start_leaves_no_record_and_no_process(current_build, model, monkeypatch):
    def refuse(self, spec, *, lease, timeout=0, **starting):
        raise core.ServerFailed("the plumbing server said no")

    monkeypatch.setattr(LlamaServerBackend, "start", refuse)
    with pytest.raises(core.ServerFailed, match="said no"):
        core.serve(model, 2048, preflight=False)
    assert recorded_servers() == {}


def test_the_backend_still_refuses_to_start_a_server_outside_the_grant(current_build, model):
    """The core opens the grant in one place; a caller that goes around it gets the same refusal as before."""
    with pytest.raises(NoGrant):
        Lease(port=1, owner_pid=1, state_file="x")
    spec = ServerSpec(model=str(model), port=1)
    with pytest.raises(NoGrant):
        LlamaServerBackend().start(spec, lease=object())


def test_the_key_reaches_the_server_in_a_private_file_and_never_on_the_command_line(current_build, model):
    with core.serve(model, 2048, preflight=False) as server:
        argv = json.loads((current_build.parent / "argv.json").read_text())
        assert "--api-key" not in argv and server.api_key not in " ".join(argv)
        keyfile = serverkeys.key_file(server.port)
        assert argv[argv.index("--api-key-file") + 1] == str(keyfile)
        assert keyfile.read_text().strip() == server.api_key
        assert keyfile.stat().st_mode & 0o777 == 0o600 and keyfile.parent.stat().st_mode & 0o777 == 0o700
    assert not keyfile.exists()


def test_the_binary_is_found_once_per_start(current_build, model, monkeypatch):
    from poolhouse.serve import strict

    calls = []
    real = strict.require_binary
    monkeypatch.setattr(strict, "require_binary", lambda *a, **k: calls.append(1) or real(*a, **k))
    with core.serve(model, 2048, preflight=False):
        pass
    assert len(calls) == 1


def test_only_the_process_that_was_started_counts_as_the_one_listening(current_build, model):
    with core.serve(model, 2048, preflight=False) as server:
        assert core._owns_listener(server.pid, server.port)
        assert not core._owns_listener(server.pid, server.port + 1)
        stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            assert not core._owns_listener(stranger.pid, server.port)
        finally:
            stranger.kill()
            stranger.wait(timeout=10)
        assert not core._owns_listener(None, server.port)


def test_a_stranger_answering_on_the_port_is_never_sent_the_key_and_the_server_is_stopped(current_build, model, monkeypatch):
    monkeypatch.setattr(core, "_owns_listener", lambda pid, port: False)
    seen = []
    monkeypatch.setattr(core, "_model_id", lambda *a: seen.append(a) or "x")
    with pytest.raises(core.ServerFailed, match="no key was sent"):
        core.serve(model, 2048, preflight=False)
    assert not seen and recorded_servers() == {}


def test_only_a_gguf_file_resolves(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("x")
    with pytest.raises(core.ModelNotFound, match=r"not a \.gguf"):
        core.resolve(other)


def test_a_binary_swapped_after_it_was_found_is_refused_by_the_launch_itself(current_build, tmp_path):
    from poolhouse.serve import backend
    from poolhouse.serve.grant import broker_grant
    from poolhouse.serve.policy import BinaryRefused, LocalPolicy

    local = LocalPolicy()
    assert local.problem(current_build) == ""
    current_build.write_bytes(current_build.read_bytes() + b"# swapped after the check")
    with broker_grant(), pytest.raises(BinaryRefused):
        backend.launch([str(current_build)], Lease(port=1, owner_pid=os.getpid(), state_file="x"),
                       log_path=tmp_path / "log", timeout=5.0, env={}, policy=local)
    assert not (tmp_path / "log").exists()
