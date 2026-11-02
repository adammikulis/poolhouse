"""Linux bubblewrap namespaces, native availability and policy arguments."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import sysconfig
import threading
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from poolhouse.platform import start_process, terminate_process_group
from poolhouse.sandbox import netforward, netrelay
from poolhouse.sandbox.backend import NO_REFUSAL_LOG, Availability, Wrapped, program_of
from poolhouse.sandbox.policy import NetMode, Policy, PolicyError, checked_path

__all__ = ["FORWARDER", "SOCKETS", "Bubblewrap", "arguments", "forwarding"]

FORWARDER = "/run/poolhouse/netforward.py"
SOCKETS = "/run/poolhouse/net"
"""Where the confined process finds the forwarder of `poolhouse.sandbox.netforward` and the sockets of the parent's relay."""

SYSTEM_READ = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/ssl", "/etc/ld.so.cache",
               "/etc/resolv.conf", "/etc/hosts", "/etc/localtime", "/etc/passwd", "/etc/group")
NAME_LOOKUP = ("/etc/resolv.conf", "/etc/hosts")
"""The host's files for resolving names; a policy with no network resolves nothing and does not get them."""


def _interpreter() -> tuple[str, list[str], list[str]]:
    """The Python that runs the forwarder inside the namespace: it, and the trees it needs read-only (itself, its standard library and,
    for a shared build, its library), and the directories of installed packages inside those trees, which are hidden."""
    executable = os.path.realpath(sys.executable)
    trees = {executable, *(os.path.realpath(sysconfig.get_path(key)) for key in ("stdlib", "platstdlib"))}
    library = Path(sysconfig.get_config_var("LIBDIR") or "/") / (sysconfig.get_config_var("LDLIBRARY") or "")
    if sysconfig.get_config_var("Py_ENABLE_SHARED") and library.is_file():
        trees.add(os.path.realpath(library))
    hidden = [str(Path(tree) / "site-packages") for tree in sorted(trees) if (Path(tree) / "site-packages").is_dir()]
    return executable, sorted(trees), hidden


def forwarding(policy: Policy) -> list[str]:
    """The command that precedes the confined one when the policy names ports: the forwarder, which listens on those ports of the
    namespace's loopback and carries each connection to the parent's socket for it. Empty when the policy names none."""
    if policy.net.mode != NetMode.PORTS:
        return []
    return [_interpreter()[0], "-I", "-S", FORWARDER, SOCKETS, ",".join(map(str, policy.net.ports)), "--"]


def arguments(policy: Policy, program: str, relay: str = "") -> list[str]:
    """The bwrap options for ``policy``: a new namespace for everything, the network included (only the namespace's own loopback
    exists, and the ports a policy names are carried in from ``relay``, the parent's directory of sockets), no further user
    namespace and no capability, the allow-listed trees bound read-only or read-write, and a fresh /proc, /dev and /tmp."""
    if policy.deny_read or policy.deny_write:
        raise PolicyError("bubblewrap cannot enforce protected path exclusions; command was not run")
    if policy.net.mode == NetMode.LOOPBACK:
        raise PolicyError("net: bubblewrap cannot give a process the host's whole loopback without giving it the host's network; "
                          "name the ports with Net.only")
    if policy.net.mode == NetMode.PORTS and not relay:
        raise PolicyError("net: the ports of a policy are carried in from the parent's socket directory, and none was made")
    out = ["--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
           "--unshare-user", "--disable-userns", "--cap-drop", "ALL"]
    out += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]  # noqa: S108 - a mount point
    system = [p for p in SYSTEM_READ if os.path.lexists(p) and not (policy.net.mode == NetMode.DENY and p in NAME_LOOKUP)]
    trees, hidden = [*system, *policy.read, *policy.exec, program], []
    if policy.net.mode == NetMode.PORTS:
        _, needed, hidden = _interpreter()
        trees += needed
    seen: set[str] = set()
    for path in trees:
        if path not in seen:
            seen.add(path)
            out += ["--ro-bind", path, path]
    for path in hidden:
        out += ["--tmpfs", path]
    if policy.net.mode == NetMode.PORTS:
        out += ["--ro-bind", relay, SOCKETS, "--ro-bind", str(Path(netforward.__file__).resolve()), FORWARDER]
    for path in policy.write:
        out += ["--bind", path, path]
    if policy.cache:
        out += ["--bind", policy.cache, policy.cache]
    if policy.gpu:
        for path in ("/dev/dxg", "/dev/nvidiactl", "/dev/nvidia0", "/dev/nvidia-uvm",
                     "/dev/nvidia-uvm-tools", "/dev/dri"):
            if Path(path).exists():
                out += ["--dev-bind", path, path]
        for path in ("/usr/lib/wsl/lib", "/usr/lib/wsl/drivers"):
            if Path(path).is_dir():
                out += ["--ro-bind", path, path]
    for key, value in policy.env.items():
        out += ["--setenv", key, value]
    return out


_PROBE_LOCK = threading.Lock()


@lru_cache(maxsize=4)
def _probe(binary: str, identity: tuple, program: str) -> Availability:
    held = Policy("bubblewrap-probe", exec=(program,), env={}).validated()
    process = start_process([binary, *arguments(held, program), "--", program],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, env={})
    try:
        _, error = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        terminate_process_group(process, force=True)
        process.communicate()
        return Availability(False, "bubblewrap namespace probe timed out")
    if process.returncode:
        said = error.decode(errors="replace")[:300].strip()
        if "disable-userns" in said:
            return Availability(False, "the installed bubblewrap cannot stop a confined process from starting sandboxes of its own "
                                       "(it lacks --disable-userns, added in bubblewrap 0.8.0): " + said)
        return Availability(False, "bubblewrap namespace probe failed: " + said)
    return Availability(True)


class Bubblewrap:
    """Runs a command inside bubblewrap namespaces."""

    name = "bubblewrap"

    def __init__(self) -> None:
        self._relays: dict[str, netrelay.Relay] = {}
        self._lock = threading.Lock()
        self._binary = ""
        """The bubblewrap that `available` probed; `wrap` runs that one and never looks the name up again."""

    def available(self) -> Availability:
        if not sys.platform.startswith("linux"):
            return Availability(False, "bubblewrap runs on Linux only")
        found = shutil.which("bwrap")
        if not found:
            return Availability(False, "bwrap is not installed (apt install bubblewrap)")
        try:
            binary = checked_path(os.path.realpath(found), what="bubblewrap")
            program = checked_path(os.path.realpath("/usr/bin/true"), what="probe")
            stat = Path(binary).stat()
            namespace = Path("/proc/self/ns/user")
            identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                        stat.st_mode, os.geteuid(),
                        namespace.stat().st_ino if namespace.exists() else 0)
            with _PROBE_LOCK:
                state = _probe(binary, identity, program)
            self._binary = binary if state.ok else ""
            return state
        except (OSError, PolicyError) as exc:
            return Availability(False, str(exc))

    def wrap(self, argv: Sequence[str], policy: Policy) -> Wrapped:
        found = self._binary or self._lookup()
        program = checked_path(program_of(argv, policy.env.get("PATH", "")), what="command", link=True)
        if policy.net.mode != NetMode.PORTS:
            return Wrapped([found, *arguments(policy, program), "--", *argv])
        relay = netrelay.Relay(policy.net.ports)
        try:
            made = [found, *arguments(policy, program, relay.directory), "--", *forwarding(policy), *argv]
        except PolicyError:
            relay.close()
            raise
        tag = secrets.token_hex(6)
        with self._lock:
            self._relays[tag] = relay
        return Wrapped(made, tag=tag)

    @staticmethod
    def _lookup() -> str:
        found = shutil.which("bwrap")
        if not found:
            raise PolicyError("bwrap is not installed")
        return checked_path(os.path.realpath(found), what="bubblewrap")

    def watch(self, tag: str) -> None:
        return None

    def started(self, tag: str, pid: int) -> None:
        return None

    def finish(self, tag: str) -> None:
        """The run tagged ``tag`` has ended: stop its relay and remove the sockets."""
        with self._lock:
            relay = self._relays.pop(tag, None)
        if relay is not None:
            relay.close()

    def denials(self, tag: str, since: float) -> list[dict[str, str]] | None:
        return None

    def unreadable(self, tag: str) -> str:
        return NO_REFUSAL_LOG
