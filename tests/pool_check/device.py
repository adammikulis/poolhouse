"""The devices of a pool check: the first device and the Linux containers, each its own node, name, state directory and session.

Both kinds answer the same calls. The Mac device runs the node and every command as a subprocess with its own temporary
``POOLHOUSE_HOME`` and its network on loopback only (``POOLHOUSE_NODE_LAN=off``, ``--listen 127.0.0.1:PORT``, no beacon, no multicast).
A Linux device is a container: its commands are `docker exec`, its node listens inside the container's network namespace
and the first device reaches it at the port Docker publishes on the Mac's loopback address. A Linux device reaches the first device at
`host.docker.internal`, which Docker Desktop answers with the Mac's loopback listener, and another Linux device at its container name.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath

from pool_check import dock
from pool_check.driver_host import Driver
from pool_check.wire import PRESENCE

ROOT = dock.ROOT
BOARD = "demo"
SYNC_MS = "400"
RIG = "/rig"
STATE = "/pc/node"
CONTAINER_NODE = "/usr/local/bin/poolhouse-node"
AGENT_MARKERS = ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", "POOLHOUSE_WORKSPACE_TOKEN",
                 "POOLHOUSE_WORKSPACE_DENYLIST", "POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_BOARD", "POOLHOUSE_HOME")


ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - a container listens on every interface of its own network namespace, a CI runner's node on its own
PLATFORM_NAME = {"darwin": "mac", "win32": "windows"}.get(sys.platform, "linux")
"""What the device the rig runs on is called in reports."""


class Refused(RuntimeError):
    """The node answered with an error: ``code`` is its word (denied, invalid, quota ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def free_port() -> int:
    """A TCP port nothing listens on, found on loopback."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@dataclass
class Device:
    """One device of the pool; subclasses say how a command runs on it and how another device reaches it."""

    name: str
    port: int
    token: str = ""
    session: str = ""
    admin_token: str = ""
    admin_name: str = ""
    extra: list[str] = field(default_factory=list)
    driver: Driver | None = field(default=None, repr=False)

    state_text = ""
    build_args: list[str] = field(default_factory=list)

    def run(self, argv: list[str], env: dict[str, str] | None = None, timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
        raise NotImplementedError

    def driver_argv(self) -> list[str]:
        """The command that starts `driver.py` on a device whose commands cannot share a process lineage any other way."""
        raise NotImplementedError

    def driven(self, argv: list[str], env: dict[str, str], timeout: float, check: bool) -> subprocess.CompletedProcess[str]:
        """``argv`` run as a child of this device's driver (driver.py says why), which is started on first use."""
        if self.driver is None:
            self.driver = Driver(self.driver_argv(), dock.account_environment())
        done = self.driver.run(argv, env, timeout)
        if check and done.returncode:
            raise RuntimeError(f"{self.name}: {' '.join(argv)} exited {done.returncode}:\n{done.stdout[-1500:]}\n{done.stderr[-1500:]}")
        return done

    def close(self) -> None:
        """End what the rig started on this device besides its node: the driver."""
        if self.driver is not None:
            self.driver.close()
            self.driver = None

    def wire(self, *argv: str, check: bool = True) -> dict:
        """One `wire.py` command on this device; its JSON envelope."""
        done = self.run(self.script("wire.py", *argv), check=False)
        try:
            out = json.loads(done.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            raise RuntimeError(f"{self.name}: wire {' '.join(argv)} printed no JSON (exit {done.returncode}):\n{done.stdout[-1500:]}\n{done.stderr[-1500:]}") from None
        if check and not out.get("ok"):
            raise Refused(out.get("code", "error"), out.get("error", ""))
        return out

    def script(self, name: str, *argv: str) -> list[str]:
        """The command line that runs one of the rig's helper scripts (tests/pool_check/NAME) on this device."""
        raise NotImplementedError

    def interpreter(self) -> list[str]:
        """The Python that runs the poolhouse package on this device."""
        raise NotImplementedError

    can_partition = False

    system_name = ""
    """What `platform.system()` says on this device: the tiny test file sent to it asserts it."""

    def node_binary_text(self) -> str:
        """The path of the node binary on this device."""
        raise NotImplementedError

    def listen_text(self) -> str:
        """The address the node of this device listens on."""
        raise NotImplementedError

    def wipe_state(self) -> None:
        """Delete everything this device's node kept (the node is stopped)."""
        raise NotImplementedError

    def delete_files(self, *names: str) -> None:
        """Delete files of the node's state directory by name or glob (what makes a device a new device on its next start)."""
        raise NotImplementedError

    def delete_tree(self, name: str) -> None:
        """Delete the directory ``name`` of the node's state directory."""
        raise NotImplementedError

    def prepare_checkout(self) -> None:
        """Make the checkout the test farm runs in a git checkout (it is a copy of the tree in a container)."""

    def test_python(self) -> str:
        """The Python 3.13 the device runs tests with."""
        raise NotImplementedError

    def partition(self, world) -> None:
        raise NotImplementedError

    def heal(self, world) -> None:
        raise NotImplementedError

    def root_of_checkout(self) -> PurePath:
        """The checkout of this tree on this device (the one `scripts/test_on.py` is in), as that device spells paths."""
        raise NotImplementedError

    def session_env(self) -> dict[str, str]:
        """The environment of a command run as this device's session on the demo board."""
        return {"POOLHOUSE_BOARD": BOARD, "POOLHOUSE_WORKSPACE_TOKEN": self.token}

    def probe(self, host: str, port: int, *requests: dict, expect: str = "") -> dict:
        """Speak the protocol members speak to ``host:port`` as this device (its own certificate); the replies or the error."""
        argv = self.script("member_probe.py", "--state", self.state_text, "--host", host, "--port", str(port), *(["--expect", expect] if expect else []), *(json.dumps(r) for r in requests))
        return json.loads(self.run(argv).stdout.strip().splitlines()[-1])

    def try_call(self, method: str, board: str = "", token: str | None = None, **params) -> dict:
        """The raw envelope of a local API call: ``ok`` and ``result``, or ``code`` and ``error``."""
        use = self.token if token is None else token
        return self.wire("call", "--state", self.state_text, method, "--board", board, "--token", use, "--params", json.dumps(params), check=False)

    def call(self, method: str, board: str = "", token: str | None = None, **params):
        """A local API call as this device's session; `Refused` when the node refuses."""
        out = self.try_call(method, board, token, **params)
        if not out["ok"]:
            raise Refused(out.get("code", "error"), out.get("error", ""))
        return out["result"]

    def start_node(self, *more: str) -> dict:
        raise NotImplementedError

    def stop_node(self) -> bool:
        return bool(self.wire("stop", "--state", self.state_text)["result"]["stopped"])

    def kill_node(self) -> int:
        """SIGKILL the node process (not its supervisor): the crash the supervisor must recover from; the pid killed."""
        raise NotImplementedError

    def hello(self) -> dict:
        return self.call("hello")

    def pool(self) -> dict:
        """`pool_status` of this device."""
        return self.call("pool_status")

    def register(self, session: str) -> None:
        """A session on the demo board, as a person's agent here would register."""
        got = self.call("register", BOARD, "", model="claude-sonnet-5-5", harness="claude-code", session=session)
        self.token, self.session = got["token"], got["name"]

    def admin_session(self) -> tuple[str, str]:
        """This device's session on the `pool` board, the one node_join uses (registered on first use): its token and its name."""
        if not self.admin_token:
            got = self.call("register", "pool", "", model="claude-sonnet-5-5", harness="poolhouse-pool", session="pool-check")
            self.admin_token, self.admin_name = got["token"], got["name"]
        return self.admin_token, self.admin_name

    def bare_session(self, board: str, session: str) -> tuple[str, str]:
        """A further session on ``board`` that no person has granted anything: its token and its name."""
        got = self.call("register", board, "", model="claude-sonnet-5-5", harness="poolhouse-pool", session=session)
        return got["token"], got["name"]

    def admin(self, method: str, **params):
        """A pool call (join, policy, remove, sync) as this device's session on the `pool` board; the node allows it only under a grant."""
        return self.call(method, "", self.admin_session()[0], **params)

    def certificate(self) -> str:
        """The device's certificate (hex DER) as its node kept it in its state directory: what an admin's admit of this device names."""
        return str(self.wire("cert", "--state", self.state_text)["result"]["cert"])

    def post(self, body: str, to: str = "#general", kind: str = "note") -> dict:
        return self.call("post", BOARD, kind="message", fields={"type": kind, "to": to, "body": body, "subject": ""})

    def post_many(self, count: int, size: int, prefix: str) -> None:
        """Write ``count`` entries of about ``size`` bytes in one go."""
        self.wire("post-many", "--state", self.state_text, "--board", BOARD, "--token", self.token, "--count", str(count), "--size", str(size), "--prefix", prefix)

    def message_count(self) -> int:
        """How many messages this device's board holds, its own and the others' (the node's own count, not a page read)."""
        return int(self.call("status", BOARD, self.token)["board"]["entries"].get("message", 0))

    def bodies(self, kind: str = "message", limit: int = 500, **filters) -> list[str]:
        """The text of every message this device's board shows (its own and the replicas of the others')."""
        got = self.call("read", BOARD, kind=kind, limit=limit, **filters)
        return [str(e["fields"].get("body", "")) for e in got["entries"]]

    def members(self) -> list[dict]:
        return self.pool()["members"]

    def others(self) -> list[dict]:
        """The active members other than this device."""
        return [m for m in self.members() if m.get("status") == "active" and not m.get("self")]

    def member(self, fingerprint: str) -> dict | None:
        return next((m for m in self.members() if m["fingerprint"] == fingerprint), None)

    @property
    def fingerprint(self) -> str:
        return str(self.pool()["fingerprint"])

    def reach(self, other: Device) -> tuple[str, int]:
        """The host and port ``other`` dials to reach this device."""
        raise NotImplementedError


@dataclass
class LocalDevice(Device):
    """The Mac this runs on, as a device: its own node in a temporary state, its network on loopback only."""

    binary: Path = field(default_factory=Path)
    root: Path = field(default_factory=Path)
    listen_all: bool = False
    """Listen on every interface, not only loopback: only a CI runner whose WSL device must reach it does (never the owner's own machine)."""

    @classmethod
    def make(cls, binary: Path, name: str = "") -> LocalDevice:
        root = Path(tempfile.mkdtemp(prefix="pcm", dir="/tmp" if Path("/tmp").is_dir() else None))
        name = name or PLATFORM_NAME
        device = cls(name=name, port=free_port(), binary=binary, root=root)
        device.state_text = str(root / "node")
        return device

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if k not in AGENT_MARKERS}
        return {**base, "PYTHONPATH": str(ROOT / "src"), "POOLHOUSE_HOME": str(self.root), "POOLHOUSE_NODE_LAN": "off", **(extra or {})}

    def run(self, argv, env=None, timeout=120, check=True):
        done = subprocess.run(argv, env=self.env(env), cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
        if check and done.returncode:
            raise RuntimeError(f"{self.name}: {' '.join(argv)} exited {done.returncode}:\n{done.stdout[-1500:]}\n{done.stderr[-1500:]}")
        return done

    def script(self, name, *argv):
        return [sys.executable, str(ROOT / "tests" / "pool_check" / name), *argv]

    def interpreter(self):
        return [sys.executable]

    def root_of_checkout(self):
        return ROOT

    def test_python(self):
        return sys.executable

    def node_binary_text(self):
        return str(self.binary)

    system_name = platform.system()

    def listen_text(self):
        return f"{ALL_INTERFACES if self.listen_all else '127.0.0.1'}:{self.port}"

    def wipe_state(self):
        shutil.rmtree(self.root / "node", ignore_errors=True)
        for leftover in self.root.iterdir():
            shutil.rmtree(leftover, ignore_errors=True) if leftover.is_dir() else leftover.unlink(missing_ok=True)

    def delete_files(self, *names):
        for pattern in names:
            for found in Path(self.state_text).glob(pattern):
                found.unlink(missing_ok=True)

    def delete_tree(self, name):
        shutil.rmtree(Path(self.state_text) / name, ignore_errors=True)

    def start_node(self, *more):
        args = ["--listen", self.listen_text(), "--sync-ms", SYNC_MS, *PRESENCE, *self.build_args, *self.extra, *more]
        flags = [f"--arg={a}" for a in args]
        return self.wire("start", "--state", self.state_text, "--binary", str(self.binary), *flags)["result"]

    def kill_node(self):
        pid = int(self.hello()["pid"])
        os.kill(pid, 9)
        return pid

    def reach(self, other):
        if isinstance(other, WslHostedDevice):
            return (other.windows_host(), self.port)
        return ("host.docker.internal", self.port) if isinstance(other, ContainerDevice) else ("127.0.0.1", self.port)

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@dataclass
class ContainerDevice(Device):
    """A container of the Docker network: a different kernel, filesystem and network stack, with a node of its own."""

    box: dock.Box = None  # type: ignore[assignment]
    net: dock.Net = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.state_text = STATE

    def driver_argv(self):
        return ["docker", "exec", "-i", self.box.name, "python", f"{RIG}/pool_check/driver.py"]

    def run(self, argv, env=None, timeout=120, check=True):
        return self.driven(argv, {"POOLHOUSE_HOME": "/pc", "POOLHOUSE_NODE_LAN": "off", "PYTHONPATH": "", **(env or {})}, timeout, check)

    def script(self, name, *argv):
        return ["python", f"{RIG}/pool_check/{name}", *argv]

    def interpreter(self):
        return ["python"]

    def root_of_checkout(self):
        return Path("/opt/poolhouse")

    can_partition = True

    system_name = "Linux"

    def node_binary_text(self):
        return CONTAINER_NODE

    def listen_text(self):
        return f"{ALL_INTERFACES}:{self.port}"

    def test_python(self):
        return "/opt/venv/bin/python"

    def wipe_state(self):
        self.box.exec("sh", "-c", "rm -rf /pc/* /pc/.[a-z]*", check=False)
        self.box.exec("mkdir", "-p", "/pc")

    def delete_files(self, *names):
        self.box.exec("sh", "-c", "cd /pc/node && rm -f " + " ".join(names))

    def delete_tree(self, name):
        self.box.exec("rm", "-rf", f"{STATE}/{name}")

    def prepare_checkout(self):
        self.box.exec("sh", "-c", "cd /opt/poolhouse && git init -q . 2>/dev/null; true")

    def partition(self, world):
        self.box.disconnect(world.net)

    def heal(self, world):
        self.box.connect(world.net)

    def start_node(self, *more):
        args = ["--listen", f"0.0.0.0:{self.port}", "--sync-ms", SYNC_MS, *PRESENCE, *self.build_args, *self.extra, *more]
        flags = [f"--arg={a}" for a in args]
        return self.wire("start", "--state", STATE, "--binary", CONTAINER_NODE, *flags)["result"]

    def kill_node(self):
        pid = int(self.hello()["pid"])
        self.box.exec("python", "-c", f"import os; os.kill({pid}, 9)")
        return pid

    def reach(self, other):
        if isinstance(other, LocalDevice):
            return ("127.0.0.1", self.port)
        if isinstance(other, WslDevice):
            return ("host.docker.internal", self.port)  # out of the NAT to this machine, whose loopback Docker publishes the port on
        return (self.box.name, self.port)


class Undialable(RuntimeError):
    """Nothing can open a connection to this device: it is behind NAT."""


@dataclass
class WslDevice(ContainerDevice):
    """What can be reproduced of a WSL2 device: an Ubuntu userland whose /proc/version and osrelease say `microsoft-standard-WSL2`, with
    WSL_DISTRO_NAME set, on a network of its own behind NAT. It can dial out (to this machine's loopback, so to the first device and to every
    published container port) and nothing can dial in: no member, and not the first device, has a route to it."""

    def reach(self, other):
        raise Undialable(f"{self.name} is behind NAT: nothing can open a connection to it")


def wsl_argv(distro: str, checkout: str, argv: list[str], env: dict[str, str]) -> list[str]:
    """The command line that runs ``argv`` in WSL distribution ``distro`` from ``checkout`` with ``env`` set (wsl.exe passes no environment through)."""
    return ["wsl.exe", "-d", distro, "--cd", checkout, "--", "env", *(f"{k}={v}" for k, v in env.items()), *argv]


def windows_address(seen: str) -> str:
    """The IPv4 address of Windows in what WSL printed: the gateway of the default route, else the nameserver WSL points at Windows; an error showing ``seen`` when neither is there."""
    route, _, resolv = seen.partition("---")
    for pattern, text in ((r"\bvia\s+(\d{1,3}(?:\.\d{1,3}){3})\b", route), (r"^nameserver\s+(\d{1,3}(?:\.\d{1,3}){3})\s*$", resolv)):
        if found := re.search(pattern, text, re.MULTILINE):
            return found.group(1)
    raise RuntimeError(f"WSL shows no address for Windows: {seen.strip()[:600]!r}")


@dataclass
class WslHostedDevice(Device):
    """A real WSL2 distribution of the Windows machine this runs on, as a device: its own node, state and Python inside the distribution.

    Windows reaches its node on `127.0.0.1` (WSL's localhost forwarding); it reaches Windows at the address of the Windows side of the virtual
    switch. Nothing here is emulated: this is only meaningful on a Windows machine with WSL, which is what the CI job has."""

    distro: str = "Ubuntu"
    checkout: str = "/root/poolhouse"
    home: str = "/tmp/pcw"
    binary_text: str = ""

    system_name = "Linux"

    def __post_init__(self) -> None:
        self.state_text = f"{self.home}/node"

    def driver_argv(self):
        return wsl_argv(self.distro, self.checkout, [*self.interpreter(), f"{self.checkout}/tests/pool_check/driver.py"], {"PYTHONPATH": f"{self.checkout}/src"})

    def run(self, argv, env=None, timeout=120, check=True):
        return self.driven(argv, {"POOLHOUSE_HOME": self.home, "POOLHOUSE_NODE_LAN": "off", "PYTHONPATH": f"{self.checkout}/src", **(env or {})}, timeout, check)

    def windows_host(self) -> str:
        """The address of Windows as WSL sees it: the default route's gateway (NAT mode)."""
        seen = self.run(["sh", "-c", "ip -4 route show default; echo '---'; cat /etc/resolv.conf"]).stdout
        return windows_address(seen)

    def script(self, name, *argv):
        return [f"{self.checkout}/.venv/bin/python", f"{self.checkout}/tests/pool_check/{name}", *argv]

    def interpreter(self):
        return [f"{self.checkout}/.venv/bin/python"]

    def root_of_checkout(self):
        return PurePosixPath(self.checkout)  # a path on the Linux side: never spelled with this machine's separators

    def test_python(self):
        return f"{self.checkout}/.venv/bin/python"

    def node_binary_text(self):
        return self.binary_text

    def listen_text(self):
        return f"{ALL_INTERFACES}:{self.port}"

    def start_node(self, *more):
        args = ["--listen", self.listen_text(), "--sync-ms", SYNC_MS, *PRESENCE, *self.build_args, *self.extra, *more]
        return self.wire("start", "--state", self.state_text, "--binary", self.binary_text, *[f"--arg={a}" for a in args])["result"]

    def kill_node(self):
        pid = int(self.hello()["pid"])
        self.run(["kill", "-9", str(pid)])
        return pid

    def wipe_state(self):
        self.run(["sh", "-c", f"rm -rf {self.home} && mkdir -p {self.home}"])

    def delete_files(self, *names):
        self.run(["sh", "-c", f"cd {self.state_text} && rm -f {' '.join(names)}"])

    def delete_tree(self, name):
        self.run(["rm", "-rf", f"{self.state_text}/{name}"])

    def prepare_checkout(self):
        self.run(["sh", "-c", "git rev-parse --git-dir >/dev/null 2>&1 || git init -q ."])

    def reach(self, other):
        if isinstance(other, LocalDevice):
            return ("127.0.0.1", self.port)
        raise Undialable(f"{self.name} is reached through Windows only")
