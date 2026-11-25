"""A confined process on Linux has none of the host's network but the ports a policy names, and cannot start a sandbox of its own.

The probes run a real Python under the real ``bwrap`` and try what any program can: connect to a port of the host that the policy did not
name, to a listener on every address, to the host's own address, to a Unix socket of the host (by path and in the abstract namespace),
enter a new user namespace and read its own capabilities. The argument tests need no bubblewrap and run everywhere. A host that cannot make
the namespaces (an unprivileged container, a distribution that restricts user namespaces) skips the probes with the reason its own
availability check gives.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import sys
import sysconfig
import threading
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import psutil
import pytest

from poolhouse import sandbox
from poolhouse.sandbox import (
    Limits,
    Net,
    Policy,
    PolicyError,
    bubblewrap,
    confinement,
    netforward,
    netrelay,
)
from poolhouse.sandbox.bubblewrap import Bubblewrap, arguments
from poolhouse.sandbox.confinement import MARKER
from poolhouse.sandbox.policies import SYSTEM_EXEC, runtime_reads
from tests.process_kit import FORKS, THREADS

pytestmark = pytest.mark.slow

PONG = b"pong"


@pytest.fixture
def bwrap():
    """The real bubblewrap backend, or a skip naming why this host cannot make its namespaces."""
    if not sys.platform.startswith("linux"):
        pytest.skip("needs Linux: bubblewrap")
    state = Bubblewrap().available()
    if not state.ok:
        pytest.skip(state.reason)
    return Bubblewrap()


class Host:
    """Listeners on this machine that a confined process must not reach unless its policy names the port."""

    def __init__(self, tmp_path: Path) -> None:
        self.stop = threading.Event()
        self.held: list[socket.socket] = []
        self.accepted = {"named": 0, "other": 0, "wild": 0}
        self.named = self._listen("named", "127.0.0.1")
        self.other = self._listen("other", "127.0.0.1")
        self.wild = self._listen("wild", "0.0.0.0")  # noqa: S104 - a listener on every address is what the probe must not reach
        self.path = str(tmp_path / "host.sock")
        self.abstract = f"poolhouse-test-{os.getpid()}"
        for address in (self.path, "\0" + self.abstract):
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(address)
            listener.listen(4)
            self.held.append(listener)

    def _listen(self, name: str, address: str) -> int:
        listener = socket.socket()
        listener.bind((address, 0))
        listener.listen(64)
        self.held.append(listener)
        threading.Thread(target=self._serve, args=(listener, name), daemon=True).start()
        return listener.getsockname()[1]

    def _serve(self, listener: socket.socket, name: str) -> None:
        kept = []
        while not self.stop.is_set():
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            self.accepted[name] += 1
            connection.sendall(PONG)
            kept.append(connection)  # held open, so a probe can count how many the relay lets through

    def close(self) -> None:
        self.stop.set()
        for each in self.held:
            each.close()


@pytest.fixture
def host(tmp_path):
    made = Host(tmp_path)
    yield made
    made.close()


def lan_address() -> str:
    """An address of this machine that is not loopback, or an empty string when it has none."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("203.0.113.1", 9))
        found = probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()
    return "" if found.startswith("127.") else found


def held(net: Net, processes: int | None = None) -> Policy:
    py = os.path.realpath(sys.executable)
    return Policy("linux", read=runtime_reads(), exec=(py, *SYSTEM_EXEC), net=net, env={"PATH": "/usr/bin:/bin"},
                  limits=Limits(wall_seconds=60, processes=processes))


def confined(code: str, *argv: str, net: Net | None = None, backend=None) -> dict:
    done = sandbox.run([os.path.realpath(sys.executable), "-c", code, *argv], held(net or Net.deny()), via=backend or Bubblewrap(), diagnose="never")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


REACH = r'''
import errno, json, socket, sys
named, other, wild, lan, path, abstract = sys.argv[1:7]
def attempt(family, address):
    made = socket.socket(family, socket.SOCK_STREAM)
    made.settimeout(3)
    try:
        made.connect(address)
    except OSError as error:
        return "refused " + errno.errorcode.get(error.errno or 0, type(error).__name__)
    try:
        return "connected " + made.recv(16).decode()
    except OSError:
        return "connected (and silent)"
    finally:
        made.close()
out = {"named": attempt(socket.AF_INET, ("127.0.0.1", int(named))), "other": attempt(socket.AF_INET, ("127.0.0.1", int(other))),
       "wild": attempt(socket.AF_INET, ("127.0.0.1", int(wild))), "abstract": attempt(socket.AF_UNIX, "\0" + abstract),
       "path": attempt(socket.AF_UNIX, path)}
if lan:
    out["lan"] = attempt(socket.AF_INET, (lan, int(named)))
    out["lan_wild"] = attempt(socket.AF_INET, (lan, int(wild)))
out["routes"] = open("/proc/net/route").read().splitlines()[1:]  # the main table: empty when nothing leads off this machine
print(json.dumps(out))
'''

NAMESPACES = r'''
import ctypes, json, os
libc = ctypes.CDLL(None, use_errno=True)
asked = libc.unshare(0x10000000)  # CLONE_NEWUSER
status = dict(line.split(":\t") for line in open("/proc/self/status").read().splitlines() if ":\t" in line)
print(json.dumps({"unshare_user": asked, "errno": ctypes.get_errno(), "uid": os.getuid(),
                  "capabilities": {k: status[k] for k in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")}, "no_new_privs": status["NoNewPrivs"]}))
'''

RELAYED = r'''
import errno, json, os, socket, sys
directory = sys.argv[1]
out = {"sockets": sorted(os.listdir(directory))}
try:
    made = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    made.bind(directory + "/p1.sock")
    out["bind_in_the_directory"] = "made"
except OSError as error:
    out["bind_in_the_directory"] = errno.errorcode[error.errno]
try:
    os.unlink(directory + "/" + out["sockets"][0])
    out["remove_a_socket"] = "removed"
except OSError as error:
    out["remove_a_socket"] = errno.errorcode[error.errno]
print(json.dumps(out))
'''

FLOOD = r'''
import json, socket, sys, time
port, count = int(sys.argv[1]), int(sys.argv[2])
made = []
for _ in range(count):
    one = socket.socket()
    one.connect(("127.0.0.1", port))
    made.append(one)
time.sleep(2)
closed = 0
for one in made:
    one.settimeout(.2)
    try:
        while one.recv(16) == b"pong":
            pass
        closed += 1
    except TimeoutError:
        pass
print(json.dumps({"closed": closed}))
'''


def test_the_arguments_never_share_the_hosts_network_and_always_close_nested_namespaces(tmp_path):
    for net in (Net.deny(), Net.only(8080)):
        args = arguments(held(net), "/usr/bin/true", relay=str(tmp_path))
        assert "--share-net" not in args  # --unshare-all makes the network namespace; nothing shares the host's back
        assert args[:4] == ["--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
        assert {"--unshare-user", "--disable-userns"} <= set(args)
        assert args[args.index("--cap-drop") + 1] == "ALL"


def test_a_policy_that_asks_for_the_whole_loopback_of_the_host_is_refused_before_a_process_starts():
    with pytest.raises(PolicyError, match=r"Net\.only"):
        arguments(held(Net.loopback()), "/usr/bin/true")


def test_the_backend_refuses_a_policy_that_asks_for_the_whole_loopback_and_starts_nothing(bwrap):
    with pytest.raises(PolicyError, match=r"Net\.only"):
        sandbox.run(["/usr/bin/true"], held(Net.loopback()), via=bwrap)


def test_named_ports_are_carried_in_from_a_socket_directory_the_namespace_gets_read_only(tmp_path):
    args = arguments(held(Net.only(80, 81)), "/usr/bin/true", relay=str(tmp_path))
    assert ["--ro-bind", str(tmp_path), bubblewrap.SOCKETS] == args[args.index(bubblewrap.SOCKETS) - 2:][:3]
    assert bubblewrap.forwarding(held(Net.only(80, 81)))[1:] == ["-I", "-S", bubblewrap.FORWARDER, bubblewrap.SOCKETS, "80,81", "--"]
    assert bubblewrap.forwarding(held(Net.deny())) == []
    with pytest.raises(PolicyError, match="socket directory"):
        arguments(held(Net.only(80)), "/usr/bin/true")


def test_a_relay_carries_a_socket_to_its_own_port_and_to_no_other(tmp_path):
    serving = [socket.socket() for _ in range(2)]
    for listener in serving:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
    named, other = (listener.getsockname()[1] for listener in serving)
    relay = netrelay.Relay((named,))
    through = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert sorted(each.name for each in Path(relay.directory).iterdir()) == [f"p{named}.sock"]
        assert oct(Path(relay.directory).stat().st_mode & 0o777) == "0o700"
        through.connect(f"{relay.directory}/p{named}.sock")
        accepted, _ = serving[0].accept()
        accepted.sendall(PONG)
        assert through.recv(16) == PONG
        through.sendall(b"ping")
        assert accepted.recv(16) == b"ping"
        serving[1].settimeout(.3)
        with pytest.raises(TimeoutError):
            serving[1].accept()
        assert not Path(f"{relay.directory}/p{other}.sock").exists()
    finally:
        relay.close()
        for each in (*serving, through):
            each.close()
    assert not Path(relay.directory).exists()


def test_a_bubblewrap_that_cannot_turn_off_nested_namespaces_is_unavailable_and_says_why(tmp_path, monkeypatch):
    fake = tmp_path / "bwrap"
    fake.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('bwrap: Unknown option --disable-userns')\nsys.exit(1)\n")
    fake.chmod(0o700)
    monkeypatch.setattr(bubblewrap.sys, "platform", "linux")
    monkeypatch.setattr(bubblewrap.shutil, "which", lambda name: str(fake))
    bubblewrap._probe.cache_clear()
    try:
        state = Bubblewrap().available()
    finally:
        bubblewrap._probe.cache_clear()
    assert not state.ok and "--disable-userns" in state.reason and "0.8.0" in state.reason


def test_a_process_reaches_the_port_its_policy_names_and_nothing_else_of_the_host(bwrap, host):
    seen = confined(REACH, str(host.named), str(host.other), str(host.wild), lan_address(), host.path, host.abstract,
                    net=Net.only(host.named), backend=bwrap)
    assert seen["named"] == "connected pong"
    for name in ("other", "wild", "abstract", "path"):
        assert seen[name].startswith("refused"), seen
    assert seen.get("lan", "refused").startswith("refused") and seen.get("lan_wild", "refused").startswith("refused"), seen
    assert seen["routes"] == [], seen
    assert host.accepted["other"] == 0 and host.accepted["wild"] == 0


def test_a_process_whose_policy_names_no_port_reaches_nothing(bwrap, host):
    seen = confined(REACH, str(host.named), str(host.other), str(host.wild), lan_address(), host.path, host.abstract, backend=bwrap)
    assert all(value.startswith("refused") for key, value in seen.items() if key != "routes"), seen
    assert sum(host.accepted.values()) == 0


def test_a_process_cannot_enter_a_user_namespace_and_holds_no_capability(bwrap):
    seen = confined(NAMESPACES, backend=bwrap)
    assert seen["unshare_user"] == -1 and seen["errno"] in (errno.EPERM, errno.ENOSPC, errno.EINVAL), seen
    assert set(seen["capabilities"].values()) == {"0000000000000000"}, seen
    assert seen["no_new_privs"] == "1"


def test_a_process_cannot_start_a_sandbox_of_its_own(bwrap):
    code = "import json, subprocess; print(json.dumps({'status': subprocess.run(['/usr/bin/bwrap', '--unshare-user', '--ro-bind', '/', '/', 'true'], capture_output=True).returncode}))"
    assert confined(code, backend=bwrap)["status"] != 0


def test_the_sockets_a_process_finds_are_the_ports_it_was_given_and_it_cannot_change_them(bwrap, host):
    seen = confined(RELAYED, bubblewrap.SOCKETS, net=Net.only(host.named), backend=bwrap)
    assert seen["sockets"] == [f"p{host.named}.sock"]
    assert seen["bind_in_the_directory"] == "EROFS" and seen["remove_a_socket"] == "EROFS", seen


def test_connections_past_the_bound_are_closed_and_the_sockets_are_gone_when_the_run_ends(bwrap, host):
    policy = held(Net.only(host.named)).validated()
    argv, tag, chosen = sandbox.wrapped([os.path.realpath(sys.executable), "-c", FLOOD, str(host.named), str(netforward.CONNECTIONS + 8)],
                                        policy, via=bwrap)
    directory = argv[argv.index(bubblewrap.SOCKETS) - 1]
    assert Path(directory).is_dir()
    done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False, env={})
    chosen.finish(tag)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["closed"] >= 8
    assert host.accepted["named"] <= netforward.CONNECTIONS
    assert not Path(directory).exists()


def test_the_command_behind_the_forwarder_keeps_its_output_and_its_exit_status(bwrap, host):
    done = sandbox.run([os.path.realpath(sys.executable), "-c", "import sys; print('out'); sys.exit(7)"], held(Net.only(host.named)),
                       via=bwrap, diagnose="never")
    assert (done.returncode, done.stdout) == (7, "out\n")


def test_the_kernel_refuses_the_fork_that_crosses_the_allowance_of_a_run(bwrap):
    py = os.path.realpath(sys.executable)
    ended = json.loads(sandbox.run([py, "-c", FORKS, "400"], held(Net.deny(), processes=40), via=bwrap, diagnose="never").stdout)
    assert ended["refused"] == ended["again"] and ended["forked"] < 200, ended


def test_the_allowance_counts_the_threads_the_kernel_counts(bwrap):
    py = os.path.realpath(sys.executable)
    busy = subprocess.Popen([py, "-c", THREADS, "300"], stdout=subprocess.PIPE, text=True)
    try:
        assert busy.stdout.readline().strip() == "ready"
        done = sandbox.run([py, "-c", FORKS, "400"], held(Net.deny(), processes=40), via=bwrap, diagnose="never")
    finally:
        busy.kill()
        busy.wait()
    ended = json.loads(done.stdout)
    assert ended["refused"] == ended["again"] and 20 <= ended["forked"] < 200, ended


def test_a_relay_directory_left_by_a_process_that_was_killed_is_swept(tmp_path, monkeypatch):
    monkeypatch.setattr(netrelay.tempfile, "tempdir", str(tmp_path))
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    stale = tmp_path / f"poolhouse-net-{gone.pid}-1-abcdefgh"
    mine = tmp_path / f"poolhouse-net-{os.getpid()}-{int(psutil.Process().create_time())}-abcdefgh"
    reused = tmp_path / f"poolhouse-net-{os.getpid()}-1-abcdefgh"
    other = tmp_path / "poolhouse-net-notes"
    linked = tmp_path / f"poolhouse-net-{gone.pid}-2-linked00"
    for each in (stale, mine, reused, other):
        each.mkdir()
    linked.symlink_to(other, target_is_directory=True)
    relay = netrelay.Relay(())
    try:
        assert [each.exists() for each in (stale, mine, reused, other)] == [False, True, False, True]
        assert linked.is_symlink() and other.is_dir()
    finally:
        relay.close()


def test_the_backend_runs_the_bubblewrap_it_probed_and_looks_the_name_up_no_more(tmp_path, monkeypatch):
    first, second = tmp_path / "first" / "bwrap", tmp_path / "second" / "bwrap"
    for each in (first, second):
        each.parent.mkdir()
        each.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(0)\n")
        each.chmod(0o700)
    monkeypatch.setattr(bubblewrap.sys, "platform", "linux")
    asked = iter([str(first), str(second), str(second)])
    monkeypatch.setattr(bubblewrap.shutil, "which", lambda name: next(asked) if name == "bwrap" else None)
    bubblewrap._probe.cache_clear()
    backend = Bubblewrap()
    try:
        assert backend.available().ok
        made = backend.wrap(["/usr/bin/true"], held(Net.deny()).validated())
    finally:
        bubblewrap._probe.cache_clear()
    assert made.argv[0] == os.path.realpath(first)


def test_the_forwarders_interpreter_is_bound_without_the_packages_installed_beside_it(tmp_path, monkeypatch):
    tree = tmp_path / "python"
    stdlib = tree / "lib" / "python3.99"
    for each in (tree / "bin", stdlib / "site-packages", stdlib / "lib-dynload"):
        each.mkdir(parents=True)
    (tree / "bin" / "python3.99").write_text("")
    monkeypatch.setattr(bubblewrap.sys, "executable", str(tree / "bin" / "python3.99"))
    monkeypatch.setattr(sysconfig, "get_path", lambda key, *more: str(stdlib))
    monkeypatch.setattr(sysconfig, "get_config_var", lambda key: None)
    args = arguments(held(Net.only(80)), "/usr/bin/true", relay=str(tmp_path))
    binds = [args[at + 1] for at, word in enumerate(args) if word == "--ro-bind"]
    real = os.path.realpath
    assert real(stdlib) in binds and real(tree / "bin" / "python3.99") in binds and real(tree) not in binds
    assert ("--tmpfs", real(stdlib / "site-packages")) in list(pairwise(args))


def test_a_policy_with_no_network_is_not_given_the_hosts_name_lookup_files(tmp_path):
    lookups = {"/etc/hosts", "/etc/resolv.conf"}
    none = arguments(held(Net.deny()), "/usr/bin/true")
    ports = arguments(held(Net.only(80)), "/usr/bin/true", relay=str(tmp_path))
    assert not lookups & set(none)
    assert {each for each in lookups if os.path.lexists(each)} <= set(ports)


def stage(tmp_path: Path) -> Path:
    """A writable tree with a directory ``ctl`` and a file ``notes`` in it, as a policy's one write root."""
    root = tmp_path / "stage"
    (root / "ctl").mkdir(parents=True)
    (root / "ctl" / "config").write_text("kept")
    (root / "notes").write_text("kept")
    return root


def protecting(root: Path, **names: tuple[str, ...]) -> list[str]:
    """The bwrap options of a policy that writes ``root`` and protects the paths ``names`` gives (``deny_read``, ``deny_write``)."""
    chosen = replace(held(Net.deny()), write=(str(root),), **{key: tuple(str(root / each) for each in value) for key, value in names.items()})
    return arguments(chosen.validated(), "/usr/bin/true")


def test_a_protected_name_directly_under_a_writable_tree_is_frozen_after_the_tree_is_bound_and_a_missing_one_is_held_by_the_null_device(tmp_path):
    root = stage(tmp_path)
    args = protecting(root, deny_write=("ctl", ".claude"))
    triples = [tuple(args[at:at + 3]) for at in range(len(args) - 2)]
    writable = triples.index(("--bind", str(root), str(root)))
    assert triples.index(("--ro-bind", str(root / "ctl"), str(root / "ctl"))) > writable
    assert triples.index(("--ro-bind", os.devnull, str(root / ".claude"))) > writable


def test_a_path_to_hide_is_covered_by_an_empty_read_only_tree_or_by_the_null_device(tmp_path):
    root = stage(tmp_path)
    args = protecting(root, deny_read=("ctl", "notes"))
    adjacent = list(pairwise(args))
    assert ("--tmpfs", str(root / "ctl")) in adjacent and ("--remount-ro", str(root / "ctl")) in adjacent
    assert ["--ro-bind", os.devnull, str(root / "notes")] == args[args.index(str(root / "notes")) - 2:][:3]
    assert str(root / "ctl" / "config") not in args


def test_a_protected_path_deeper_in_a_writable_tree_is_refused_because_the_directory_above_it_could_be_renamed(tmp_path):
    root = stage(tmp_path)
    with pytest.raises(PolicyError, match="directly under a writable tree"):
        protecting(root, deny_write=("ctl/config",))


def test_a_protected_path_in_no_writable_tree_adds_nothing(tmp_path):
    root = stage(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    chosen = replace(held(Net.deny()), write=(str(root),), deny_write=(str(elsewhere),)).validated()
    assert arguments(chosen, "/usr/bin/true") == arguments(replace(chosen, deny_write=()), "/usr/bin/true")


def test_the_files_of_the_hosts_etc_that_a_namespace_binds_are_exactly_the_declared_exposure(tmp_path):
    binds = {args[at + 1] for args in (arguments(held(net), "/usr/bin/true", relay=str(tmp_path)) for net in (Net.deny(), Net.only(80)))
             for at, word in enumerate(args) if word == "--ro-bind" and args[at + 1].startswith("/etc/")}
    assert binds <= set(bubblewrap.ACCOUNT_AND_HOST_FILES.paths)
    assert {each for each in ("/etc/passwd", "/etc/group", "/etc/localtime") if os.path.lexists(each)} <= binds
    assert not {"/etc/machine-id", "/var/lib/dbus/machine-id", "/sys/class/dmi/id"} & set(bubblewrap.SYSTEM_READ)


def test_the_marker_that_says_a_process_is_confined_is_bound_read_only():
    args = arguments(held(Net.deny()), "/usr/bin/true")
    assert ["--ro-bind", os.devnull, MARKER] == args[args.index(MARKER) - 2:][:3]


ATTEMPTS = r'''
import errno, json, os, sys
root = sys.argv[1]
def tried(call):
    try:
        call()
        return "done"
    except OSError as error:
        return errno.errorcode[error.errno]
def append(path):
    with open(path, "a") as out:
        out.write("x")
print(json.dumps({"append": tried(lambda: append(root + "/ctl/config")), "create": tried(lambda: append(root + "/ctl/new")),
                  "rename": tried(lambda: os.rename(root + "/ctl", root + "/other")), "remove": tried(lambda: os.remove(root + "/ctl/config")),
                  "claude": tried(lambda: os.mkdir(root + "/.claude")), "notes": tried(lambda: append(root + "/notes")),
                  "read": tried(lambda: open(root + "/ctl/config").read())}))
'''


def test_a_process_cannot_write_create_rename_or_remove_under_a_protected_name_and_works_in_the_rest_of_the_tree(bwrap, tmp_path):
    root = stage(tmp_path)
    chosen = replace(held(Net.deny()), write=(str(root),), deny_write=(str(root / "ctl"), str(root / ".claude")))
    done = sandbox.run([os.path.realpath(sys.executable), "-c", ATTEMPTS, str(root)], chosen, via=bwrap, diagnose="never")
    seen = json.loads(done.stdout)
    assert all(seen[key] != "done" for key in ("append", "create", "rename", "remove", "claude")), seen
    assert seen["notes"] == "done" and seen["read"] == "done"
    assert (root / "ctl" / "config").read_text() == "kept" and (root / "other").exists() is False


def test_a_process_in_the_namespace_knows_it_is_confined_and_the_parent_does_not(bwrap):
    module = Path(confinement.__file__).resolve()
    code = ("import importlib.util, json, sys; spec = importlib.util.spec_from_file_location('marker', sys.argv[1]); "
            "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print(json.dumps(mod.confined()))")
    chosen = replace(held(Net.deny()), read=(*runtime_reads(), str(module.parent)))
    done = sandbox.run([os.path.realpath(sys.executable), "-c", code, str(module)], chosen, via=bwrap, diagnose="never")
    assert sandbox.confined() is False
    assert json.loads(done.stdout) is True, done.stderr
