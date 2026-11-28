"""This daemon's compute service, bound to its node so the other devices of the pool can reach it.

A member reaches a device's daemon only at the certificate and address that device's node vouches for (`fleet.service_pool`). The node takes a
binding with proof of the certificate's key from this device's own session, and renews an unchanged one for a process that cannot prove that
session. The binding lasts as long as the daemon's process and the node's, so `Keeper` looks again every few seconds and binds when it is gone
or the address changed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from poolhouse import errors, node_health, node_launch
from poolhouse.board import founding
from poolhouse.board.client import Client
from poolhouse.fleet import tls
from poolhouse.fleet.service_pool import Pool
from poolhouse.log import say, warn

__all__ = ["INTERVAL_S", "Keeper", "bind"]

INTERVAL_S = 10.0


def bind(state: Path, identity: tls.Identity, endpoint: str) -> None:
    """Have the node of ``state`` vouch for ``identity``'s certificate as this device's compute service at ``endpoint``."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    params = {"cert": identity.der.hex(), "endpoint": endpoint}
    try:
        token = founding.pool_session(Client(state), person_session=True).token
    except founding.Refusal:
        token = ""   # the node still renews a binding it approved before
    draft = node_health.call(state, "service_draft", params)
    key = serialization.load_pem_private_key(identity.keyfile.read_bytes(), password=None)
    proof = key.sign(bytes.fromhex(draft["proof_bytes"]), ec.ECDSA(hashes.SHA256())).hex()
    node_health.call(state, "service_bind", {**params, "challenge": draft["challenge"], "proof": proof}, token=token)


class Keeper:
    """Keep this process's compute service bound while it runs. ``endpoint`` is asked each time, so a new address is bound."""

    def __init__(self, state: Path, identity: Callable[[], tls.Identity], endpoint: Callable[[], str]) -> None:
        self.state, self.identity, self.endpoint = Path(state), identity, endpoint
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._said = ""

    def check(self) -> bool:
        """Whether the pool can reach this service now, binding it when needed. False on a device that is in no pool."""
        if not (self.state / "device.cert").is_file():
            return False   # a device whose node never ran is in no pool
        if node_health.node_health(self.state) is None:
            node_launch.ensure_node(self.state)
        pool = Pool(self.state)
        if not pool.pool_id(or_unfounded=True):
            return False
        identity, endpoint = self.identity(), self.endpoint()
        try:
            if pool.authorize(identity.fingerprint).endpoint == endpoint:
                return True
        except PermissionError:
            pass
        bind(self.state, identity, endpoint)
        say(f"  the pool reaches this device's compute service at {endpoint}", flush=True)
        return True

    def _look(self) -> None:
        try:
            self.check()
            self._said = ""
        except (OSError, ValueError, RuntimeError, errors.Error) as exc:
            if str(exc) != self._said:   # said once, and again only when the reason changes
                self._said = str(exc)
                warn(f"the pool cannot reach this device's compute service: {exc}")

    def _run(self) -> None:
        while True:
            self._look()
            if self._stop.wait(INTERVAL_S):
                return

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="compute-service-binding", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=INTERVAL_S)
