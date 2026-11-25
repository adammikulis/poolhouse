#!/usr/bin/env python3
"""Measure the bytes a real package client sends to each registry host while it installs, for `contracts/egress-hosts.json`.

A counting CONNECT proxy on loopback relays each client's tunnels and counts the bytes sent toward the host and received from it. The clients
(pip, npm, cargo) install a small real project and a larger one (about ten direct dependencies and what they pull in) from the real registries; a
download counts nothing against the bound (docs/design-isolated-launcher.md, 6.3 and 17). It prints one JSON entry per host and install: the bytes
sent to the host over every tunnel of the install, the most one tunnel sent, the tunnels made, the command that measured it and the date. The bound
of one tunnel is the small install's total, and the budget of a session is the large install's total.

    python scripts/measure_egress_hosts.py            # runs the six installs; needs the network and the three clients
"""

from __future__ import annotations

import datetime
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from pathlib import Path

SMALL = {"pip": "requests", "npm": "express", "cargo": 'serde = "1"\nserde_json = "1"\n'}
LARGE = {"pip": "flask sqlalchemy httpx pydantic rich pytest pyyaml jinja2 click attrs",
         "npm": "express lodash axios moment chalk commander yargs uuid debug async",
         "cargo": 'serde = "1"\nserde_json = "1"\nregex = "1"\nrand = "0.8"\nchrono = "0.4"\nclap = "4"\nreqwest = "0.12"\n'
                  'tokio = { version = "1", features = ["full"] }\n'}


def pip(names: str):
    return lambda proxy, work: ["pip", "download", *names.split(), "--no-cache-dir", "--disable-pip-version-check", "--proxy", proxy, "-d", work]


def npm(names: str):
    return lambda proxy, work: ["npm", "install", *names.split(), "--prefix", work, "--cache", work + "/cache", "--no-audit", "--no-fund",
                                "--proxy", proxy, "--https-proxy", proxy]


def cargo(_names: str):
    return lambda proxy, work: ["cargo", "fetch", "--manifest-path", work + "/Cargo.toml"]


INSTALLS = {
    "pip": ("python", "small", pip(SMALL["pip"])),
    "npm": ("javascript", "small", npm(SMALL["npm"])),
    "cargo": ("rust", "small", cargo("")),
    "pip-many": ("python", "large", pip(LARGE["pip"])),
    "npm-many": ("javascript", "large", npm(LARGE["npm"])),
    "cargo-many": ("rust", "large", cargo("")),
}


class Counter:
    """Bytes per host, out (toward the host) and in (from it), over every tunnel of one install, the most one tunnel sent, and how many tunnels."""

    def __init__(self) -> None:
        self.out: dict[str, int] = defaultdict(int)
        self.inbound: dict[str, int] = defaultdict(int)
        self.most: dict[str, int] = defaultdict(int)
        self.tunnels: dict[str, int] = defaultdict(int)
        self.lock = threading.Lock()

    def add(self, host: str, out: int, inbound: int) -> None:
        with self.lock:
            self.out[host] += out
            self.inbound[host] += inbound
            self.most[host] = max(self.most[host], out)
            self.tunnels[host] += 1


def tunnel(client: socket.socket, counter: Counter) -> None:
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = client.recv(4096)
        if not chunk:
            client.close()
            return
        head += chunk
    target = head.split(b" ", 2)[1].decode()
    host, _, port = target.partition(":")
    upstream = socket.create_connection((host, int(port or 443)))
    client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
    out = inbound = 0
    sockets = [client, upstream]
    while True:
        ready, _, _ = select.select(sockets, [], [], 30)
        if not ready:
            break
        done = False
        for each in ready:
            data = each.recv(65536)
            if not data:
                done = True
                break
            (upstream if each is client else client).sendall(data)
            if each is client:
                out += len(data)
            else:
                inbound += len(data)
        if done:
            break
    counter.add(host, out, inbound)
    client.close()
    upstream.close()


def proxy(counter: Counter) -> tuple[socket.socket, int]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(64)

    def serve() -> None:
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=tunnel, args=(connection, counter), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return server, server.getsockname()[1]


def measure(name: str) -> tuple[str, Counter, str]:
    ecosystem, size, build = INSTALLS[name]
    counter = Counter()
    server, port = proxy(counter)
    work = tempfile.mkdtemp(prefix=f"egress-{name}-")
    env = {**os.environ, "CARGO_HOME": work + "/cargo", "CARGO_HTTP_PROXY": f"http://127.0.0.1:{port}"}
    if name.startswith("cargo"):
        (Path(work) / "src").mkdir()
        deps = (LARGE if size == "large" else SMALL)["cargo"]
        (Path(work) / "Cargo.toml").write_text(f'[package]\nname = "measure"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\n{deps}')
        (Path(work) / "src" / "lib.rs").write_text("")
    argv = build(f"http://127.0.0.1:{port}", work)
    subprocess.run(argv, env=env, check=True, capture_output=True, timeout=900)
    server.close()
    shutil.rmtree(work, ignore_errors=True)
    return ecosystem, counter, " ".join(argv[:3]) + (" ..." if size == "large" else "")


def main() -> int:
    today = datetime.date.today().isoformat()
    found = []
    for name, (_ecosystem, size, _build) in INSTALLS.items():
        ecosystem, counter, command = measure(name)
        found += [{"host": host, "ecosystem": ecosystem, "install": size, "bytes_out": sent, "max_tunnel_out": counter.most[host],
                   "tunnels": counter.tunnels[host], "command": f"scripts/measure_egress_hosts.py ({command})", "date": today}
                  for host, sent in sorted(counter.out.items())]
    json.dump(found, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
