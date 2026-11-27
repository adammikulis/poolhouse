"""The probe: a standard-library program that tries to escape the confinement it runs in and reports what happened, one JSON line each.

The parent starts it inside the exact profile the harness will get, with the interpreter in isolated mode (``-I``) and an empty working
directory, so no module of the project can change it (docs/design-isolated-launcher.md, 10.1). It reads the plan the parent gives it
(canaries it cannot know the contents of, listeners, pids, names) and decides nothing: the parent reads every line and judges it. An
attempt the platform cannot make is reported as ``cannot attempt``, never skipped. The Linux branches are PREDICTED: no Linux device ran them.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import http.client
import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path

DARWIN = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")
CANNOT = {"outcome": "cannot attempt"}
PROFILER_SECONDS = 2.0
"""Inside the box ``system_profiler`` waits over five seconds for services it is refused and then prints nothing, longer than the whole proof is
expected to take: it is given this long, and what it has printed by then is searched."""
PLAN: dict = {}


def seen(call):
    """``("allowed", value)`` when ``call`` returned, ``("denied", errno name)`` when the system refused it."""
    try:
        return "allowed", call()
    except OSError as error:
        return "denied", errno.errorcode.get(error.errno or 0, type(error).__name__)


def read_bytes(path):
    return Path(path).read_bytes()[:64].decode("utf-8", "replace")


def create(path, kind):
    """Create ``path`` as a file or a directory; the system refuses or it exists."""
    if kind == "file":
        Path(path).open("x").close()
    else:
        Path(path).mkdir()


def remove(path):
    """Take away whatever `create` made at ``path``."""
    for undo in (Path.unlink, Path.rmdir):
        with contextlib.suppress(OSError):
            undo(Path(path))


def connect(host, port, kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.settimeout(1.0)
        sock.connect((host, port))
        return "connected"


def p01():
    state, data = seen(lambda: read_bytes(PLAN["canary"]))
    return {"outcome": state, "data": data if state == "allowed" else ""}


def p02():
    state, names = seen(lambda: list(Path(PLAN["person_home"]).iterdir()))
    return {"outcome": state, "listed": len(names) if state == "allowed" else 0}


def p03():
    where = [Path(PLAN["canary_dir"]) / "written", Path(PLAN["person_home"]) / "poolhouse-proof-written"]
    states = [seen(lambda path=path: create(path, "file"))[0] for path in where]
    for path in where:
        remove(path)
    return {"outcome": "allowed" if "allowed" in states else "denied", "states": states}


def p04():
    made = [name for name in PLAN["control_variants"]
            if "allowed" in [seen(lambda name=name, kind=kind: create(Path(PLAN["stage"]) / name, kind))[0] for kind in ("file", "directory")]]
    for name in made:
        remove(Path(PLAN["stage"]) / name)
    return {"outcome": "allowed" if made else "denied", "made": made}


def p05():
    link = Path(PLAN["stage"]) / "poolhouse-proof-link"
    seen(lambda: link.symlink_to(PLAN["canary"]))
    state, data = seen(lambda: read_bytes(link))
    remove(link)
    return {"outcome": state, "data": data if state == "allowed" else ""}


def p06():
    states = []
    for source in (PLAN["canary"], PLAN["readable_file"], *(Path(PLAN["stage"]) / name for name in PLAN["stage_controls"])):
        link = Path(PLAN["stage"]) / "poolhouse-proof-hardlink"
        states.append(seen(lambda source=source, link=link: os.link(source, link))[0])
        remove(link)
    return {"outcome": "allowed" if "allowed" in states else "denied", "states": states}


def p07():
    return {"outcome": seen(lambda: connect("127.0.0.1", PLAN["loopback_port"]))[0]}


def p08():
    host, port = PLAN["lan"] or ["192.0.2.1", 9]
    return {"outcome": seen(lambda: connect(host, port))[0]}


def refused(call):
    """``"denied"`` only when the sandbox refused ``call``: another error (a port already held) means the system took it up."""
    state, why = seen(call)
    return "denied" if state == "denied" and why in ("EPERM", "EACCES") else "allowed"


def p09():
    """No datagram leaves for a port nobody granted, and no address is bound or listened on: not on loopback, and not on the wildcard, for no port
    the system picks and for none the profile names."""
    def send():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"x", ("127.0.0.1", PLAN["udp_port"]))

    def bind(kind, host, port):
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, kind) as sock:
            sock.bind((host, port))

    def listen():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.listen(1)

    states = [seen(send)[0], refused(listen),
              *(refused(lambda kind=kind, host=host, port=port: bind(kind, host, port)) for port in (0, *PLAN["named_ports"])
                for host in ("127.0.0.1", "0.0.0.0", "::") for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM))]  # noqa: S104 - must be refused
    return {"outcome": "allowed" if "allowed" in states else "denied", "states": states}


def p10():
    def reach():
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(1.0)
            sock.connect(PLAN["unix_socket"])
            return "connected"

    return {"outcome": seen(reach)[0]}


def libc():
    return ctypes.CDLL(None, use_errno=True)


def ptrace(pid):
    if not (DARWIN or LINUX):
        return -1
    call = libc().ptrace
    call.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    return call(10 if DARWIN else 16, pid, None, None)


def p11():
    states = {}
    for name in ("launcher", "bystander"):
        states[name + "-signal"] = seen(lambda pid=PLAN[name]: os.kill(pid, 0))[0]
        states[name + "-trace"] = "denied" if ptrace(PLAN[name]) == -1 else "allowed"
    return {"outcome": "allowed" if "allowed" in states.values() else "denied", "states": states}


def arguments_of(pid):
    """The raw arguments and environment of ``pid`` as the system gives them to a process, or None."""
    if DARWIN:
        mib, size = (ctypes.c_int * 3)(1, 49, pid), ctypes.c_size_t(0)
        if libc().sysctl(mib, 3, None, ctypes.byref(size), None, 0):
            return None
        buffer = ctypes.create_string_buffer(size.value + 4096)
        size = ctypes.c_size_t(len(buffer))
        return None if libc().sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) else buffer.raw[:size.value]
    state, data = seen(lambda: Path(f"/proc/{pid}/environ").read_bytes())
    return data if state == "allowed" else None


def p12():
    readable, found = [], False
    for name in ("launcher", "bystander"):
        raw = arguments_of(PLAN[name])
        if raw:
            readable.append(name)
            found = found or PLAN["marker"].encode() in raw
    return {"outcome": "allowed" if readable else "denied", "readable": readable, "marker_found": found}


def proc_reads(pid):
    """Which facts of ``pid`` a Linux process could read besides the list."""
    wanted = (("working directory", Path(f"/proc/{pid}/cwd").readlink), ("open files", lambda: list(Path(f"/proc/{pid}/fd").iterdir())))
    return [label for label, call in wanted if seen(call)[0] == "allowed"]


def pid_reads(pid):
    """Which of the other facts of ``pid`` could be read besides what the process list of the platform shows."""
    out = ["arguments"] if arguments_of(pid) else []
    if DARWIN:
        proc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        for flavor, size, label in ((9, 2352, "working directory"), (1, 4096, "open files")):
            if proc.proc_pidinfo(pid, flavor, 0, ctypes.create_string_buffer(size), size) > 0:
                out.append(label)
    elif LINUX:
        out += proc_reads(pid)
    return out


def p13():
    listed, reads = 0, {}
    for pid in PLAN["pids"]:
        if DARWIN:
            size = ctypes.c_size_t(0)
            mib = (ctypes.c_int * 4)(1, 14, 1, pid)
            listed += libc().sysctl(mib, 4, None, ctypes.byref(size), None, 0) == 0 and size.value > 0
        found = pid_reads(pid)
        if found:
            reads[pid] = found
    if LINUX:
        listed = len([each for each in Path("/proc").iterdir() if each.name.isdigit()])
    return {"outcome": "allowed" if reads else "denied", "listed": int(listed), "reads": len(reads)}


def p14():
    states = [seen(lambda: os.close(os.open("/dev/tty", os.O_RDWR)))[0]]
    if PLAN["terminal"]:
        states.append(seen(lambda: os.close(os.open(PLAN["terminal"], os.O_RDWR)))[0])
    return {"outcome": "allowed" if "allowed" in states else "denied", "states": states}


def tool(argv):
    """True when the program ran and exited 0; False when it ran and failed or is not there."""
    try:
        return subprocess.run(argv, capture_output=True, check=False, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def p15():
    attempts = [["/bin/launchctl", "submit", "-l", "poolhouse.proof", "--", "/usr/bin/true"]] if DARWIN else \
        [["systemd-run", "--user", "/usr/bin/true"], ["crontab", "-l"]]
    if not any(Path(each[0]).exists() or "/" not in each[0] for each in attempts):
        return CANNOT
    return {"outcome": "allowed" if any(tool(each) for each in attempts) else "denied"}


def p16():
    if DARWIN:
        try:
            metal = ctypes.CDLL("/System/Library/Frameworks/Metal.framework/Metal")
            metal.MTLCreateSystemDefaultDevice.restype = ctypes.c_void_p
            return {"outcome": "allowed" if metal.MTLCreateSystemDefaultDevice() else "none"}
        except OSError:
            return {"outcome": "none"}
    opened = [path for path in ("/dev/dxg", "/dev/nvidiactl", "/dev/dri/card0", "/dev/dri/renderD128")
              if seen(lambda path=path: os.close(os.open(path, os.O_RDWR)))[0] == "allowed"]
    return {"outcome": "allowed" if opened else "none"}


def p17():
    where = [Path(PLAN["person_home"]) / part for part in (".ssh", ".aws", ".gnupg", "Library/Keychains")]
    states = [seen(lambda path=path: list(path.iterdir()))[0] for path in where]
    keychain = DARWIN and tool(["/usr/bin/security", "list-keychains"])
    return {"outcome": "allowed" if "allowed" in states or keychain else "denied", "states": states, "keychain": bool(keychain)}


def p18():
    nested = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"] if DARWIN else ["unshare", "--user", "true"]
    return {"outcome": "allowed" if tool(nested) or tool([PLAN["program"]]) else "denied"}


def p19():
    return {"outcome": "listed", "names": sorted(os.environ)}


def roundtrip(path):
    """Write a file and read it back."""
    Path(path).write_text("control")
    return Path(path).read_text()


def request(method, path, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", PLAN["model_port"])
    try:
        connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
        return connection.getresponse().status
    finally:
        connection.close()


def p20():
    out = {}
    for label, where in (("stage", PLAN["stage"]), ("home", PLAN["home"])):
        path = Path(where) / "poolhouse-proof-control"
        state, back = seen(lambda path=path: roundtrip(path))
        out[label] = "wrote" if back == "control" else state
        remove(path)
    out["toolchains"] = [tool([exe, "--version"]) for exe in PLAN["toolchains"]]
    for label, call in (("health", lambda: request("GET", "/health")), ("props", lambda: request("GET", "/props")),
                        ("other model", lambda: request("POST", "/v1/messages", json.dumps({"model": "poolhouse-proof-other-model"})))):
        state, status = seen(call)
        out[label] = status if state == "allowed" else state
    return {"outcome": "controls", **out}


def p21():
    if not PLAN["session_socket"]:
        return CANNOT

    def ask():
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect(PLAN["session_socket"])
            sock.sendall(b'{"verb": "poolhouse-proof-unknown-frame"}\n')
            return sock.recv(1024).decode("utf-8", "replace")

    state, reply = seen(ask)
    return {"outcome": state, "reply": reply if state == "allowed" else ""}


def said_by(argv, wait=None):
    """What ``argv`` printed, within ``wait`` seconds when one is given; with none it is waited for, inside the time the whole probe is given."""
    try:
        return subprocess.run(argv, capture_output=True, timeout=wait, check=False, stdin=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as cut:
        return (cut.stdout or b"").decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def p22():
    commands = [(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], None),
                (["/usr/sbin/system_profiler", "SPHardwareDataType"], PROFILER_SECONDS), (["/sbin/ifconfig", "-a"], None)] if DARWIN else []
    said = [said_by(argv, wait) for argv, wait in commands]
    if DARWIN:
        buffer = (ctypes.c_char * 16)()
        if libc().gethostuuid(buffer, (ctypes.c_long * 2)(1, 0)) == 0:
            said.append(str(uuid.UUID(bytes=bytes(buffer))))
    for path in PLAN["identifier_files"]:
        state, text = seen(lambda path=path: Path(path).read_bytes()[:4096].decode("utf-8", "replace"))
        said.append(text if state == "allowed" else "")
    return {"outcome": "obtained", "data": "\n".join(said)}


def p23():
    readable = []
    if DARWIN:
        size = ctypes.c_size_t()
        readable = [name for name in PLAN["sysctls"] if libc().sysctlbyname(name.encode(), None, ctypes.byref(size), None, 0) == 0]
    files = [path for path in PLAN["files"] if seen(lambda path=path: Path(path).read_bytes()[:1])[0] == "allowed"]
    return {"outcome": "listed", "sysctls": readable, "files": files}


def p24():
    namespace: dict = {"__name__": "confinement"}
    exec(compile(PLAN["confinement_source"], "confinement", "exec"), namespace)  # noqa: S102 - the module of the library that asks the system
    before = namespace["confined"]()
    markers = [name for name in ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE") if os.environ.pop(name, None) is not None]
    saved = os.dup(1)
    try:
        master, slave = os.openpty()
        for each in (0, 1, 2):
            os.dup2(slave, each)
        os.write(master, b"yes\n")
    except OSError:
        pass
    finally:
        after = namespace["confined"]()
        os.dup2(saved, 1)
    return {"outcome": "listed", "confined": bool(before), "confined_after": bool(after), "markers_dropped": markers}


ATTEMPTS = [("P01", p01), ("P02", p02), ("P03", p03), ("P04", p04), ("P05", p05), ("P06", p06), ("P07", p07), ("P08", p08), ("P09", p09),
            ("P10", p10), ("P11", p11), ("P12", p12), ("P13", p13), ("P14", p14), ("P15", p15), ("P16", p16), ("P17", p17), ("P18", p18),
            ("P19", p19), ("P20", p20), ("P21", p21), ("P22", p22), ("P23", p23), ("P24", p24)]


def run_attempts(plan_text):
    PLAN.update(json.loads(plan_text))
    sys.stdout.write(json.dumps({"id": "digest", "digest": PLAN["digest"]}) + "\n")
    for name, attempt in ATTEMPTS:
        try:
            record = attempt()
        except (OSError, ValueError, KeyError, RuntimeError, AttributeError) as error:
            record = {"outcome": "error", "error": type(error).__name__}
        sys.stdout.write(json.dumps({"id": name, **record}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run_attempts(sys.stdin.read())
