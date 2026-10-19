"""Sealed Studio execution requests and explicit resource exchange with one enrolled device."""
from __future__ import annotations

import json
import os
import re
import shutil
from http.client import IncompleteRead
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from poolhouse import http, macauth, sealing
from poolhouse.http import ONCE, Options, Sealed, ServerError, open_stream

from . import tls
from .studio_transfer import (
    CHUNK_BYTES,
    MANIFEST_BYTES,
    TransferStore,
    _name,
    _open,
    checked_manifest,
    manifest,
    resource_handle,
    resource_ports,
)

ACTIONS = frozenset({"validate", "submit", "status", "cancel", "result", "authorize", "resume"})
MAX_REPLY = MANIFEST_BYTES * 2
_ID = re.compile(r"[a-f0-9]{32}(?:[a-f0-9]{32})?")


class MemberUncertain(OSError):
    """A device request may have been accepted and requires dispatch reconciliation."""


class MemberExecution:
    """Exchange fixed Studio actions with the currently selected enrolled member."""
    def __init__(self, member, authorize):
        parts = urlsplit(member.base_url)
        beacon = member.beacon
        if (parts.scheme != "https" or parts.username or parts.password or parts.path not in ("", "/")
                or parts.query or parts.fragment or not beacon or not beacon.cert or not beacon.machine
                or not member.token.startswith(macauth.PREFIX) or not callable(authorize)):
            raise ValueError("Studio execution requires a currently enrolled encrypted pool device.")
        if parts.hostname not in {beacon.host, beacon.hostname} or parts.port != beacon.port:
            raise ValueError("Studio execution must use the discovered device address.")
        self.member, self.authorize = member, authorize
        self.base_url, self.machine, self.certificate = member.base_url, beacon.machine, beacon.cert
        self._allowed()
        http.pin(parts.netloc, tls.pinned_context(beacon.cert))

    def _allowed(self):
        if (self.member.base_url != self.base_url or self.member.beacon.machine != self.machine
                or self.member.beacon.cert != self.certificate):
            raise PermissionError("The selected execution device identity changed.")
        if self.authorize() is False:
            raise PermissionError("This device execution is no longer authorized.")

    def _wire(self, method: str, path: str, raw: bytes | None, limit: int) -> bytes:
        self._allowed()
        endpoint = self.base_url + path
        def guard(url):
            self._allowed()
            if url != endpoint:
                raise ValueError("Studio device requests cannot redirect.")
            return url
        options = Options(token=self.member.token, headers={sealing.HEADER: "2", "Content-Type": "application/json"},
                          retry=ONCE, timeout=min(self.member.timeout, 30), guard=guard,
                          error_body_limit=MAX_REPLY + sealing.NONCE_BYTES + 16)
        try:
            with open_stream(endpoint, method=method, data=raw, options=options) as response:
                if response.headers.get(sealing.HEADER) != "1":
                    raise ValueError("The Studio device response was not authenticated and encrypted.")
                bound = limit + sealing.NONCE_BYTES + 16
                data = response.read(bound + 1)
                if len(data) > bound:
                    raise ValueError("The Studio device response exceeds its declared size limit.")
                envelope = getattr(response, "sealed", Sealed())
                if not envelope.key:
                    raise ValueError("The Studio response has no authenticated request binding.")
                data = envelope.open(response.status, response.headers, data)
                if len(data) > limit:
                    raise ValueError("The Studio device response exceeds its declared size limit.")
        except ServerError as exc:
            if not exc.status or 500 <= exc.status < 600:
                raise MemberUncertain("The device outcome is uncertain; reconcile this dispatch before continuing.") from None
            raise ValueError("The selected device refused this Studio request.") from None
        except (IncompleteRead, OSError):
            raise MemberUncertain("The device outcome is uncertain; reconcile this dispatch before continuing.") from None
        self._allowed()
        return data

    def _json(self, method: str, path: str, value: dict | None = None) -> dict:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() if value is not None else None
        if raw is not None and len(raw) > MAX_REPLY:
            raise ValueError("The Studio device request exceeds its size limit.")
        value = json.loads(self._wire(method, path, raw, MAX_REPLY))
        if not isinstance(value, dict):
            raise ValueError("The Studio device returned an invalid response.")
        return value

    def request(self, action: str, payload: dict) -> dict:
        if action not in ACTIONS or not isinstance(payload, dict):
            raise ValueError("Choose a fixed Studio execution action.")
        return self._json("POST", "/studio/v1/executions/" + action, payload)

    @staticmethod
    def _resource(dispatch: str, resource: str, action: str, **query) -> str:
        if not isinstance(dispatch, str) or not _ID.fullmatch(dispatch) or not isinstance(resource, str) or not re.fullmatch(r"[a-f0-9]{64}", resource):
            raise ValueError("Invalid resource dispatch identity.")
        path = f"/studio/v1/resources/{dispatch}/{resource}/{action}"
        return path + ("?" + urlencode(query, quote_via=quote) if query else "")

    def stage(self, dispatch: str, inputs: dict, spec: dict) -> dict:
        safe, resources = dict(inputs), {}
        for name in sorted(resource_ports(spec, "inputs")):
            value = inputs.get(name)
            if value is None:
                continue
            if not isinstance(value, dict) or not isinstance(value.get("path"), str):
                raise ValueError("Choose an authorized local input resource.")
            path = Path(value["path"]).absolute()
            resource = manifest(path, value["kind"], gym=value.get("gym", False))
            checked_manifest(resource, resource["bytes"])
            safe[name] = resource_handle(value, resource)
            if resource["id"] in resources:
                continue
            self._json("POST", self._resource(dispatch, resource["id"], "begin"), resource)
            root = path if resource["shape"] == "directory" else path.parent
            for file in resource["files"]:
                self._upload(dispatch, resource["id"], root, file)
            acknowledged = self._json("POST", self._resource(dispatch, resource["id"], "finish"), {})
            if acknowledged != resource:
                raise ValueError("The selected device acknowledged different resource content.")
            resources[resource["id"]] = resource
        return {"inputs": safe, "resources": list(resources.values())}

    def _upload(self, dispatch: str, resource: str, root: Path, file: dict) -> None:
        descriptor = _open(root, _name(file["name"]), os.O_RDONLY)
        with os.fdopen(descriptor, "rb") as stream:
            offset = 0
            while True:
                data = stream.read(CHUNK_BYTES)
                if not data and offset:
                    break
                target = self._resource(dispatch, resource, "file", name=file["name"], offset=offset)
                reply = json.loads(self._wire("PUT", target, data, MAX_REPLY))
                offset += len(data)
                if not isinstance(reply, dict) or type(reply.get("offset")) is not int or not offset <= reply["offset"] <= file["size"]:
                    raise ValueError("The device acknowledged a different resource chunk.")
                if reply["offset"] > offset:
                    offset = reply["offset"]
                    stream.seek(offset)
                if not data:
                    break
            if offset != file["size"]:
                raise ValueError("The input resource changed during transfer.")

    def collect(self, reply: dict, output_dir: Path, spec: dict, *, dispatch_id: str) -> dict:
        if not isinstance(reply, dict) or not isinstance(reply.get("outputs"), dict) or not isinstance(reply.get("resources"), list):
            raise ValueError("The device returned invalid execution outputs.")
        dispatch = reply.get("dispatch_id")
        if not isinstance(dispatch_id, str) or not _ID.fullmatch(dispatch_id) or dispatch != dispatch_id:
            raise ValueError("The device returned another execution dispatch.")
        resources = {}
        total = 0
        for resource in reply["resources"]:
            checked_manifest(resource, resource["bytes"])
            if resource["id"] in resources:
                raise ValueError("The device returned duplicate output resources.")
            resources[resource["id"]] = resource
            total += resource["bytes"]
        expected = {value.get("resource") for name, value in reply["outputs"].items()
                    if name in resource_ports(spec, "outputs") and isinstance(value, dict)}
        if set(resources) != expected:
            raise ValueError("The device returned undeclared output resources.")
        output_dir = Path(output_dir).absolute()
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if total > shutil.disk_usage(output_dir).free:
            raise ValueError("The remote outputs exceed available local storage.")
        if set(reply["outputs"]) - {port["name"] for port in spec["outputs"]}:
            raise ValueError("The device returned undeclared output ports.")
        store = TransferStore(output_dir / ".resources", max_bytes=total,
                              authorize=lambda source, identifier: self.authorize())
        source = self.machine
        for resource in resources.values():
            store.begin(source, dispatch, resource)
            for file in resource["files"]:
                offset = 0
                while offset < file["size"]:
                    path = self._resource(dispatch, resource["id"], "file", name=file["name"], offset=offset)
                    size = min(CHUNK_BYTES, file["size"] - offset)
                    data = self._wire("GET", path, None, size)
                    if len(data) != size:
                        raise ValueError("The output resource was truncated.")
                    store.put(source, dispatch, resource["id"], name=file["name"], offset=offset, data=data)
                    offset += size
                if offset == 0:
                    store.put(source, dispatch, resource["id"], name=file["name"], offset=0, data=b"")
            store.finish(source, dispatch, resource["id"])
        outputs = dict(reply["outputs"])
        for name in resource_ports(spec, "outputs"):
            if name in outputs:
                outputs[name] = store.inputs(source, dispatch, {name: outputs[name]})[name]
        return outputs
