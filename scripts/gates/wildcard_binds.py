"""A socket, server or child process that listens beyond loopback, outside the code that is the network on purpose."""

from __future__ import annotations

import ast
import re
from itertools import pairwise
from pathlib import Path

from . import Finding
from ._perfile import finder
from ._util import calls, dotted, parse, rel

NAME = "wildcard-binds"
OWNER = "poolhouse.lan"
INCREMENTAL = True
HARD = True
ROOTS = ("src/poolhouse", "scripts", "tests", "examples")
RUST_ROOT = "app"

LAN = {
    "src/poolhouse/fleet/daemon.py": "LAN mode of the pool daemon, behind lan.allowed()",
    "src/poolhouse/fleet/onboard/cli.py": "add-device listens for a join because the person ran it",
    "src/poolhouse/fleet/onboard/share_cli.py": "share serves a folder to the LAN because the person ran it",
    "tests/pool_check/device.py": "a container listens in a network namespace of its own; a CI runner's node on its own",
    "app/poolhouse-node/src/main.rs": "--lan: the node's own listener and beacon",
    "app/poolhouse-node/src/beacon.rs": "the beacon sockets, joined only under --lan",
    "app/poolhouse-node/src/probe.rs": "the Local Network permission probe the person asks for",
    "app/poolhouse-node/src/netif.rs": "the route lookup, run on Linux only",
    "app/poolhouse-node/src/presence/mod.rs": "the phone address a status line shows",
}
"""Every file that may name a wildcard listener, with why. A development or test file is not here."""

WILD = re.compile(r"(?:0\.0\.0\.0|\[::\]|::|\*)(?::\d*)?|")
FLAGS = frozenset({"--host", "--bind", "--listen", "--beacon-bind", "--phone", "-b"})
LISTENERS = frozenset({"bind", "create_server", "start_server", "HTTPServer", "ThreadingHTTPServer", "Server", "TCPServer",
                       "ThreadingTCPServer", "UDPServer", "ThreadingUDPServer", "run_app", "serve", "run"})
RUST_BIND = re.compile(r"0\.0\.0\.0:|Ipv[46]Addr::UNSPECIFIED|\[::\]:")
OPT_IN = "// ---- OPT-IN BELOW"


def describe() -> str:
    return ("A listener, server argument or child-process flag that opens beyond loopback outside poolhouse.lan's own code;\n"
            "    macOS asks the person to allow every new binary that does, so development and tests bind 127.0.0.1.")


def wild(node: ast.expr | None, names: dict[str, str]) -> bool:
    """Whether ``node`` is a string that listens on every interface (or on none named, which is the same)."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and bool(WILD.fullmatch(node.value))
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        return isinstance(first, ast.Constant) and isinstance(first.value, str) and bool(re.match(r"(?:0\.0\.0\.0|\[::\]):", first.value))
    if isinstance(node, ast.Name):
        return node.id in names and bool(WILD.fullmatch(names[node.id]))
    if isinstance(node, ast.IfExp):
        return wild(node.body, names) or wild(node.orelse, names)
    return False


def named(node: ast.expr | None) -> bool:
    """Whether ``node`` spells the wildcard out (an empty string alone says nothing: it is a common default for 'not given')."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and bool(node.value) and bool(WILD.fullmatch(node.value)) and node.value != "*"
    if isinstance(node, ast.IfExp):
        return named(node.body) or named(node.orelse)
    if isinstance(node, ast.BoolOp):
        return any(named(value) for value in node.values)
    return False


def constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "text"`` assignments, so a named wildcard is seen for what it is."""
    return {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str) for t in n.targets if isinstance(t, ast.Name)}


def listens(call: ast.Call, names: dict[str, str]) -> bool:
    """Whether a listener call is given a wildcard address: ``host=``, a ``(host, port)`` tuple, or a host word before a port."""
    if any(k.arg == "host" and wild(k.value, names) for k in call.keywords):
        return True
    first = call.args[0] if call.args else None
    if isinstance(first, ast.Tuple) and first.elts:
        return wild(first.elts[0], names)
    return any(wild(arg, names) for arg in call.args[:-1][:2])


def flagged(items: list[ast.expr], names: dict[str, str]) -> list[int]:
    """Lines of the words in an argument list that a listening flag is followed by a wildcard for."""
    out = []
    for before, after in pairwise(items):
        if isinstance(before, ast.Constant) and before.value in FLAGS and wild(after, names):
            out.append(after.lineno)
    return out


def published(items: list[ast.expr]) -> list[int]:
    """Lines where ``-p`` or ``--publish`` gives a container port with no address, which Docker publishes on every interface."""
    out = []
    for before, after in pairwise(items):
        if not (isinstance(before, ast.Constant) and before.value in ("-p", "--publish")):
            continue
        bare = isinstance(after, ast.Constant) and isinstance(after.value, str) and re.fullmatch(r"\d+(?::\d+){1,2}(?:/\w+)?", after.value)
        if bare or (isinstance(after, ast.JoinedStr) and after.values and isinstance(after.values[0], ast.FormattedValue)):
            out.append(after.lineno)
    return out


def scan(path: Path, where: str) -> list[Finding]:
    out: list[Finding] = []
    if where in LAN:
        return out
    tree = parse(path)
    if tree is None:
        return out
    names = constants(tree)
    for node, name in calls(tree):
        method = name.rsplit(".", 1)[-1]
        if method in LISTENERS and listens(node, names):
            out.append(Finding(where, node.lineno, f"{dotted(node.func) or method} listens on every interface"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple) and len(node.elts) == 2 and named(node.elts[0]):
            out.append(Finding(where, node.lineno, "an address that listens on every interface"))
        elif isinstance(node, ast.BoolOp) and named(node):
            out.append(Finding(where, node.lineno, "a fallback to the wildcard address"))
        elif isinstance(node, ast.keyword) and named(node.value) and node.arg in ("host", "default", "bind", "address", "interface"):
            out.append(Finding(where, node.value.lineno, f"{node.arg}= the wildcard address"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and named(node.value):
            out.append(Finding(where, node.lineno, "a name for the wildcard address"))
        if isinstance(node, (ast.List, ast.Tuple)):
            words = list(node.elts)
            out += [Finding(where, line, "a listening flag with a wildcard address") for line in flagged(words, names)]
            out += [Finding(where, line, "a published port with no address") for line in published(words)]
        elif isinstance(node, ast.Call):
            out += [Finding(where, line, "a listening flag with a wildcard address") for line in flagged(list(node.args), names)]
    return out


def rust_files(root: Path) -> list[Path]:
    base = root / RUST_ROOT
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.rs") if "target" not in p.relative_to(base).parts)


def scan_rust(path: Path, where: str) -> list[Finding]:
    if where in LAN:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for number, text in enumerate(lines, 1):
        if OPT_IN in text:
            break
        if RUST_BIND.search(text):
            out.append(Finding(where, number, "a wildcard address outside the opt-in tests"))
    return out


python = finder(ROOTS, scan)


def find(root: Path) -> list[Finding]:
    return [*python(root), *(f for path in rust_files(root) for f in scan_rust(path, rel(path, root)))]
