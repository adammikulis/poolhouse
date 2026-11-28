"""The proof: before the harness starts, the launcher shows that the confinement holds, on this computer, now.

`run` starts the probe inside the exact policy the harness will get, with the interpreter in isolated mode and an empty working directory,
lets it try the twenty-four attempts of design section 10.1, and judges every line against what the parent knows and the probe cannot:
canaries in a fresh directory outside every grant, listeners on loopback, a unix socket, a bystander process that carries a marker, and the
machine's own identifiers read by the parent. A control that fails is a deviation too, so a profile that refuses everything cannot pass. A
deviation raises `IsolationFailed` naming the first attempt that got through, and nothing else starts: no flag, variable or argument skips it.
An attempt the probe did not answer in the time it is given shows nothing either way: nothing starts, and the message says it did not answer in
time rather than that it was allowed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from poolhouse import activity, home, procscan
from poolhouse.coding_workspace_state import CONTROL_PREFIXES
from poolhouse.isolation import probe, toolchain
from poolhouse.isolation.capability import capability, drop_cache, lan_address
from poolhouse.isolation.environment import allowed
from poolhouse.isolation.profile import Profile
from poolhouse.isolation.refusals import IsolationFailed
from poolhouse.isolation.serving import IDLE_SECONDS
from poolhouse.platform import start_process
from poolhouse.sandbox import Limits, bubblewrap, confinement, run as run_sandboxed, seatbelt_open
from poolhouse.sandbox.backend import Backend, Exposure
from poolhouse.sandbox.io import IO
from poolhouse.sandbox.run import HOOKS

__all__ = ["ATTEMPTS", "BUDGET_SECONDS", "WAIT_SECONDS", "Line", "Proof", "Services", "run"]

BUDGET_SECONDS = 5.0
"""The proof's share of a run's fixed cost, which is budgeted (AGENTS.md section 5, "A run's fixed cost is budgeted": over 5 s of fixed overhead is a
defect), shared by its 24 attempts (docs/design-isolated-launcher.md, 17). It is what a proof is expected to take, not how long one is waited
for (`WAIT_SECONDS`)."""
WAIT_SECONDS = IDLE_SECONDS
"""How long the probe is waited for, and a command the parent reads identifiers with: a computer busy enough to need more than the expected cost
still starts its agent. It is the project's one request timeout."""
TOLERATED = frozenset({"__CF_USER_TEXT_ENCODING"})
"""A name macOS puts in the environment of every process at exec, whatever the launcher set."""
GUARANTEE = {"P01": "G1", "P02": "G1", "P03": "G2", "P04": "G3", "P05": "G1", "P06": "G1", "P07": "G4", "P08": "G4", "P09": "G4",
             "P10": "G5", "P11": "G6", "P12": "G6", "P13": "G6", "P14": "G7", "P15": "G10", "P16": "G9", "P17": "G8", "P18": "G12",
             "P19": "G8", "P20": "controls", "P21": "controls", "P22": "G13", "P23": "exposures", "P24": "I17"}
ATTEMPTS = tuple(GUARANTEE)
MUST = {"P01": "fail", "P02": "fail", "P03": "fail", "P04": "fail", "P05": "fail", "P06": "fail", "P07": "fail", "P08": "fail",
        "P09": "fail", "P10": "fail", "P11": "fail", "P12": "marker absent", "P13": "no read beyond the list", "P14": "fail",
        "P15": "fail", "P16": "none", "P17": "fail", "P18": "fail", "P19": "exactly the allow-list", "P20": "controls pass",
        "P21": "well-formed refusal", "P22": "none found", "P23": "inside the declared exposures", "P24": "still confined"}
"""What each attempt must show (10.1), in words for the record."""


@dataclass(frozen=True, slots=True)
class Services:
    """The services the launch started that the probe reaches as controls: the model bridge, the session bridge and the toolchains to run."""

    model_port: int
    session_socket: str = ""
    toolchains: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Line:
    """One attempt: what it was, what it had to show, what it showed and whether that is a pass."""

    id: str
    guarantee: str
    must: str
    observed: str
    ok: bool


@dataclass(frozen=True, slots=True)
class Proof:
    """The record of a proof that passed: every line, the profile digest, the operating system build and when it ran and for how long."""

    lines: tuple[Line, ...]
    digest: str
    os_build: str
    at: float
    seconds: float
    unchecked: tuple[str, ...] = ()
    """The attempts that could not be made on this computer (P08 where it has no address on a network): their lines say so and are no pass."""


@dataclass(slots=True)
class Canaries:
    """What the parent holds and the probe cannot know: a secret in a fresh directory, listeners, a bystander, the machine's identifiers."""

    directory: str
    secret: str
    marker: str
    working: str
    tcp: socket.socket
    udp: socket.socket
    unix: socket.socket
    lan: socket.socket | None
    bystander: subprocess.Popen[bytes]
    identifiers: dict[str, str]
    terminal: str
    program: str
    heard: dict[str, int] = field(default_factory=lambda: {"tcp": 0, "unix": 0, "lan": 0, "udp": 0})


def _listen(family: int, kind: int, where: object) -> socket.socket:
    sock = socket.socket(family, kind)
    sock.bind(where)
    if kind == socket.SOCK_STREAM:
        sock.listen(4)
    return sock


def _said(argv: list[str]) -> str:
    try:
        # waited for as long as the probe: identifiers the parent could not read would leave nothing to search the probe's answer for
        return subprocess.run(argv, capture_output=True, text=True, timeout=WAIT_SECONDS, check=False, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _names(directory: str) -> list[str]:
    try:
        return [each.name for each in Path(directory).iterdir()]
    except OSError:
        return []


def identifier_files() -> tuple[str, ...]:
    """The files of a Linux host that carry a machine identifier (P22)."""
    return ("/etc/machine-id", "/var/lib/dbus/machine-id", "/sys/class/dmi/id/product_uuid", "/sys/class/dmi/id/product_serial",
            "/sys/class/dmi/id/board_serial") if sys.platform.startswith("linux") else ()


def _identifiers() -> dict[str, str]:
    """The serial number, the hardware UUID and the network hardware addresses of this machine, by kind, as the unconfined parent reads
    them. They are held only to be searched for, never recorded and never printed."""
    found: dict[str, str] = {}
    if sys.platform == "darwin":
        said = _said(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]) + _said(["/sbin/ifconfig", "-a"])
        for kind, pattern in (("serial", r'"IOPlatformSerialNumber" = "([^"]+)"'), ("hardware-uuid", r'"IOPlatformUUID" = "([^"]+)"')):
            if match := re.search(pattern, said):
                found[kind] = match.group(1)
        found.update({f"address-{n}": mac for n, mac in enumerate(sorted(set(re.findall(r"ether ([0-9a-f:]{17})", said))))})
    for path in (*identifier_files(), *(f"/sys/class/net/{name}/address" for name in _names("/sys/class/net"))):
        with contextlib.suppress(OSError):
            text = Path(path).read_text().strip()
            if text and text != "00:00:00:00:00:00":
                found[path] = text
    return found


def _accept(sock: socket.socket, name: str, canaries: Canaries) -> None:
    sock.settimeout(0.2)
    while sock.fileno() >= 0:
        try:
            if sock.type == socket.SOCK_DGRAM:
                sock.recvfrom(16)
            else:
                sock.accept()[0].close()
            canaries.heard[name] += 1
        except TimeoutError:
            continue
        except OSError:
            return


@contextlib.contextmanager
def _canaries(lan: bool) -> Iterator[Canaries]:
    # in the account's own temporary folder, which every box is refused, wherever the launcher's `TMPDIR` points
    root = os.path.realpath(tempfile.mkdtemp(prefix="pp-proof-", dir=toolchain.user_temporary() or tempfile.gettempdir()))
    secret, working = secrets.token_hex(16), str(Path(root) / "work")
    Path(working).mkdir()
    Path(root, "canary").write_text(secret)
    address = lan_address() if lan else ""
    made = [_listen(socket.AF_INET, socket.SOCK_STREAM, ("127.0.0.1", 0)), _listen(socket.AF_INET, socket.SOCK_DGRAM, ("127.0.0.1", 0)),
            _listen(socket.AF_UNIX, socket.SOCK_STREAM, str(Path(root) / "unix")),
            _listen(socket.AF_INET, socket.SOCK_STREAM, (address, 0)) if address else None]
    marker = secrets.token_hex(8)
    # it reads a pipe nothing is written to, so it runs however long the probe takes and ends with the parent: a bystander that had ended
    # would be readable as a signal's target and unreadable as a holder of arguments
    bystander = start_process(["/bin/cat"], env={"POOLHOUSE_PROOF_MARKER": marker}, stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    program = str(Path(root) / "program")
    shutil.copyfile(shutil.which("true") or "/usr/bin/true", program)
    Path(program).chmod(0o700)
    master, slave = os.openpty()
    held = Canaries(root, secret, marker, working, made[0], made[1], made[2], made[3], bystander, _identifiers(), os.ttyname(slave), program)
    for sock, name in zip(made, ("tcp", "udp", "unix", "lan"), strict=True):
        if sock is not None:
            threading.Thread(target=_accept, args=(sock, name, held), daemon=True).start()
    try:
        yield held
    finally:
        os.close(master)
        os.close(slave)
        for sock in made:
            if sock is not None:
                sock.close()
        bystander.kill()
        bystander.wait()
        bystander.stdin.close()
        for name in _names(root):
            with contextlib.suppress(OSError):
                (Path(root) / name).unlink()
        for leftover in (working, root):
            with contextlib.suppress(OSError):
                Path(leftover).rmdir()


def _variants() -> list[str]:
    """Each control name in the spellings a sloppy comparison would miss: as given, upper, swapped case and title case."""
    return list(dict.fromkeys(v for name in CONTROL_PREFIXES for v in (name, name.upper(), name.swapcase(), name.title())))


def _exposed() -> tuple[Exposure, ...]:
    """The exposures declared for the boxes this computer builds."""
    return (*seatbelt_open.EXPOSURES, *bubblewrap.EXPOSURES)


def _plan(profile: Profile, services: Services, held: Canaries) -> dict[str, object]:
    exposures = _exposed()
    names = _said(["/usr/sbin/sysctl", "-aN"]).split() if sys.platform == "darwin" else []
    lan = held.lan.getsockname() if held.lan else None
    return {"digest": profile.digest, "canary": str(Path(held.directory) / "canary"), "canary_dir": held.directory,
            "person_home": str(home.user_home()), "stage": profile.stage, "home": profile.home, "control_variants": _variants(),
            "loopback_port": held.tcp.getsockname()[1], "udp_port": held.udp.getsockname()[1], "lan": list(lan) if lan else None,
            "unix_socket": held.unix.getsockname(), "model_port": services.model_port, "session_socket": services.session_socket,
            "open": profile.policy.open, "withheld": list(profile.policy.net.refused),
            "named_ports": sorted({services.model_port, *profile.policy.net.ports, *profile.policy.net.refused, *profile.policy.net.accept[:1],
                                   *profile.policy.net.accept[-1:]}),
            "launcher": os.getpid(), "bystander": held.bystander.pid, "marker": held.marker, "terminal": held.terminal, "program": held.program,
            "readable_file": os.path.realpath(sys.executable),
            "stage_controls": [name for name in CONTROL_PREFIXES if os.path.lexists(Path(profile.stage) / name)],
            "pids": [proc.pid for proc, _ in procscan.readable(["pid"]) if proc.pid not in (os.getpid(), held.bystander.pid)],
            "toolchains": list(services.toolchains), "sysctls": names,
            "files": [path for each in exposures for path in each.paths], "identifier_files": list(identifier_files()),
            "confinement_source": Path(confinement.__file__).read_text(encoding="utf-8")}


def _declared(record: Mapping[str, object]) -> list[str]:
    """What the probe could read that no exposure declares: system names outside the exact and prefix lists, and files outside the paths."""
    exposures = _exposed()
    exact = {name for each in exposures for name in each.sysctls}
    prefixes = tuple(prefix for each in exposures for prefix in each.sysctl_prefixes)
    paths = {path for each in exposures for path in each.paths}
    return [*(str(name) for name in record.get("sysctls", []) if name not in exact and not str(name).startswith(prefixes)),
            *(str(path) for path in record.get("files", []) if path not in paths)]


def _healthy(record: Mapping[str, object], writable: bool) -> bool:
    """The controls of P20 all passed: the stage and the home behave as the profile says, the toolchains run, the bridge answers its routes."""
    wanted = "wrote" if writable else "denied"
    refused = record.get("other model")
    return (record.get("stage") == wanted and record.get("home") == "wrote" and all(record.get("toolchains", [])) and record.get("health") == 200
            and record.get("props") == 404 and (refused == "denied" or (isinstance(refused, int) and refused >= 400)))


def _leaks(record: Mapping[str, object], held: Canaries, profile: Profile) -> list[str]:
    """The kinds of machine identifier found in what the probe obtained; the values never leave this function. An open box lists the network
    interfaces, their hardware addresses with them (exposure X3), as a program of this machine does."""
    text = str(record.get("data", "")).lower()
    return sorted(kind for kind, value in held.identifiers.items() if value.lower() in text and not (profile.policy.open and kind.startswith("address-")))


def _refusal(record: Mapping[str, object]) -> bool:
    try:
        reply = json.loads(str(record.get("reply", "")).splitlines()[0])
    except (ValueError, IndexError):
        return False
    return isinstance(reply, dict) and ("refused" in reply or "error" in reply)


def _heard(name: str, record: Mapping[str, object], held: Canaries) -> tuple[bool, str]:
    """The attempts whose answer the parent checks against what its own listeners heard."""
    state, kind = str(record.get("outcome", "")), {"P07": "tcp", "P08": "lan", "P09": "udp", "P10": "unix"}[name]
    return state == "denied" and held.heard[kind] == 0, f"{state}, {held.heard[kind]} heard"


def _judge(name: str, record: Mapping[str, object], profile: Profile, held: Canaries) -> tuple[bool, str]:
    """Whether attempt ``name`` showed what it must, and a short observation that holds no value the parent protects."""
    state = str(record.get("outcome", ""))
    if name in ("P01", "P05"):
        return state == "denied" and held.secret not in str(record.get("data", "")), state
    if name == "P08" and held.lan is None and not profile.policy.open:
        return True, "not checked: this computer has no address on a network to listen on"
    if name in ("P07", "P08", "P09", "P10"):
        return _heard(name, record, held)   # an open box's attempts reach none of the parent's listeners either

    if name == "P03":
        return state == "denied" and not (Path(held.directory) / "written").exists(), state
    if name == "P04":
        return state == "denied", f"{state}: {record.get('made', [])}"
    if name == "P12":
        return not record.get("readable") and not record.get("marker_found"), f"readable {record.get('readable', [])}"
    if name == "P13":
        near = sys.platform == "darwin" or int(record.get("listed", 0)) <= 4
        return record.get("reads") == 0 and near, f"{record.get('reads')} read(s) beyond the list, {record.get('listed')} listed"
    if name == "P16":
        return state == "none", state
    if name == "P19":
        names = set(record.get("names", [])) - TOLERATED
        inside = names == set(profile.policy.env) and names <= allowed(profile.harness, profile.interactive)
        return state == "listed" and inside and "POOLHOUSE_PROOF_MARKER" not in names, f"{len(names)} name(s)"
    if name == "P20":
        return _healthy(record, not profile.read_only), state
    if name == "P21":
        return state == "allowed" and _refusal(record), state
    if name == "P22":
        leaked = _leaks(record, held, profile)
        return not leaked, "absent" if not leaked else "found: " + ", ".join(kind.split("-")[0] for kind in leaked)
    if name == "P23":
        outside = _declared(record)
        return not outside, "inside" if not outside else f"outside: {outside[0]}"
    if name == "P24":
        return bool(record.get("confined")) and bool(record.get("confined_after")), "confined" if record.get("confined_after") else "not confined"
    return state == "denied", state


def _fail(profile: Profile, line: Line, *, unanswered: bool = False) -> IsolationFailed:
    """The deviation, reported to the sentinel and the activity record, with the capability cache dropped so the next call reads the machine."""
    drop_cache()
    fields: dict[str, object] = {"severity": "warning", "policy": "isolation", "attempt": line.id, "guarantee": line.guarantee,
                                 "digest": profile.digest}
    if HOOKS.events is not None:
        with contextlib.suppress(OSError, ValueError, RuntimeError):
            HOOKS.events("isolation.proof_failed", dict(fields))
    activity.record("isolation.proof", subject=line.id, outcome="unanswered" if unanswered else "failed",
                    meta={"guarantee": line.guarantee, "digest": profile.digest})
    return IsolationFailed(line.id, line.guarantee, profile.digest, line.observed, unanswered=unanswered)


def _records(text: str) -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    for each in text.splitlines():
        with contextlib.suppress(ValueError):
            row = json.loads(each)
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                found[row["id"]] = row
    return found


def run(profile: Profile, services: Services, *, via: Backend | None = None, budget: float = WAIT_SECONDS, lan: bool = True) -> Proof:
    """Prove ``profile``: run the probe inside it, wait up to ``budget`` seconds for it and judge every attempt. Returns the `Proof`, or raises
    `IsolationFailed` for the first attempt that showed more than the profile allows (a control that failed included), and when none did but the
    time ran out first, for the first attempt not answered, saying that. ``lan`` is whether the parent listens on this machine's own LAN address
    for P08; without it the probe tries a documentation-range address, as it does on a machine with none. It never leaves an attempt out."""
    began = time.monotonic()
    with _canaries(lan) as held:
        policy = replace(profile.policy, limits=Limits(wall_seconds=budget, output_bytes=4_000_000, processes=profile.policy.limits.processes))
        done = run_sandboxed([os.path.realpath(sys.executable), "-I", "-c", Path(probe.__file__).read_text(encoding="utf-8")], policy,
                             cwd=held.working, io=IO(input=json.dumps(_plan(profile, services, held)).encode()), via=via,
                             diagnose="never")
        seen = _records(done.stdout)
        lines = []
        for name in ATTEMPTS:
            if name not in seen or seen[name].get("outcome") in ("error", "cannot attempt"):
                lines.append(Line(name, GUARANTEE[name], MUST[name], str(seen.get(name, {}).get("outcome", "no answer in the budget")), False))
                continue
            ok, observed = _judge(name, seen[name], profile, held)
            lines.append(Line(name, GUARANTEE[name], MUST[name], observed, ok))
    first = next((each for each in lines if not each.ok), None)
    if first is None and seen.get("digest", {}).get("digest") != profile.digest:
        first = Line("digest", "profile", "the digest the parent computed", "another profile", False)
    if first is not None:
        # the attempts answer in order, so one that showed too much stands before any the time ran out on
        raise _fail(profile, first, unanswered=done.timed_out and first.id not in seen)
    return Proof(tuple(lines), profile.digest, capability().os_build, time.time(), round(time.monotonic() - began, 3),
                 tuple(each.id for each in lines if each.observed.startswith("not checked")))
