"""Authenticated inert package exchange between current pool devices."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.parse
from pathlib import Path

from poolhouse import sealing
from poolhouse.http import ONCE, Options, Sealed, ServerError, open_stream
from poolhouse.studio.library import LocalLibrary
from poolhouse.studio.packages import import_package
from poolhouse.studio.store import StudioStore

from . import project_enrollment
from .discovery import DiscoveryError, memberships
from .remote import Discovery, Member

ENDPOINT = "/studio/v1/packages"
MAX_BYTES = 3_000_000
_INITIALIZE = threading.Lock()


def _shareable(package):
    checked = import_package(package)
    if len(json.dumps(checked).encode()) > MAX_BYTES - 2048:
        raise ValueError("This package exceeds the pool sharing size limit.")
    return checked


def _page(library, target):
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query, strict_parsing=True)
    if set(query) - {"after"} or len(query.get("after", [])) > 1:
        raise ValueError("Choose a valid library page.")
    after = json.loads(query.get("after", ['["", ""]'])[0])
    if not isinstance(after, list) or len(after) != 2 or any(not isinstance(item, str) for item in after):
        raise ValueError("Choose a valid library cursor.")
    prefix = f"studio.library:{library.scope}:"
    position = library._key(*after) if any(after) else prefix
    page, used, cursor = [], 64, None
    with library.lock:
        while True:
            rows = library.graph.query("MATCH (d:Doc) WHERE d.key STARTS WITH $prefix AND d.key > $after "
                                       "RETURN d.key AS key ORDER BY d.key LIMIT 1",
                                       {"prefix": prefix, "after": position})
            if not rows:
                break
            position = rows[0]["key"]
            package = import_package(library.graph.get_doc(position))
            size = len(json.dumps(package).encode())
            if size > MAX_BYTES - 2048:
                raise ValueError("This package exceeds the pool sharing size limit.")
            if used + size > MAX_BYTES - 1024:
                cursor = [page[-1]["id"], page[-1]["version"]]
                break
            page.append(package)
            used += size + 2
    return {"packages": page, "next": cursor}


def _storage(ui):
    with _INITIALIZE:
        if getattr(ui, "_studio_pool_store", None) is None:
            if not ui.root:
                raise ValueError("Start the daemon to share packages with your pool.")
            ui._studio_pool_store = StudioStore(Path(ui.root) / "studio-library.ladybug")
        return ui._studio_pool_store


def _local(ui, membership):
    store = _storage(ui)
    scope = "pool:" + hashlib.sha256(membership.key).hexdigest()
    return LocalLibrary(store.graph, scope, lock=store.lock)


def answer(handler, ui, path, body=None):
    """Answer package requests after the daemon authenticates and opens the request."""
    if path != ENDPOINT:
        return False
    membership = project_enrollment._matched(handler._sealing(), memberships(ui.pool_key_path)) if ui else None
    if membership is None:
        handler._send(403, {"error": "Package sharing requires a current pool and a sealed request."})
        return True
    try:
        library = _local(ui, membership)
        if handler.command in {"GET", "HEAD"}:
            handler._send(200, _page(library, handler.path))
        elif handler.command == "POST":
            if not body or len(body) > MAX_BYTES:
                raise ValueError("Choose a package smaller than 3 MB.")
            request = json.loads(body)
            if not isinstance(request, dict) or set(request) != {"package"}:
                raise ValueError("Package sharing accepts one declared package.")
            handler._send(201, {"package": library.publish(_shareable(request["package"]))})
        else:
            handler._send(405, {"error": "Choose a supported package action."})
    except (ValueError, OSError, TypeError) as exc:
        handler._send(400, {"error": str(exc)})
    return True


class MemberLibrary:
    """Discover and exchange immutable package versions with authenticated devices."""

    def __init__(self, local, members, authorize=lambda: None):
        self.local, self.members, self.authorize = local, members, authorize
        self.unavailable = []

    def _request(self, member, package=None):
        self.authorize()
        endpoint = member.base_url + ENDPOINT
        if not member.beacon or not member.beacon.cert or not endpoint.startswith("https://"):
            raise ValueError("Package sharing requires an enrolled encrypted device.")

        def guard(url):
            if url != endpoint:
                raise ValueError("Package exchange cannot redirect to another address.")
            return url

        raw = json.dumps({"package": package}).encode() if package is not None else None
        options = Options(token=member.token, headers={sealing.HEADER: "2", "Content-Type": "application/json"},
                          retry=ONCE, timeout=5, guard=guard, error_body_limit=MAX_BYTES)
        packages, after = [], None
        while True:
            self.authorize()
            endpoint = member.base_url + ENDPOINT + ("?" + urllib.parse.urlencode({"after": json.dumps(after)}) if after else "")
            response = self._response(endpoint, raw, options)
            values = [response["package"]] if raw is not None else response.get("packages")
            if not isinstance(values, list):
                raise ValueError("The pool device returned an invalid package list.")
            checked = [import_package(value) for value in values]
            if raw is not None:
                if len(checked) != 1 or checked[0]["digest"] != package["digest"]:
                    raise ValueError("The pool device acknowledged different package content.")
                return checked
            keys = [value["id"] + "@" + value["version"] for value in checked]
            if keys != sorted(set(keys)) or (after and any(key <= "@".join(after) for key in keys)):
                raise ValueError("The pool device returned an invalid package page.")
            packages.extend(checked)
            cursor = response.get("next")
            if cursor is None:
                return packages
            if not checked or cursor != [checked[-1]["id"], checked[-1]["version"]]:
                raise ValueError("The pool device returned an invalid package cursor.")
            after = cursor

    @staticmethod
    def _response(endpoint, raw, options):
        with open_stream(endpoint, method="POST" if raw is not None else "GET", data=raw, options=options) as response:
            if response.headers.get(sealing.HEADER) != "1":
                raise ValueError("The package response was not encrypted.")
            data = response.read(MAX_BYTES + sealing.NONCE_BYTES + 17)
            if len(data) > MAX_BYTES + sealing.NONCE_BYTES + 16:
                raise ValueError("The package response exceeds its size limit.")
            data = getattr(response, "sealed", Sealed()).open(response.status, response.headers, data)
        response = json.loads(data)
        if not isinstance(response, dict):
            raise ValueError("The pool device returned an invalid package response.")
        return response

    def _members(self):
        try:
            return self.members()
        except PermissionError:
            raise
        except (DiscoveryError, ServerError, OSError, ValueError):
            self.unavailable.append("Pool discovery is unavailable; this device keeps its saved packages.")
            return []

    def publish(self, package):
        self.authorize()
        checked = self.local.publish(_shareable(package))
        self.unavailable = []
        for member in self._members():
            try:
                self._request(member, checked)
            except PermissionError:
                raise
            except (ServerError, OSError, ValueError, KeyError, TypeError):
                self.unavailable.append("A pool device could not accept this version; the local package is saved.")
        return checked

    def search(self, query="", tags=None):
        self.authorize()
        self.unavailable = []
        values, conflicts = {}, set()
        batches = [self.local.search()]
        for member in self._members():
            try:
                batches.append(self._request(member))
            except PermissionError:
                raise
            except (ServerError, OSError, ValueError, KeyError, TypeError):
                self.unavailable.append("A pool device is unavailable; its packages could not be checked.")
        for batch in batches:
            for package in batch:
                key = (package["id"], package["version"])
                previous = values.get(key)
                if previous and previous["digest"] != package["digest"]:
                    conflicts.add(key)
                values[key] = package
        if conflicts:
            self.unavailable.append("Conflicting package versions were hidden. Publish a new version to resolve them.")
        return [package for key, package in sorted(values.items()) if key not in conflicts
                and query.casefold() in " ".join([package["name"], package["description"], *package["tags"]]).casefold()
                and set(tags or []).issubset(package["tags"])]

    def get(self, package_id, version):
        return next((package for package in self.search()
                     if package["id"] == package_id and package["version"] == version), None)


def provider(ui):
    """Return the active pool's package provider using current enrolled members."""
    rows = memberships(ui.pool_key_path)
    if not rows:
        raise ValueError("Join a pool to share packages. File sharing is available here.")
    selected = rows[0]

    def authorize():
        current = memberships(ui.pool_key_path)
        if not current or current[0] != selected:
            raise PermissionError("The active pool changed; open its library again.")

    def members():
        authorize()
        return Member.discover(discovery=Discovery(key=selected.key, group=selected.group,
                               pool_key_path=ui.pool_key_path, port=ui.discovery_port), timeout=5)

    return MemberLibrary(_local(ui, selected), members, authorize)
