"""TCP forwarding from the Windows LAN address to a WSL daemon."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import secrets
import select
import socket
import threading
from collections.abc import Callable
from typing import Any

from .onboard.lan import require_local

ENV = "POOLHOUSE_WSL_NETWORK"
LIMIT = 100_000
NAT = ipaddress.ip_network("172.16.0.0/12")


def nat_notice(address: str) -> str:
    """The sentence for a WSL guest on Hyper-V's private network with no Windows launcher in front of it, else ''."""
    try:
        behind = bool(address) and ipaddress.ip_address(address) in NAT
    except ValueError:
        return ""
    if not behind:
        return ""
    return (f"This WSL machine is on Windows' private network ({address}), so your other devices cannot find it or reach it. "
            "Open Poolhouse from Windows, which carries the network for it, or run `python -m poolhouse.device_setup --yes` here: "
            "it asks Windows once, switches WSL to mirrored networking and restarts WSL.")


def _forward(client: socket.socket, target: tuple[str, int], stop: threading.Event) -> None:
    with client:
        try:
            require_local(*target)
            with socket.create_connection(target, timeout=3) as upstream:
                sockets = [client, upstream]
                while sockets and not stop.is_set():
                    ready, _, _ = select.select(sockets, [], [], 0.25)
                    for source in ready:
                        destination = upstream if source is client else client
                        data = source.recv(65536)
                        if data:
                            destination.sendall(data)
                        else:
                            sockets.remove(source)
                            destination.shutdown(socket.SHUT_WR)
        except OSError:
            return


class NetworkBridge:
    """The daemon listeners held by the Windows launcher."""

    def __init__(self, host: str, target: tuple[str, int]) -> None:
        self.host, self.target = host, target
        self.gateway = ""
        self.token = secrets.token_hex(32)
        self.stop = threading.Event()
        self.listeners: list[socket.socket] = []
        self.clients: set[socket.socket] = set()
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(32)
        self.threads: list[threading.Thread] = []

    def start(self) -> NetworkBridge:
        try:
            def forward(client):
                _forward(client, self.target, self.stop)
            listener = self._listen(self.target[1], forward)
            if self.gateway and self.gateway != self.host:
                self._listen(self.target[1], forward, host=self.gateway)
            self.config = json.dumps({"address": listener.getsockname(), "token": self.token})
            return self
        except OSError:
            self.close()
            raise

    def _listen(self, port: int, handler: Callable[[socket.socket], None], *, host: str = "") -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listeners.append(listener)
        # an address, never the empty string that means every interface
        listener.bind((str(ipaddress.ip_address(host or self.host)), port))
        listener.listen(32)
        listener.settimeout(0.25)
        thread = threading.Thread(target=self._accept, args=(listener, handler), daemon=True)
        self.threads.append(thread)
        thread.start()
        return listener

    def _accept(self, listener: socket.socket, handler: Callable[[socket.socket], None]) -> None:
        while not self.stop.is_set():
            try:
                client, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self.slots.acquire(blocking=False):
                client.close()
                continue
            with self.lock:
                self.clients.add(client)
            threading.Thread(target=self._handle, args=(client, handler), daemon=True).start()

    def _handle(self, client: socket.socket, handler: Callable[[socket.socket], None]) -> None:
        try:
            handler(client)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        finally:
            client.close()
            with self.lock:
                self.clients.discard(client)
            self.slots.release()

    def close(self) -> None:
        self.stop.set()
        for listener in self.listeners:
            listener.close()
        with self.lock:
            for client in self.clients:
                with contextlib.suppress(OSError):
                    client.shutdown(socket.SHUT_RDWR)
        for thread in self.threads:
            thread.join(1)

    def __enter__(self) -> NetworkBridge:
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.close()
