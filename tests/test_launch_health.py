"""Read-only authenticated attach to an existing daemon; no model behavior."""
import argparse
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from poolhouse.fleet import app_identity, launch
from poolhouse.http import Server


@pytest.fixture
def existing(tmp_path):
    root = tmp_path / "daemon"
    root.mkdir(mode=0o700)
    key = app_identity.secret(root)
    body = {"ok": True, "name": "box", "slots": 1, "free": 1, "busy": False, "commit": "old"}
    mode = ["valid"]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            challenge = self.headers.get(app_identity.HEADER, "")
            proof = hmac.new(key, challenge.encode(), hashlib.sha256).hexdigest()
            value = dict(body, proof=proof)
            if mode[0] == "wrong":
                value["proof"] = "0" * 64
            if mode[0] == "replay":
                value["proof"] = hmac.new(key, b"previous", hashlib.sha256).hexdigest()
            if mode[0] == "malformed":
                value["slots"] = True
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *args):
            pass

    server = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield root, server.server_address[1], mode
    server.shutdown()
    server.server_close()
    worker.join()


def test_ordinary_open_preserves_authenticated_different_commit(existing, monkeypatch):
    root, port, _ = existing
    monkeypatch.setattr(launch, "state", lambda: {"commit": "new"})
    monkeypatch.setattr(launch, "_replace", lambda *args: pytest.fail("ordinary open replaced host"))
    known = argparse.Namespace(port=port, restart=False, no_browser=True)
    assert launch._settle(known, ["--root", str(root)], lambda: None) == 0


@pytest.mark.parametrize("mode", ["wrong", "replay", "malformed"])
def test_ordinary_open_refuses_untrusted_health_without_replacement(existing, monkeypatch, mode):
    root, port, modes = existing
    modes[0] = mode
    monkeypatch.setattr(launch, "state", lambda: {"commit": "new"})
    monkeypatch.setattr(launch, "_replace", lambda *args: pytest.fail("untrusted host replaced"))
    known = argparse.Namespace(port=port, restart=False, no_browser=True)
    assert launch._settle(known, ["--root", str(root)], lambda: None) == 1


@pytest.mark.parametrize("damage", ["missing", "malformed", "link", "readable"])
def test_identity_refusal_does_not_repair_or_chmod(existing, damage):
    from poolhouse.fleet import launch_health

    root, port, _ = existing
    path = root / app_identity.PROOF_KEY_FILE
    if damage == "missing":
        path.unlink()
    elif damage == "malformed":
        path.write_text("short")
    elif damage == "link":
        target = root / "other"
        path.rename(target)
        path.symlink_to(target)
    else:
        path.chmod(0o644)
    before = [(p.name, p.lstat().st_mode, p.lstat().st_mtime_ns) for p in root.iterdir()]
    assert launch_health.authenticated(port, root) is None
    after = [(p.name, p.lstat().st_mode, p.lstat().st_mtime_ns) for p in root.iterdir()]
    assert before == after


def test_wrong_root_is_not_reused(existing, tmp_path):
    from poolhouse.fleet import launch_health

    _, port, _ = existing
    other = tmp_path / "different"
    other.mkdir(mode=0o700)
    app_identity.secret(other)
    assert launch_health.authenticated(port, other) is None


def test_held_unresponsive_port_is_left_alone(monkeypatch):
    monkeypatch.setattr(launch, "already_running", lambda _: None)
    monkeypatch.setattr(launch, "port_held", lambda _: True)
    monkeypatch.setattr(launch, "_held_health", lambda _: None)
    monkeypatch.setattr(launch, "_replace", lambda *args: pytest.fail("replaced unresponsive host"))
    assert launch._settle(argparse.Namespace(port=8770, restart=False), [], lambda: None) == 1


@pytest.mark.parametrize("root", ["", "/linux/root", "windows-root"])
def test_legacy_wsl_authentication_uses_translated_root_without_prepare(monkeypatch, root):
    from poolhouse.fleet import wsl

    calls = []
    health = {"ok": True, "name": "box", "slots": 1, "free": 1, "busy": False, "proof": "a" * 64}

    def read(*args):
        calls.append(args)
        if args[0] == "python3":
            return "/installed/python"
        if args[0] == "wslpath":
            return "/translated/root"
        return json.dumps(health)

    monkeypatch.delenv("POOLHOUSE_HOME", raising=False)
    monkeypatch.delenv("POOLHOUSE_CACHE", raising=False)
    monkeypatch.setattr(wsl, "_read", read)
    monkeypatch.setattr(wsl, "prepare", lambda: pytest.fail("prepared runtime"))
    assert wsl.authenticated_health(["--root", root] if root else [], 8770) == health
    call = calls[-1]
    assert call[:4] == ("env", "/installed/python", "-I", "-c")
    assert call[-2:] == (("/translated/root" if root == "windows-root" else root), "8770")
    assert "launch_health import authenticated" in call[4]
    assert "print(json.dumps(authenticated" in call[4]


def test_legacy_wsl_probe_timeout_leaves_host_running(monkeypatch):
    import subprocess

    monkeypatch.setattr(launch.sys, "platform", "win32")
    monkeypatch.setattr(launch, "already_running", lambda _: {"commit": "old"})
    monkeypatch.setattr(launch, "_replace", lambda *args: pytest.fail("replaced host"))

    def timeout(*args):
        raise subprocess.TimeoutExpired("wsl", 60)

    monkeypatch.setattr(launch, "wsl_authenticated_health", timeout)
    known = argparse.Namespace(port=8770, restart=False, no_browser=True)
    assert launch._settle(known, [], lambda: None) == 1
