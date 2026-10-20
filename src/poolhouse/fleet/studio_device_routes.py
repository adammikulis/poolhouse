"""Sealed member routes for typed Studio execution and staged resources."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.parse

from poolhouse.home import machine_id
from poolhouse.studio.store import Conflict

from . import project_enrollment
from .discovery import memberships
from .membership import fingerprint_of
from .pool_roster import Pool
from .remote import Discovery, Member
from .studio_execution import DeviceExecutions
from .studio_transport import MemberExecution

PREFIX = "/studio/v1/"
_INITIALIZE = threading.Lock()


def _admitted(ui, source: str, dispatch: str) -> bool:
    certificate, pool, target = source.split(":")
    if target != machine_id():
        return False
    roster = Pool(ui.pool_key_path)
    return any(hashlib.sha256(item.key).hexdigest() == pool and item.group in roster.groups_of(certificate)
               for item in memberships(ui.pool_key_path))


def _origin(ui, source: str, dispatch: str, spec_digest: str) -> bool:
    if not _admitted(ui, source, dispatch):
        return False
    certificate = source.split(":", 1)[0]
    origins = ui.__dict__.setdefault("_studio_origins", {})
    if source not in origins:
        pool = source.split(":")[1]
        membership = next((item for item in memberships(ui.pool_key_path) if hashlib.sha256(item.key).hexdigest() == pool), None)
        if membership is None:
            return False
        discovered = Member.discover(discovery=Discovery(key=membership.key, group=membership.group,
                                                        pool_key_path=ui.pool_key_path, port=ui.discovery_port))
        found = [item for item in discovered if item.beacon and fingerprint_of(item.beacon.cert) == certificate]
        if len(found) != 1:
            return False
        origins[source] = found[0]
    try:
        reply = MemberExecution(origins[source], lambda: _admitted(ui, source, dispatch)).request(
            "authorize", {"dispatch_id": dispatch, "spec_digest": spec_digest})
        return reply.get("authorized") is True
    except (ValueError, OSError, RuntimeError):
        origins.pop(source, None)
        return False


def service(ui):
    with _INITIALIZE:
        if getattr(ui, "studio_executions", None) is None:
            ui.studio_executions = DeviceExecutions(ui, lambda source, dispatch: _admitted(ui, source, dispatch),
                                                  lambda source, dispatch, digest: _origin(ui, source, dispatch, digest))
        return ui.studio_executions


def answer(handler, ui, path: str, body=None) -> bool:
    """Dispatch device work only after the daemon opens a sealed member request."""
    if not path.startswith((PREFIX + "executions/", PREFIX + "resources/")):
        return False
    membership = project_enrollment._matched(handler._sealing(), memberships(ui.pool_key_path)) if ui else None
    if membership is None or not handler._member:
        handler._send(403, {"error": "Device execution requires a current enrolled pool device and a sealed request."})
        return True
    source = handler._member + ":" + hashlib.sha256(membership.key).hexdigest() + ":" + machine_id()
    if not _admitted(ui, source, ""):
        handler._send(403, {"error": "This device is no longer a member of the selected pool."})
        return True
    try:
        if path.startswith(PREFIX + "resources/"):
            return _resource(handler, service(ui), source, path, body)
        if handler.command != "POST":
            raise ValueError("Device execution uses sealed typed requests.")
        request = json.loads(body or b"{}")
        if not isinstance(request, dict):
            raise ValueError("Device execution requires an object.")
        action = path.removeprefix(PREFIX + "executions/")
        if action == "authorize":
            studio = getattr(ui, "studio", None)
            authorized = bool(studio and studio.dispatcher and studio.dispatcher.authorize_execution(source, request))
            handler._send(200, {"authorized": authorized})
        elif action == "validate":
            if set(request) != {"nodes"} or not isinstance(request["nodes"], list):
                raise ValueError("Choose fixed nodes to validate on this device.")
            handler._send(200, service(ui).validate(request["nodes"]))
        elif action in {"submit", "status", "cancel", "result", "resume"}:
            handler._send(200, getattr(service(ui), action)(source, request))
        else:
            handler._send(404, {"error": "No such device execution action."})
    except Conflict as exc:
        handler._send(409, {"error": str(exc)})
    except PermissionError as exc:
        handler._send(403, {"error": str(exc)})
    except KeyError as exc:
        handler._send(404, {"error": str(exc), "state": "interrupted"})
    except (ValueError, OSError, RuntimeError, TypeError) as exc:
        handler._send(400, {"error": str(exc)})
    return True


def _resource(handler, executions, source: str, path: str, body) -> bool:
    transfer = executions.transfer
    parts = path.removeprefix(PREFIX + "resources/").split("/")
    if len(parts) != 3:
        raise ValueError("Choose a declared resource operation.")
    dispatch, resource, action = parts
    if action in {"begin", "finish"} and handler.command == "POST":
        request = json.loads(body or b"{}")
        if action == "begin":
            if not isinstance(request, dict) or request.get("id") != resource:
                raise ValueError("Choose the matching resource manifest.")
            reply = transfer.begin(source, dispatch, request)
        else:
            if request:
                raise ValueError("Completing a resource accepts an empty object.")
            reply = transfer.finish(source, dispatch, resource)
        handler._send(200, reply)
    elif action == "file" and handler.command in {"PUT", "GET"}:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query, strict_parsing=True)
        if set(query) - {"name", "offset", "size"} or any(len(values) != 1 for values in query.values()):
            raise ValueError("Choose one resource file and byte range.")
        arguments = {"name": query.get("name", [""])[0], "offset": int(query.get("offset", ["0"])[0])}
        if handler.command == "PUT":
            handler._send(200, transfer.put(source, dispatch, resource, **arguments, data=body or b""))
        else:
            executions.authorize_read(source, dispatch)
            arguments["size"] = int(query.get("size", ["262144"])[0])
            handler._send(200, {}, raw=transfer.read(source, dispatch, resource, **arguments))
    else:
        handler._send(405, {"error": "Choose a supported resource action."})
    return True
