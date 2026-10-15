"""The node launcher with real processes: start on demand, kill -9, checksum refusal, upgrade swap and rollback."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psutil
import pytest
from node_kit import runner_binary

from poolhouse import (
    node_binary,
    node_launch,
    node_supervise,
    runtime,
    runtime_store,
    worktreerules,
)
from poolhouse.lock import held_by, only_one, pid_alive
from poolhouse.node_health import call, node_health

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.slow


@pytest.fixture(scope="session")
def built() -> Path:
    """The node binary the runner built before the run."""
    return runner_binary()


@pytest.fixture
def home(monkeypatch):
    """A short state root (a unix socket path is limited to about 100 bytes), with every node in it stopped afterwards."""
    root = Path(tempfile.mkdtemp(prefix="mln"))
    (root / "starts.log").touch(mode=0o600)
    monkeypatch.setenv("POOLHOUSE_HOME", str(root))
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    source = root / "source"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(source)], check=True)
    from test_runtime_trust import commit, git

    head = commit(source, "source")
    git(source, "update-ref", "refs/remotes/origin/dev", head)
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "dev")
    runtime_store.write_state({"checkout": str(source)})
    yield root
    node_launch.stop_node(root / "node", wait_s=5)
    shutil.rmtree(root, ignore_errors=True)


def install_runtime(binary: Path, name: str, *, select: bool = True) -> Path:
    """A runtime tree holding ``binary`` as its node, selected the way `runtime.publish` writes it."""
    prefix = runtime.directory() / (name * 40)[:40] / uuid.uuid4().hex
    prefix.mkdir(parents=True)
    source = Path(runtime_store.read_state()["checkout"])
    from test_runtime_trust import git

    actual = prefix / "actual-node"
    shutil.copyfile(binary, actual)
    actual.chmod(0o700)
    wrapper = prefix / "launch.py"
    ledger = source.parent / "starts.log"
    digest = node_binary.sha256(actual)
    wrapper.write_text(
        f"#!{sys.executable} -I\n"
        "import hashlib,os,sys\n"
        f"log={str(ledger)!r};binary={str(actual)!r};digest={digest!r}\n"
        "assert os.stat(log).st_size <= 4096\n"
        "assert sys.argv.count('--state')==1\n"
        "state=sys.argv[sys.argv.index('--state')+1]\n"
        "scope=hashlib.sha256(os.path.realpath(state).encode()).hexdigest()\n"
        "with open(log,'ab') as out:out.write(f'{os.getpid()} {os.getppid()} {scope}\\n'.encode('ascii'))\n"
        "h=hashlib.sha256()\n"
        "with open(binary,'rb') as stream:\n"
        " while block:=stream.read(1<<20):h.update(block)\n"
        "assert h.hexdigest()==digest\n"
        "os.execv(binary,[binary,*sys.argv[1:]])\n",
        encoding="utf-8")
    wrapper.chmod(0o700)
    node_binary.install(prefix, wrapper, commit=git(source, "rev-parse", "HEAD"))
    if select:
        with only_one(worktreerules.landing_lock(source), timeout=15, note="test runtime selection"):
            (runtime.directory() / "selected.json").write_text(json.dumps({"prefix": str(prefix)}), encoding="utf-8")
    return prefix


def clients(state: Path, count: int) -> list[subprocess.Popen]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return [subprocess.Popen([sys.executable, "-m", "poolhouse.node_launch", "ensure", "--state", str(state)], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(count)]


def processes(state: Path) -> list[str]:
    nodes, supervisors = set(), set()
    scope = hashlib.sha256(str(state.resolve()).encode()).hexdigest()
    with (state.parent / "starts.log").open("rb") as stream:
        data = stream.read(4097)
    assert len(data) <= 4096, "owned node launch ledger exceeds its bound"
    for line in data.decode("ascii").splitlines():
        match = re.fullmatch(r"([1-9][0-9]{0,9}) ([1-9][0-9]{0,9}) ([0-9a-f]{64})", line)
        assert match is not None, "owned node launch ledger is malformed"
        if match.group(3) != scope:
            continue
        node, supervisor = map(int, match.groups()[:2])
        nodes.add(node)
        supervisors.add(supervisor)
    run = node_supervise.running(state)
    for field, found in (("pid", nodes), ("supervisor", supervisors)):
        if field in run:
            pid = run[field]
            assert type(pid) is int and pid > 0, "owned node run PID is malformed"
            found.add(pid)
    lock = state / node_supervise.LOCK
    if lock.exists() and (holder := held_by(lock)):
        match = re.fullmatch(r"pid ([1-9][0-9]{0,9})(?: .*)?", holder)
        assert match is not None, "owned supervisor lock PID is malformed"
        supervisors.add(int(match.group(1)))
    return [*(f"{pid} poolhouse-node run" for pid in sorted(nodes) if _alive(pid)),
            *(f"{pid} node_launch supervise" for pid in sorted(supervisors) if _alive(pid))]


def _alive(pid: int) -> bool:
    if not pid_alive(pid):
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def board(state: Path) -> tuple[str, str]:
    """A session on the board `demo`: its name and token."""
    said = call(state, "register", {"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "launcher-test"}, board="demo")
    return said["name"], said["token"]


def posts(state: Path, token: str) -> list[str]:
    read = call(state, "read", {"since": {}, "limit": 100}, board="demo", token=token)
    return [e["fields"]["body"] for e in read["entries"] if e["kind"] == "message"]


def post(state: Path, token: str, text: str) -> None:
    call(state, "post", {"kind": "message", "fields": {"type": "status", "body": text}}, board="demo", token=token)


def waited(what: str, ready, seconds: float = 15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if (found := ready()):
            return found
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def selected_node(state: Path, prefix: Path):
    """Observe the selected binary answering, rather than an intermediate swap report."""
    found = node_launch.status(state)
    return found if found.get("healthy") and found.get("sha256") == node_binary.record_of(prefix)["sha256"] else None


def broken_node(home: Path):
    attempted, binary = home / "broken-attempted", home / "broken"
    assert not attempted.exists()
    binary.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                      f"Path({str(attempted)!r}).write_text('attempted')\nraise SystemExit(3)\n", encoding="utf-8")
    binary.chmod(0o700)
    return binary, attempted


def recovered_node(state: Path, previous: Path, attempted: Path):
    found = selected_node(state, previous)
    return found if (found and found["pinned"] and attempted.exists()
                     and attempted.read_text() == "attempted") else None


def test_fixture_selection_waits_for_the_same_runtime_boundary(home, built, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    first = install_runtime(built, "a")
    entered = threading.Event()
    real_lock = only_one

    def observed_lock(path, **options):
        assert path == worktreerules.landing_lock(home / "source")
        entered.set()
        return real_lock(path, **options)

    monkeypatch.setitem(install_runtime.__globals__, "only_one", observed_lock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with real_lock(worktreerules.landing_lock(home / "source"), note="held test cutover"):
            pending = executor.submit(install_runtime, built, "b")
            assert entered.wait(5), "selection writer did not reach the shared boundary"
            assert not pending.done()
            assert runtime.selection_prefix() == first
        second = pending.result(timeout=5)
    assert runtime.selection_prefix() == second


def test_eight_clients_start_exactly_one_node(home, built):
    install_runtime(built, "a")
    state = home / "node"
    runs = clients(state, 8)
    results = [run.communicate(timeout=60) for run in runs]
    assert [run.returncode for run in runs] == [0] * 8, results
    pids = {json.loads(out)["pid"] for out, _ in results}
    assert len(pids) == 1
    lines = processes(state)
    assert len([line for line in lines if "poolhouse-node run" in line]) == 1, lines
    assert len([line for line in lines if "node_launch supervise" in line]) == 1, lines
    health = node_launch.status(state)
    assert health["healthy"] and health["supervised"] and health["pid"] in pids and health["version"]
    assert health["uptime_s"] is not None and health["socket"].endswith("node.sock")


def test_kill_9_restarts_the_node_with_the_board_intact(home, built):
    install_runtime(built, "a")
    state = home / "node"
    node_launch.ensure_node(state)
    _, token = board(state)
    for index in range(5):
        post(state, token, f"row {index}")
    first = node_health(state)["pid"]
    os.kill(first, signal.SIGKILL)
    after = waited("a new node", lambda: (h := node_health(state)) and h["pid"] != first and h)
    assert after["pid"] != first
    again = call(state, "register", {"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "launcher-test"}, board="demo")
    assert again["name"] and sorted(posts(state, again["token"])) == [f"row {i}" for i in range(5)]
    # ensure_node finds the restarted node and starts nothing more
    assert node_launch.ensure_node(state)["pid"] == after["pid"]
    assert len([line for line in processes(state) if "poolhouse-node run" in line]) == 1


def test_a_binary_that_does_not_match_its_checksum_is_refused(home, built):
    prefix = install_runtime(built, "a")
    with node_binary.location(prefix).open("ab") as stream:
        stream.write(b"tampered")
    state = home / "node"
    with pytest.raises(node_binary.NodeBinaryError, match="checksum"):
        node_launch.ensure_node(state)
    assert node_health(state) is None and processes(state) == []
    with pytest.raises(node_binary.NodeBinaryError, match="checksum"):
        node_binary.verified(prefix)


def test_swap_to_a_new_binary_keeps_the_board_and_the_socket(home, built):
    first = install_runtime(built, "a")
    state = home / "node"
    node_launch.ensure_node(state)
    _, token = board(state)
    post(state, token, "row before")
    old = node_launch.status(state)
    newer = home / "node-v2"
    shutil.copyfile(built, newer)
    with newer.open("ab") as stream:
        stream.write(b"\0" * 16)  # trailing bytes: a different checksum, the same program
    newer.chmod(0o700)
    second = install_runtime(newer, "b")
    done = node_launch.swap(state)
    assert done["action"] in {"swapped", "current"} or done == {
        "action": "deferred", "detail": "node replacement waits for landing"}, done
    now = waited("the selected binary to answer", lambda: selected_node(state, second))
    assert now["pid"] != old["pid"] and now["sha256"] != old["sha256"] and now["socket"] == old["socket"]
    assert "row before" in posts(state, board(state)[1])
    assert first.is_dir()
    assert len([line for line in processes(state) if "poolhouse-node run" in line]) == 1
    assert node_launch.swap(state)["action"] == "current"


def test_swap_to_an_unhealthy_binary_rolls_back_to_the_old_one(home, built):
    first = install_runtime(built, "a")
    state = home / "node"
    node_launch.ensure_node(state)
    _, token = board(state)
    post(state, token, "row before")
    old = node_launch.status(state)
    broken, attempted = broken_node(home)
    install_runtime(broken, "c")
    done = node_launch.swap(state, wait_s=4)
    assert (done["action"] == "failed" and "runs the previous binary again" in done["detail"]) or done == {
        "action": "deferred", "detail": "node replacement waits for landing"}, done
    now = waited("the attempted target to recover", lambda: recovered_node(state, first, attempted))
    assert now["healthy"] and now["sha256"] == old["sha256"] and now["pinned"]
    assert "row before" in posts(state, board(state)[1])


def test_stop_ends_node_and_supervisor(home, built):
    install_runtime(built, "a")
    state = home / "node"
    node_launch.ensure_node(state)
    assert node_launch.stop_node(state)
    waited("the processes to go", lambda: processes(state) == [])
    assert node_supervise.running(state)["pid"]


def variant(home: Path, built: Path, name: str, tail: bytes) -> Path:
    """A copy of the node with other trailing bytes: another checksum, the same program."""
    copy = home / name
    shutil.copyfile(built, copy)
    with copy.open("ab") as stream:
        stream.write(tail)
    copy.chmod(0o700)
    return copy


def test_runtime_status_line_and_ensure_move_the_node(home, built):
    from poolhouse import runtime_cli, runtime_deploy

    state = home / "node"
    assert "NOT RUNNING" in runtime_cli.node_line()
    first = install_runtime(built, "a")
    node_launch.ensure_node(state)
    line = runtime_cli.node_line()
    assert "healthy" in line and "supervised" in line and str(state / "node.sock") in line and "pid" in line
    second = install_runtime(variant(home, built, "node-v3", b"\1"), "d")
    moved = runtime_cli._with_node(runtime_deploy.Outcome("switched", "d" * 40, "selected"))
    assert moved.action == "switched"
    assert moved.detail.startswith(("selected; node swapped:", "selected; node current:")) or moved.detail == (
        "selected; node deferred: node replacement waits for landing"), moved
    now = waited("the selected binary to answer", lambda: selected_node(state, second))
    assert now["sha256"] != node_binary.record_of(first)["sha256"]
    broken, attempted = broken_node(home)
    install_runtime(broken, "e")
    refused = runtime_cli._with_node(runtime_deploy.Outcome("switched", "e" * 40, "selected"))
    assert (refused.action == "failed" and "previous binary again" in refused.detail) or (
        refused.action == "switched" and refused.detail == "selected; node deferred: node replacement waits for landing"), refused
    recovered = waited("the attempted target to recover", lambda: recovered_node(state, second, attempted))
    assert recovered["socket"] == now["socket"]
    assert "PINNED" in runtime_cli.node_line()


def test_packaging_builds_the_node_with_its_checksum(home, built, monkeypatch):
    owner = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setenv("RUSTUP_HOME", str(owner / ".rustup"))
    monkeypatch.setenv("CARGO_HOME", str(owner / ".cargo"))
    monkeypatch.setenv("CARGO_TARGET_DIR", str(Path(tempfile.gettempdir()) / "poolhouse-packaging-target"))
    packaging = runpy.run_path(str(ROOT / "packaging" / "build.py"))
    made = packaging["node"](home / "dist")
    digest, name = (made.with_name(made.name + ".sha256").read_text(encoding="utf-8").split())
    assert name == made.name and digest == node_binary.sha256(made) and made.parent == home / "dist" / "node"


def test_a_runtime_whose_verification_mark_names_another_node_is_refused(home, built):
    prefix = install_runtime(built, "a", select=False)
    mark = prefix / "verified.json"
    mark.write_text(json.dumps({"node_sha256": "0" * 64}), encoding="utf-8")
    with pytest.raises(node_binary.NodeBinaryError, match="checksum"):
        node_binary.verified(prefix)
    mark.write_text(json.dumps({"node_sha256": node_binary.record_of(prefix)["sha256"]}), encoding="utf-8")
    assert node_binary.verified(prefix).is_file()
    assert node_launch.smoke(prefix)["version"]


def test_owned_launch_ledger_keeps_duplicate_nodes_visible(home, monkeypatch):
    scope = hashlib.sha256(str((home / "node").resolve()).encode()).hexdigest()
    (home / "starts.log").write_text(f"101 201 {scope}\n102 201 {scope}\n101 201 {scope}\n")
    monkeypatch.setattr(sys.modules[__name__], "_alive", lambda _: True)
    assert processes(home / "node") == ["101 poolhouse-node run", "102 poolhouse-node run", "201 node_launch supervise"]


def test_owned_supervisor_is_visible_before_first_node_exec(home, monkeypatch):
    state = home / "node"
    state.mkdir()
    (state / node_supervise.LOCK).touch()
    monkeypatch.setattr(sys.modules[__name__], "held_by", lambda _: "pid 201 node supervisor")
    monkeypatch.setattr(sys.modules[__name__], "_alive", lambda _: True)
    assert processes(state) == ["201 node_launch supervise"]


@pytest.mark.parametrize("receipt", [b"bad", b"1 2 extra", b"0 2", b"1 -2", b"1 2\n" * 1025], ids=["text", "extra", "zero", "negative", "overflow"])
def test_owned_launch_ledger_rejects_malformed_or_unbounded_data(home, receipt):
    (home / "starts.log").write_bytes(receipt)
    with pytest.raises(AssertionError):
        processes(home / "node")


def test_owned_launch_receipt_is_scoped_to_its_actual_state(home, monkeypatch):
    scope = hashlib.sha256(str((home / "other").resolve()).encode()).hexdigest()
    (home / "starts.log").write_text(f"101 201 {scope}\n")
    queried = []
    monkeypatch.setattr(sys.modules[__name__], "_alive", lambda pid: queried.append(pid) or True)
    assert processes(home / "node") == []
    assert queried == []


def test_actual_binary_checksum_is_bound_by_its_owned_exec_wrapper(home, built):
    prefix = install_runtime(built, "a")
    with (prefix / "actual-node").open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(node_launch.NodeUnavailable):
        node_launch.smoke(prefix, wait_s=2)
    assert node_health(home / "node") is None and processes(home / "node") == []
