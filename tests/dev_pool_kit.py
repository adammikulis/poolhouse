"""Real daemons for the suites about test shards between members of a pool: the daemon's own request handler and job runner,
listening on loopback over the TLS the pool pins, reached through `Member` as another member would.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from poolhouse import http
from poolhouse.fleet import shard_host, tls
from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.daemon import load_or_create_token
from poolhouse.fleet.discovery import Beacon, mint_pool
from poolhouse.fleet.framing import LimitedServer
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.pool_roster import Pool
from poolhouse.fleet.remote import Member
from poolhouse.testfarm import dev

REAL_RUNNER = '''\
import subprocess
import sys

junit = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--junitxml="))
files = [a for a in sys.argv[2:] if a.startswith("tests/")]
done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}", *files], check=False)
raise SystemExit(done.returncode)
'''


@dataclass
class Device:
    """One member of the pool: its daemon, the client another member would use, and its root."""

    httpd: LimitedServer
    runner: JobRunner
    member: Member
    root: Path
    ident: tls.Identity

    @property
    def fingerprint(self) -> str:
        return dev.fingerprint_of(self.ident.beacon)

    def stop(self) -> None:
        self.runner.shutdown()
        self.httpd.shutdown()
        self.httpd.server_close()


def real_tree(root: Path) -> Path:
    """A git tree whose `scripts/test` runs pytest on the named files, with one passing and one failing test file."""
    (root / "scripts").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "scripts" / "test").write_text(REAL_RUNNER, encoding="utf-8")
    (root / "tests" / "test_pass.py").write_text("def test_adds():\n    assert 1 + 1 == 2\n\n\ndef test_names():\n    print('real output')\n", encoding="utf-8")
    (root / "tests" / "test_broken.py").write_text("def test_wrong():\n    assert 1 == 2, 'one is not two'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture
def this_python_runs_shards(monkeypatch):
    """The Python running these tests is the one the device uses, whatever its minor version."""
    monkeypatch.setattr(shard_host, "WANTED", f"{sys.version_info.major}.{sys.version_info.minor}")
    monkeypatch.setenv(shard_host.PYTHON_ENV, sys.executable)
    shard_host._known.clear()
    yield
    http._PINNED.clear()


def serve(tmp_path: Path, name: str = "box", mode: str = "dev", enrol_sender: bool = True) -> Device:
    """A daemon listening on loopback over TLS in a pool of mode ``mode``; the sender's certificate is a member's unless told otherwise."""
    root = tmp_path / name
    (root / "files").mkdir(parents=True)
    ident = tls.identity(root / "tls", name)
    keyfile = root / "pool.key"
    mint_pool("lab", keyfile, mode=mode)
    pool = Pool(keyfile)
    for one in (ident, tls.local()) if enrol_sender else (ident,):
        pool.enrol("lab", one.beacon, name, "test")
    runner = JobRunner(root)
    token = load_or_create_token(root)
    daemon = Daemon(runner, root / "files", token, name=name, pool_key_path=keyfile, members=pool, pool_mode=mode)
    httpd = LimitedServer(("127.0.0.1", 0), make_handler(daemon), tls=tls.member_context(ident, pool))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    http.pin(f"127.0.0.1:{httpd.server_port}", tls.pinned_context(ident.beacon))
    beacon = Beacon(name=name, port=httpd.server_port, host="127.0.0.1", cert=ident.beacon, machine=f"machine-of-{name}")
    member = Member(f"https://127.0.0.1:{httpd.server_port}", token, beacon=beacon)
    return Device(httpd, runner, member, root, ident)


@contextlib.contextmanager
def device(tmp_path: Path, **kwargs):
    one = serve(tmp_path, **kwargs)
    try:
        yield one
    finally:
        one.stop()


def sender(one: Device, lines: list[str] | None = None) -> dev.DevShards:
    """`DevShards` talking to ``one``; its output lines go to ``lines``."""
    return dev.DevShards({one.fingerprint: one.member}, say=(lines if lines is not None else []).append)


