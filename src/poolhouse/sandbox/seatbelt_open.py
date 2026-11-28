"""The open box: a Seatbelt profile that allows by default and refuses named harms (`Policy.open`, macOS).

What a shell here can do it can: GPU compute, headless browsers, the installed programs and libraries, loopback, programs of its own. Refused,
each held by a red-team test (tests/test_redteam_open_box.py): writes outside ``write``; a ``private`` tree no granted tree inside it names
(the person's home, other volumes, other programs' temporary files, the credential stores); other processes' signals, memory, arguments and
files; a network beyond loopback, a refused port, every bind of an IP address (`isolation.ports` hands a socket in) and another program's unix
socket; ways out (launchd, LaunchServices, Apple events, preference writes, other terminals); camera, microphone, input, other device classes
and the hardware identifiers. System services are named rather than refused one by one: most act for their caller, outside any box (open a
program, fetch a URL, read the clipboard, unlock a keychain), and a list of the harmful ones could never be complete. An unnamed service is
also how a process here is known to be in a box of the engine's (`sandbox.confinement.BOX_MARKER`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from itertools import groupby
from pathlib import Path

from poolhouse.sandbox.backend import Exposure
from poolhouse.sandbox.policy import NetMode, Policy
from poolhouse.sandbox.seatbelt_text import (
    BATCH,
    SYMLINKS,
    ancestors,
    executable_links,
    filters,
    literal_rules,
    quote,
    trees,
)

__all__ = ["BROWSER_NAMES", "EXPOSURES", "IDENTIFIER_PROPERTIES", "IOKIT_CLASSES", "REFUSED_OPERATIONS", "SERVICES", "profile"]

EXPOSURES = (
    Exposure("X1", "macOS", "the name, pid, parent pid and user id of every process of the user"),
    Exposure("X2", "macOS", "every value the kernel serves by name, the machine class, host name, load and boot time among them, but another "
             "process's arguments and environment", sysctl_prefixes=("",)),
    Exposure("X3", "macOS", "the network interfaces, their addresses and hardware addresses among them"),
    Exposure("X4", "macOS", "a service of this computer that listens on loopback or on its network address, but a withheld port, and the names "
             "and metadata the system's own trees hold"))
"""What a process in an open box learns or reaches that the box does not hide (docs/design-isolated-launcher.md, 5.7): what any program of this
machine could. The serial number and the hardware UUID are not among them (G13)."""

SERVICES = ("com.apple.system.opendirectoryd.libinfo",   # who a user or a group is
            "com.apple.logd", "com.apple.system.logger", "com.apple.system.notification_center", "com.apple.distributed_notifications@Uv3",
            "com.apple.cfprefsd.daemon", "com.apple.cfprefsd.agent", "com.apple.SystemConfiguration.configd",
            "com.apple.MTLCompilerService")   # Metal compiles a shader there
"""The system services an open box may look up: what the system's libraries ask while a program starts, reads settings or computes on the GPU.
Not ``com.apple.bsd.dirhelper`` or the directory's membership service: without them a library is not told the account's temporary and cache
folders, which the box does not write, and takes the box's ``TMPDIR`` in their place (Apple's `xcrun` keeps its cache there, in silence)."""
BROWSER_NAMES = r'(global-name-regex #"^(org[.]chromium[.]Chromium|com[.]google[.]chrome[.]for[.]testing)[.]MachPortRendezvousServer[.][0-9]+$")'
"""The names a headless Chromium registers and its own processes look up, without which it ends at once. The browser's inner sandbox cannot start
inside a box (`sandbox_apply` is refused), so it runs with it off, inside this one."""
IOKIT_CLASSES = ("IOGPUDeviceUserClient", "AGXDeviceUserClient", "IOSurfaceRootUserClient", "RootDomainUserClient")
"""The device classes an open box may open: GPU compute, the surfaces it draws to, and power state."""
IDENTIFIER_PROPERTIES = ("IOPlatformSerialNumber", "IOPlatformUUID", "IOPlatformSystemSerialNumber", "serial-number", "mlb-serial-number",
                         "unique-chip-id", "IOMACAddress")
"""The registry properties that carry a serial number, the hardware UUID, the chip's own number or a network hardware address."""
SHARED = ("/", "/private", "/private/tmp", "/private/var", "/private/var/tmp", "/private/var/folders")
"""Folders every program of this computer uses: a grant of one, however it came, reaches no unix socket under it."""
PREFERENCES = ("kCFPreferencesAnyApplication", ".GlobalPreferences", "org.chromium.Chromium", "com.google.chrome.for.testing")
"""The preference domains every open box reads: the system-wide settings and a headless browser's own."""
REFUSED_OPERATIONS = ("appleevent-send", "lsopen", "job-creation", "nvram*", "system-kext*", "system-privilege",
                      "file-mount", "file-unmount", "device-camera", "device-microphone", "hid-control", "authorization-right-obtain",
                      "iokit-set-properties", "darwin-notification-post", "distributed-notification-post", "mach-priv-host-port")
"""Operations an open box never has: driving other programs, scheduling work, changing the system and using a person's devices."""
EXPRESSIONS = re.compile(r'\(regex #"\^/[^"]*\(/\|\$\)"\)')
"""The grouped expressions of `seatbelt_text.trees` in a profile."""


def _over(verb: str, operations: str, paths: Sequence[str], note: str = "") -> list[str]:
    """``(verb operations ...)`` over the trees ``paths``, at most `BATCH` filters to a rule."""
    held = trees(paths)
    return [f"({verb} {operations} {' '.join(held[at:at + BATCH])}{note})" for at in range(0, len(held), BATCH)]


def _literals(verb: str, operations: str, paths: Sequence[str], note: str) -> list[str]:
    """``(verb operations ...)`` for each of ``paths`` itself, at most `BATCH` filters to a rule."""
    return [f"({verb} {operations} {filters('literal', paths[at:at + BATCH])}{note})" for at in range(0, len(paths), BATCH)]


def _sockets(verb: str, paths: Sequence[str], note: str = "") -> list[str]:
    """A rule for each side of a unix socket under ``paths``."""
    out, held = [], trees(paths)
    for operation, side in (("network-outbound", "remote"), ("network-bind", "local"), ("network-inbound", "local")):
        for at in range(0, len(held), BATCH):
            where = " ".join(f"({side} unix-socket {each})" for each in held[at:at + BATCH])
            out.append(f"({verb} {operation} {where}{note})")
    return out


def _layer(granted: Sequence[str], private: Sequence[str]) -> list[tuple[list[str], list[str]]]:
    """``(hidden, shown)`` for each depth, the shallowest first: rules written in this order let the longest tree that holds a path decide it, a
    granted one before a private one of the same name."""
    marked = sorted({*((path, False) for path in private), *((path, True) for path in granted)}, key=lambda each: each[0].rstrip("/").count("/"))
    out = []
    for _, same in groupby(marked, key=lambda each: each[0].rstrip("/").count("/")):
        rows = list(same)
        out.append((sorted(path for path, shown in rows if not shown), sorted(path for path, shown in rows if shown)))
    return out


def _files(policy: Policy, program: str, note: str) -> list[str]:
    """What is read, run, written and reached as a unix socket: all of the system but the private trees to read and run, ``write`` alone to write."""
    readable = tuple(dict.fromkeys((*policy.read, *policy.exec, *policy.write, *([policy.cache] if policy.cache else []))))
    writable = tuple(dict.fromkeys((*policy.write, *([policy.cache] if policy.cache else []))))
    out = [f"(deny file-write* file-link{note})"]
    for hidden, shown in _layer(readable, policy.private):
        out += _over("deny", "file-read* process-exec", hidden, note)
        out += _over("allow", "file-read* process-exec", shown)
    for hidden, shown in _layer(writable, policy.private):
        out += _over("deny", "file-write* file-link", hidden, note)
        out += _sockets("deny", hidden, note)
        out += _over("allow", "file-write* file-link", shown)
        out += _sockets("allow", [each for each in shown if each not in SHARED])
    named = [*readable, *policy.read_files, *policy.read_metadata, *policy.read_dirs, *policy.unix_sockets, *policy.unix_namespaces]
    out += literal_rules("file-read-metadata", [*ancestors(named), *executable_links(program), *SYMLINKS, *policy.read_metadata, *policy.read_dirs,
                                                *policy.unix_sockets])
    if policy.unix_namespaces:
        out += _over("allow", "file-read-metadata", policy.unix_namespaces)
    if policy.read_files:
        out += literal_rules("file-read*", policy.read_files)
    if policy.read_dirs:
        out += literal_rules("file-read-data", policy.read_dirs)
    return out


def _devices(note: str) -> list[str]:
    """The null devices and the pseudo-terminals this box makes are written; no other terminal is read, written or controlled."""
    made = '(require-all (regex #"^/dev/ttys[0-9]+") (extension "com.apple.sandbox.pty"))'
    return [f'(deny file-ioctl (subpath "/dev"){note})',
            '(allow file-write* file-ioctl (literal "/dev/null") (literal "/dev/zero") (literal "/dev/dtracehelper") (subpath "/dev/fd"))',
            f'(deny file-read* file-write* file-ioctl (regex #"^/dev/tty") (regex #"^/dev/pty"){note})',
            "(allow pseudo-tty)",
            f'(allow file-read* file-write* file-ioctl (literal "/dev/ptmx") {made})']


def _network(policy: Policy, note: str) -> list[str]:
    """The granted unix sockets, then this machine's loopback: a refused port is refused after every rule that allows a socket, since a later
    allow of a socket's path can also answer for a loopback port an earlier rule refuses. No rule allows a bind of an IP address: Seatbelt's
    `localhost` matches the wildcard and the machine's network address too, so such a rule would allow a listener the network reaches. A socket
    on a window port is handed in, and the one inbound rule, for those ports, is followed by the refusal of every bind."""
    net, out = policy.net, []
    for endpoint in policy.unix_sockets:
        out.append(f"(allow network-outbound (remote unix-socket (literal {quote(endpoint)})))")
    for namespace in policy.unix_namespaces:
        out += _sockets("allow", [namespace])
        out.append(f"(allow file-write-create (require-all (subpath {quote(namespace)}) (vnode-type SOCKET)))")   # a bind makes the socket's file
    if net.mode == NetMode.LOOPBACK:
        out.append('(allow network-outbound (remote ip "localhost:*"))')
    out += [f'(allow network-outbound (remote ip "localhost:{port}"))' for port in net.ports]
    out += [f'(deny network-outbound (remote ip "localhost:{port}"){note})' for port in net.refused]
    out.append(f'(deny network-inbound (local ip "*:*"){note})')
    if net.accept:
        out.append("(allow network-inbound " + " ".join(f'(local tcp "localhost:{port}")' for port in net.accept) + ")")
    out.append(f'(deny network-bind (local ip "*:*"){note})')
    return out


def profile(policy: Policy, program: str, tag: str) -> str:
    """The profile text for the open ``policy`` running ``program``. Every path in it was checked and is quoted."""
    note = f' (with message {quote("poolhouse-sandbox:" + tag)})'
    domains = " ".join(f"(preference-domain {quote(each)})" for each in (*PREFERENCES, *policy.preferences))
    out = [
        "(version 1)",
        "(allow default)",
        *(f"(deny {operation}{note})" for operation in REFUSED_OPERATIONS),
        # other processes: only the box's own are signalled, traced or read
        f"(deny signal{note})", "(allow signal (target self) (target same-sandbox))",
        f"(deny process-info*{note})", "(allow process-info* (target self) (target same-sandbox))", "(allow process-info-listpids)",
        # the arguments and environment of a process are given by this sysctl or by the rule above, whichever allows: both refuse another's
        f'(deny sysctl-read (sysctl-name-prefix "kern.procargs"){note})',
        f"(deny mach-priv-task-port{note})", "(allow mach-priv-task-port (target same-sandbox))",
        # ways out: no service that was not named, and none registered but a browser's own
        f"(deny mach-lookup{note})", "(allow mach-lookup " + " ".join(f"(global-name {quote(each)})" for each in SERVICES) + f" {BROWSER_NAMES})",
        f"(deny mach-register{note})", f"(allow mach-register {BROWSER_NAMES})",
        f"(deny user-preference*{note})", f"(allow user-preference-read {domains})",
        '(allow ipc-posix-shm-read-data (ipc-posix-name-prefix "apple.cfprefs."))',
        # devices and identifiers
        f"(deny iokit-open*{note})", "(allow iokit-open " + " ".join(f"(iokit-user-client-class {quote(each)})" for each in IOKIT_CLASSES) + ")",
        "(deny iokit-get-properties " + " ".join(f"(iokit-property {quote(each)})" for each in IDENTIFIER_PROPERTIES) + f"{note})",
        f"(deny syscall-unix (syscall-number SYS_gethostuuid){note})",
        f"(deny network*{note})",
        *_files(policy, program, note),
        *_devices(note),
        *_network(policy, note),
    ]
    if policy.deny_read or policy.deny_write:
        policy = policy.validated()
        protected = tuple(dict.fromkeys((*policy.deny_read, *policy.deny_write)))
        if policy.deny_read:
            out += _over("deny", "file-read* process-exec", policy.deny_read, note)
            out += _sockets("deny", policy.deny_read, note)
        out += _over("deny", "file-write* file-link", protected, note)
        above = tuple(dict.fromkeys(str(parent) for path in protected for parent in Path(path).parents))
        out += _literals("deny", "file-write-unlink", above, note)
    hidden = tuple(dict.fromkeys(str(parent) for path in policy.private for parent in Path(path).parents if os.path.lexists(parent)))
    out += _literals("deny", "file-write-unlink", hidden, note)
    return _named_once(out)


def _named_once(lines: Sequence[str]) -> str:
    """The profile of ``lines`` with each long expression defined once and named wherever it is used: the same names are refused a read, a write
    and each side of a socket, and the whole profile is one argument of a command."""
    text = "\n".join(lines) + "\n"
    defined = []
    for number, expression in enumerate(dict.fromkeys(EXPRESSIONS.findall(text))):
        text = text.replace(expression, f"names-{number}")
        defined.append(f"(define names-{number} {expression})\n")
    return text.replace("(version 1)\n", "(version 1)\n" + "".join(defined), 1)
