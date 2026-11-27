"""A leased llama-server starts with an API key, and clients of its address send it."""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

from poolhouse import home, secretpaths, serverkeys
from poolhouse.http import build_request
from poolhouse.ownership import cmdline_digest, lease_file
from poolhouse.serve import backend as be
from poolhouse.serve.grant import broker_grant
from poolhouse.serve.policy import LocalPolicy
from poolhouse.serve.process import started_at
from poolhouse.testing.registry import record_server


@pytest.fixture
def port():
    """A port this process listens on at 127.0.0.1, as a server does."""
    with socket.create_server(("127.0.0.1", 0)) as held:
        yield held.getsockname()[1]


def test_a_key_is_found_by_the_url_of_its_port(port):
    key = serverkeys.issue(port)
    serverkeys.bind(port, os.getpid())
    assert serverkeys.for_url(f"http://127.0.0.1:{port}/v1/models") == key
    assert serverkeys.for_url(f"http://localhost:{port}") == "", "the name leads to ::1 too, where this process does not listen"
    assert serverkeys.for_url("http://127.0.0.1:9") == ""


def test_a_key_is_not_sent_to_another_host():
    serverkeys.issue(8123)
    assert serverkeys.for_url("http://192.168.1.5:8123") == ""


def test_a_key_is_not_sent_before_it_is_bound_to_its_server():
    serverkeys.issue(8123)   # the health probes of a start that has not yet checked who listens carry none
    assert serverkeys.for_url("http://127.0.0.1:8123") == ""


def test_each_issue_is_a_new_key():
    assert serverkeys.issue(8123) != serverkeys.issue(8123)


def test_a_key_whose_server_has_died_is_not_sent():
    serverkeys.issue(8123)
    gone = subprocess.Popen(["true"])
    gone.wait()
    serverkeys.bind(8123, gone.pid)
    assert serverkeys.for_url("http://127.0.0.1:8123") == ""


def test_a_bound_key_is_sent_while_its_process_lives(port):
    key = serverkeys.issue(port)
    serverkeys.bind(port, os.getpid())
    assert serverkeys.for_url(f"http://127.0.0.1:{port}") == key


def test_forgetting_a_port_drops_the_key_of_an_ended_server_and_never_that_of_a_live_one(port):
    key = serverkeys.issue(port)
    serverkeys.write_key_file(port, key)
    serverkeys.bind(port, os.getpid())
    serverkeys.forget(port)   # a start that failed beside a live server on the port
    assert serverkeys.for_url(f"http://127.0.0.1:{port}") == key and serverkeys.key_file(port).exists()
    gone = subprocess.Popen(["true"])
    gone.wait()
    serverkeys.bind(port, gone.pid)
    serverkeys.forget(port)
    assert serverkeys.for_url(f"http://127.0.0.1:{port}") == "" and not serverkeys.key_file(port).exists()
    serverkeys.issue(8124)   # one that never came up
    serverkeys.forget(8124)
    assert serverkeys.for_url("http://127.0.0.1:8124") == ""


def test_the_keys_file_is_private():
    serverkeys.issue(8123)
    assert serverkeys._file().stat().st_mode & 0o077 == 0


@pytest.mark.redteam
def test_no_file_whose_name_can_be_known_in_advance_holds_a_key():
    key = serverkeys.issue(8123)
    serverkeys.bind(8123, os.getpid())
    held = serverkeys.write_key_file(8123, key)
    assert held.read_text().strip() == key and held.name != "8123.key" and held.stat().st_mode & 0o077 == 0
    assert [path for path in home.home().rglob("*") if path.is_file() and key in path.read_text(errors="ignore")] == [held]
    assert secretpaths.path_hit(str(held.parent)), "the folder that would tell the name is a secret path"
    serverkeys.issue(8123)
    assert serverkeys.key_file(8123).name != held.name and not held.exists()


def test_a_request_to_a_leased_server_carries_its_key(port):
    key = serverkeys.issue(port)
    serverkeys.bind(port, os.getpid())
    request = build_request(f"http://127.0.0.1:{port}/props")
    assert request.get_header("Authorization") == f"Bearer {key}"


def test_a_request_to_an_unleased_address_carries_none():
    serverkeys.issue(8123)
    assert build_request("http://127.0.0.1:9999/props").get_header("Authorization") is None


def test_a_header_the_caller_set_is_kept():
    serverkeys.issue(8123)
    request = build_request("http://127.0.0.1:8123/x", headers={"Authorization": "Bearer mine"})
    assert request.get_header("Authorization") == "Bearer mine"


def test_the_command_passes_the_key_to_llama_server(tmp_path):
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    argv = be.LlamaServerBackend(binary=binary).command(
        be.ServerSpec(model="m.gguf", port=8123, api_key="sekrit"))
    assert "--api-key" not in argv and "sekrit" not in " ".join(argv)   # the key goes in a file, not on a command line
    assert serverkeys.key_file(8123).read_text().strip() == "sekrit"


def test_start_issues_a_key_and_hands_it_to_the_server(monkeypatch, tmp_path, port):
    weights = tmp_path / "m.gguf"
    weights.write_bytes(b"GGUF")
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(be, "claim_port", lambda spec, lease: None)
    seen: list[list[str]] = []

    def launch(argv, *a, **k):
        seen.append(list(argv))
        raise be.ServerFailed("stop here")

    monkeypatch.setattr(be, "launch", launch)
    with broker_grant(), pytest.raises(be.ServerFailed):
        be.LlamaServerBackend(binary=binary, sandboxed=False, policy=LocalPolicy()).start(
            be.ServerSpec(model=weights, port=port), lease=be.Lease(port=port, owner_pid=os.getpid(), state_file="x"),
            check_flags=False, preflight=False)
    argv = seen[0]
    keyfile = Path(argv[argv.index("--api-key-file") + 1])
    key = keyfile.read_text().strip()
    serverkeys.bind(port, os.getpid())
    assert key and "--api-key" not in argv and serverkeys.for_url(f"http://127.0.0.1:{port}") == key


def test_claude_and_codex_run_with_the_servers_key(tmp_path, monkeypatch):
    from poolhouse import claude, codex, harnessid, harnessing

    held = socket.create_server(("127.0.0.1", 0))   # the server's listener, in this process, and its lease record
    port = held.getsockname()[1]
    record_server(lease_file(), port, model="m.gguf", pid=os.getpid(), started=started_at(os.getpid()), cmdline=cmdline_digest(os.getpid()))
    key = serverkeys.issue(port)
    serverkeys.bind(port, os.getpid())
    assert claude.environment(f"http://127.0.0.1:{port}", "m", base={})["ANTHROPIC_AUTH_TOKEN"] == key
    assert claude.environment("http://127.0.0.1:9", "m", base={})["ANTHROPIC_AUTH_TOKEN"] == "local"
    monkeypatch.setattr(codex, "alias_of", lambda *_: "m")
    monkeypatch.setattr(harnessing, "window_of", lambda _: 0)
    ran = []
    assert codex.launch(["--on", f"http://127.0.0.1:{port}", "--project", str(tmp_path), "--codex", "true"], say=lambda _: None,
                        session_factory=lambda name, *_: harnessid.HarnessSession(name), run_codex=lambda run: ran.append(run) or 0) == 0
    [named] = [word.partition("=")[2].strip('"') for word in ran[0].argv if word.startswith("model_providers.poolhouse.env_key=")]
    assert ran[0].env[named] == key
    assert f'env_key = "{codex.KEY_ENV}"' in codex.config_toml(f"http://127.0.0.1:{port}", "m", 0, ("a", "b", 1.0))
    held.close()


def test_a_key_never_starts_with_a_dash(monkeypatch):
    monkeypatch.setattr(serverkeys, "_save", lambda entries: None)
    assert all(not serverkeys.issue(9000).startswith("-") for _ in range(400))
