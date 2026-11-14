"""A leased llama-server starts with an API key, and clients of its address send it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from poolhouse import serverkeys
from poolhouse.http import build_request
from poolhouse.serve import backend as be
from poolhouse.serve.grant import broker_grant
from poolhouse.serve.policy import LocalPolicy


def test_a_key_is_found_by_the_url_of_its_port():
    key = serverkeys.issue(8123)
    serverkeys.bind(8123, os.getpid())
    assert serverkeys.for_url("http://127.0.0.1:8123/v1/models") == key
    assert serverkeys.for_url("http://localhost:8123") == key
    assert serverkeys.for_url("http://127.0.0.1:8124") == ""


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


def test_a_bound_key_is_sent_while_its_process_lives():
    key = serverkeys.issue(8123)
    serverkeys.bind(8123, os.getpid())
    assert serverkeys.for_url("http://127.0.0.1:8123") == key


def test_forgetting_a_port_drops_the_key_of_an_ended_server_and_never_that_of_a_live_one():
    key = serverkeys.issue(8123)
    serverkeys.write_key_file(8123, key)
    serverkeys.bind(8123, os.getpid())
    serverkeys.forget(8123)   # a start that failed beside a live server on the port
    assert serverkeys.for_url("http://127.0.0.1:8123") == key and serverkeys.key_file(8123).exists()
    gone = subprocess.Popen(["true"])
    gone.wait()
    serverkeys.bind(8123, gone.pid)
    serverkeys.forget(8123)
    assert serverkeys.for_url("http://127.0.0.1:8123") == "" and not serverkeys.key_file(8123).exists()
    serverkeys.issue(8124)   # one that never came up
    serverkeys.forget(8124)
    assert serverkeys.for_url("http://127.0.0.1:8124") == ""


def test_the_keys_file_is_private():
    serverkeys.issue(8123)
    assert serverkeys._file().stat().st_mode & 0o077 == 0


def test_a_request_to_a_leased_server_carries_its_key():
    key = serverkeys.issue(8123)
    serverkeys.bind(8123, os.getpid())
    request = build_request("http://127.0.0.1:8123/props")
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


def test_start_issues_a_key_and_hands_it_to_the_server(monkeypatch, tmp_path):
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
            be.ServerSpec(model=weights, port=8123), lease=be.Lease(port=8123, owner_pid=os.getpid(), state_file="x"),
            check_flags=False, preflight=False)
    argv = seen[0]
    keyfile = Path(argv[argv.index("--api-key-file") + 1])
    key = keyfile.read_text().strip()
    serverkeys.bind(8123, os.getpid())
    assert key and "--api-key" not in argv and serverkeys.for_url("http://127.0.0.1:8123") == key


def test_claude_and_codex_run_with_the_servers_key(tmp_path):
    from poolhouse import claude, codex

    key = serverkeys.issue(8123)
    serverkeys.bind(8123, os.getpid())
    assert claude.environment("http://127.0.0.1:8123", "m", base={})["ANTHROPIC_AUTH_TOKEN"] == key
    assert claude.environment("http://127.0.0.1:9", "m", base={})["ANTHROPIC_AUTH_TOKEN"] == "local"
    assert codex.environment(tmp_path, {}, key=key)[codex.KEY_ENV] == key
    assert f'env_key = "{codex.KEY_ENV}"' in codex.config_toml("http://127.0.0.1:8123", "m", 0, ("a", "b", 1.0))


def test_a_key_never_starts_with_a_dash(monkeypatch):
    monkeypatch.setattr(serverkeys, "_save", lambda entries: None)
    assert all(not serverkeys.issue(9000).startswith("-") for _ in range(400))
