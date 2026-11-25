#!/usr/bin/env python3
"""Measure the bytes a real package client sends to each registry host while it installs, for `contracts/egress-hosts.json`.

A counting CONNECT proxy on loopback relays each client's tunnels and counts the bytes sent toward the host and received from it. The clients
(pip, npm, cargo) install a small real project from the real registries; a download counts nothing against the bound (docs/design-isolated-launcher.md,
6.3 and 17). It prints one JSON entry per host: the largest bytes-out of any one install, the command that measured it and the date.

    python scripts/measure_egress_hosts.py            # runs the three installs; needs the network and the three clients
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

INSTALLS = {
    "pip": ("python", lambda proxy, work: ["pip", "download", "requests", "--no-cache-dir", "--disable-pip-version-check", "--proxy", proxy, "-d", work]),
    "npm": ("javascript", lambda proxy, work: ["npm", "install", "express", "--prefix", work, "--cache", work + "/cache", "--no-audit", "--no-fund",
                                                "--proxy", proxy, "--https-proxy", proxy]),
    "cargo": ("rust", lambda proxy, work: ["cargo", "fetch", "--manifest-path", work + "/Cargo.toml"]),
}


class Counter:
    """Bytes per host, out (toward the host) and in (from it), over every tunnel of one install."""

    def __init__(self) -> None:
        self.out: dict[str, int] = defaultdict(int)
        self.inbound: dict[str, int] = defaultdict(int)
        self.lock = threading.Lock()

    def add(self, host: str, out: int, inbound: int) -> None:
        with self.lock:
            self.out[host] += out
            self.inbound[host] += inbound


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


def measure(name: str) -> tuple[str, dict[str, int], str]:
    ecosystem, build = INSTALLS[name]
    counter = Counter()
    server, port = proxy(counter)
    work = tempfile.mkdtemp(prefix=f"egress-{name}-")
    env = {**os.environ, "CARGO_HOME": work + "/cargo", "CARGO_HTTP_PROXY": f"http://127.0.0.1:{port}"}
    if name == "cargo":
        (Path(work) / "src").mkdir()
        (Path(work) / "Cargo.toml").write_text('[package]\nname = "measure"\nversion = "0.1.0"\nedition = "2021"\n[dependencies]\nserde = "1"\nserde_json = "1"\n')
        (Path(work) / "src" / "lib.rs").write_text("")
    argv = build(f"http://127.0.0.1:{port}", work)
    subprocess.run(argv, env=env, check=True, capture_output=True, timeout=600)
    server.close()
    shutil.rmtree(work, ignore_errors=True)
    return ecosystem, dict(counter.out), " ".join(argv[:3])


def main() -> int:
    today = datetime.date.today().isoformat()
    found = []
    for name in INSTALLS:
        ecosystem, out, command = measure(name)
        found += [{"host": host, "ecosystem": ecosystem, "bytes_out": sent, "command": f"scripts/measure_egress_hosts.py ({command})", "date": today}
                  for host, sent in sorted(out.items())]
    json.dump(found, sys.stdout, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
