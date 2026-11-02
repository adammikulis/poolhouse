"""Software publisher SDK: public control metadata, process-bound DATA only.

Construction authenticates one installed daemon. Methods never switch its standing
anchor or an offer's bridge generation, and never retry an uncertain publication.
An authorized connector deliberately forwarding descriptors remains outside the
kernel connector proof; ordinary inherited Publisher objects are invalidated.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import json
import os
import re
import threading
import time
import weakref

from poolhouse import unix_socket
from poolhouse.graph.guard import MAX_BODY

from . import producer_bootstrap, producer_holder, tls
from .job_records import process_started
from .producer_control import BRIDGE_BYTES, METADATA_BYTES, receive_frame, send_frame

_EVENT = re.compile("[A-Za-z0-9_.-]{1,128}", re.ASCII)
_HEX32 = re.compile("[0-9a-f]{32}")
_CLIENTS = weakref.WeakSet()
_FORK_LOCK = threading.RLock()
_FORK_CLIENTS = []


def _before_fork():
    _FORK_LOCK.acquire()
    _FORK_CLIENTS[:] = sorted(_CLIENTS, key=id)
    for client in _FORK_CLIENTS:
        client._lock.acquire()


def _after_parent():
    for client in reversed(_FORK_CLIENTS):
        client._lock.release()
    _FORK_CLIENTS.clear()
    _FORK_LOCK.release()


def _after_fork():
    global _FORK_LOCK
    for client in _FORK_CLIENTS:
        client._invalid = True
        for channel in tuple(client._sockets):
            channel.close()
        client._sockets.clear()
        client._lock = threading.RLock()
    _FORK_CLIENTS.clear()
    _FORK_LOCK = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_before_fork, after_in_parent=_after_parent, after_in_child=_after_fork
    )


class ProducerUnavailable(OSError):
    """The existing software/source connection is unavailable or its outcome is unknown."""


def _process(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"pid", "started"}
        or type(value["pid"]) is not int
        or value["pid"] <= 0
        or type(value["started"]) not in (int, float)
        or not 0 < value["started"] < float("inf")
    ):
        raise ProducerUnavailable("The software process identity is invalid.")
    return copy.deepcopy(value)


def _offer(value, expected=None):
    fields = {"id", "revision", "label", "device_label", "software_label", "state"}
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or not isinstance(value["id"], str)
        or not _HEX32.fullmatch(value["id"])
        or (expected is not None and value["id"] != expected)
        or type(value["revision"]) is not int
        or value["revision"] != 1
        or not isinstance(value["state"], str)
        or value["state"] not in {"offered", "withdrawn", "connected", "unavailable"}
        or any(
            not isinstance(value[key], str) or len(value[key]) > 256
            for key in ("label", "device_label", "software_label")
        )
    ):
        raise ProducerUnavailable("The software offer changed.")
    return copy.deepcopy(value)


def _path(value):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or "\0" in value
        or ".." in value.split("/")
        or len(os.fsencode(value)) > 103
    ):
        raise ProducerUnavailable("The software DATA location is invalid.")
    return value


class ProducerClient:
    """One original software process, one authenticated daemon, no exported secret."""

    def __init__(self):
        self._initialize(producer_holder.launch_anchor())

    def _initialize(self, binding):
        # Only the private installed helper supplies this; no public root/anchor setter.
        self._anchor = producer_bootstrap._binding(binding, binding.get("nonce"))
        self._owner = {"pid": os.getpid(), "started": process_started(os.getpid())}
        if self._owner["started"] is None:
            raise ProducerUnavailable("The software process is unavailable.")
        self._invalid, self._sockets = False, set()
        self._lock, self._channels = threading.RLock(), {}
        with _FORK_LOCK:
            _CLIENTS.add(self)

    def _current(self):
        if (
            self._invalid
            or os.getpid() != self._owner["pid"]
            or process_started(self._owner["pid"]) != self._owner["started"]
        ):
            raise ProducerUnavailable("The original software process is no longer current.")

    @contextlib.contextmanager
    def _connected(self, path, expected, deadline):
        self._current()
        with self._lock:
            channel = producer_holder._connect(path, deadline)
            self._sockets.add(channel)
        try:
            if not unix_socket.matches(channel, expected):
                raise ProducerUnavailable("The software endpoint process changed.")
            yield channel
        finally:
            with self._lock:
                channel.close()
                self._sockets.discard(channel)

    def _control(self, action, request):
        self._current()
        deadline = time.monotonic() + producer_holder.DEADLINE
        process = {key: self._anchor["daemon"][key] for key in ("pid", "started")}
        context = tls.certificate_context(
            self._anchor["certificate"], context_type=tls.ExactContext
        )
        context.expected_der = base64.b64decode(self._anchor["certificate"], validate=True)
        try:
            with self._connected(self._anchor["control_path"], process, deadline) as plain:
                with self._lock:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Software control handshake expired.")
                    plain.settimeout(remaining)
                    secure = context.wrap_socket(plain, server_hostname=None)
                    secure.set_inheritable(False)
                    self._sockets.discard(plain)
                    self._sockets.add(secure)
                try:
                    if secure.getpeercert(
                        binary_form=True
                    ) != context.expected_der or not unix_socket.matches(secure, process):
                        raise ProducerUnavailable(
                            "The installed software control identity changed."
                        )
                    self._current()
                    send_frame(
                        secure,
                        {"version": 1, "action": action, "request": request},
                        deadline=deadline,
                    )
                    reply = receive_frame(secure, deadline=deadline)
                    self._current()
                    if not unix_socket.matches(secure, process):
                        raise ProducerUnavailable("The installed software control process changed.")
                    return self._reply(reply)
                finally:
                    with self._lock:
                        secure.close()
                        self._sockets.discard(secure)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            raise ProducerUnavailable("The installed software control is unavailable.") from exc

    @staticmethod
    def _reply(value):
        if (
            not isinstance(value, dict)
            or value.get("ok") is not True
            or set(value) != {"ok", "result"}
            or not isinstance(value["result"], dict)
        ):
            raise ProducerUnavailable(
                "The software request was not acknowledged; inspect its receipt before retrying."
            )
        return copy.deepcopy(value["result"])

    def receivers(self):
        result = self._control("receivers", {})
        if set(result) != {"receivers"} or not isinstance(result["receivers"], list):
            raise ProducerUnavailable("The software receiver list is invalid.")
        return result["receivers"]

    def offer(self, receiver_id, label):
        if (
            not isinstance(receiver_id, str)
            or not 0 < len(receiver_id) <= 256
            or not isinstance(label, str)
            or len(label) > 256
        ):
            raise ValueError("Choose an existing receiver and a short software label.")
        result = self._control("offer", {"receiver_id": receiver_id, "label": label})
        if set(result) != {"offer"}:
            raise ProducerUnavailable("The software offer is invalid.")
        return _offer(result["offer"])

    def status(self, offer_id):
        if not isinstance(offer_id, str) or not _HEX32.fullmatch(offer_id):
            raise ValueError("The software offer identity is invalid.")
        result = self._control("offer-status", {"offer_id": offer_id})
        if set(result) != {"offer", "channel"}:
            raise ProducerUnavailable("The software offer status is invalid.")
        offer = _offer(result["offer"], offer_id)
        channel = result["channel"]
        if channel is not None:
            if offer["state"] != "connected":
                raise ProducerUnavailable("The software offer is not connected.")
            if not isinstance(channel, dict) or set(channel) != {"path", "process"}:
                raise ProducerUnavailable("The software DATA binding is invalid.")
            checked = {"path": _path(channel["path"]), "process": _process(channel["process"])}
            with self._lock:
                previous = self._channels.setdefault(offer_id, checked)
                if previous != checked:
                    raise ProducerUnavailable(
                        "The software DATA process changed; this offer cannot be reused."
                    )
            channel = copy.deepcopy(checked)
        return {"offer": offer, "channel": channel}

    def withdraw(self, offer_id):
        if not isinstance(offer_id, str) or not _HEX32.fullmatch(offer_id):
            raise ValueError("The software offer identity is invalid.")
        result = self._control("withdraw", {"offer_id": offer_id})
        if set(result) != {"offer"}:
            raise ProducerUnavailable("The software withdrawal is invalid.")
        return _offer(result["offer"], offer_id)

    def _data(self, offer_id, event_id, record=None, *, retry_key=None):
        if not isinstance(event_id, str) or not _EVENT.fullmatch(event_id):
            raise ValueError(
                "Use a stable event identity of letters, digits, dots, dashes or underscores."
            )
        if retry_key is not None:
            if not isinstance(retry_key, str) or not _HEX32.fullmatch(retry_key):
                raise ValueError("A stable retry command identity is required.")
            if record is not None:
                raise ValueError("Retry cannot supply replacement records.")
        if record is not None:
            if not isinstance(record, dict):
                raise ValueError("A software record must be a JSON object.")
            raw = json.dumps(
                record, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            if len(raw) > MAX_BODY:
                raise ValueError("The software record exceeds the existing event size limit.")
        current = self.status(offer_id)
        endpoint = current["channel"]
        if endpoint is None:
            raise ProducerUnavailable("The software offer is not currently connected.")
        deadline = time.monotonic() + producer_holder.DEADLINE
        request = {"action": "receipt" if record is None else "publish", "event_id": event_id}
        if retry_key is not None:
            request.update(action="retry", retry_key=retry_key)
        elif record is not None:
            request["record"] = record
        try:
            with self._connected(endpoint["path"], endpoint["process"], deadline) as channel:
                # Verify the authenticated DATA server before a single record byte leaves this process.
                self._current()
                send_frame(channel, request, deadline=deadline, maximum=BRIDGE_BYTES)
                reply = receive_frame(channel, deadline=deadline, maximum=METADATA_BYTES)
                self._current()
                if not unix_socket.matches(channel, endpoint["process"]):
                    raise ProducerUnavailable("The software DATA process changed.")
                return self._reply(reply)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            raise ProducerUnavailable(
                "The software outcome is unknown; inspect its receipt before retrying."
            ) from exc

    def publish(self, offer_id, event_id, record):
        if record is None:
            raise ValueError("A software record must be a JSON object.")
        return self._data(offer_id, event_id, record)

    def receipt(self, offer_id, event_id):
        return self._data(offer_id, event_id)

    def retry(self, offer_id, event_id, retry_key):
        """Explicitly retry the same immutable event only after definitive receiver rejection."""
        if not isinstance(retry_key, str) or not _HEX32.fullmatch(retry_key):
            raise ValueError("A stable retry command identity is required.")
        return self._data(offer_id, event_id, retry_key=retry_key)
