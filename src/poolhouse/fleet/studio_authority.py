"""Bounded, revocable daemon execution identities for Studio subscriptions."""
from __future__ import annotations

import copy
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from poolhouse.graph import GraphStore
from poolhouse.studio.store import Conflict, digest

from . import studio_epoch

STRONG_ORIGINS = frozenset({"launch-ticket", "passphrase", "recovery-file"})


class StudioAuthority:
    """Only trusted daemon callbacks establish identities and resource authorization."""

    def __init__(self, root: Path, *, current: Any, owner: Any, resources: Any) -> None:
        self.root = Path(root).absolute()
        self.current, self.owner, self.resources = current, owner, resources
        self.lock = threading.RLock()
        with studio_epoch.directory(self.root, create=True):
            self.graph = GraphStore(self.root / "studio-authority.ladybug")

    def close(self) -> None:
        with self.lock:
            self.graph.close()

    def _context(self) -> dict:
        context = self.current()
        if (not isinstance(context, dict) or set(context) != {"pool", "device", "scope", "active"}
                or context["active"] is not True
                or any(not isinstance(context[key], str) or not context[key] for key in ("pool", "device", "scope"))
                or not studio_epoch.HEX.fullmatch(context["pool"]) or not studio_epoch.HEX.fullmatch(context["device"])):
            raise PermissionError("This device no longer holds Studio execution authority.")
        return context

    def _owner(self, principal: dict) -> dict:
        context = self._context()
        # owner() inspects the actual current server session and origin, never JSON flags.
        if principal.get("scope") != context["scope"] or self.owner(principal) not in STRONG_ORIGINS:
            raise PermissionError("An authenticated owner starts or changes this subscription.")
        return context

    def _audit(self, event: str, grant: dict) -> None:
        ident = "studio.authority.audit:" + secrets.token_hex(16)
        self.graph.upsert_node({"id": ident, "kind": "studio.authority.audit", "label": event})
        self.graph.put_doc(ident, {"event": event, "at": time.time(), "actor": "studio-daemon",
                                  "grant": grant["id"], "pool": grant["pool"], "epoch": grant["epoch"]})

    @staticmethod
    def principal(grant: dict) -> dict:
        return {"id": "studio-grant:" + grant["id"], "kind": "studio_subscription",
                "grant_id": grant["id"], "scope": grant["scope"]}

    def mint(self, principal: dict, subscription: str, manifest: dict, key: str) -> dict:
        """Explicit Start delegates this exact immutable resource manifest once."""
        context = self._owner(principal)
        if (not isinstance(subscription, str) or not subscription or not isinstance(key, str) or not key or len(key) > 256
                or not isinstance(manifest, dict) or set(manifest) != {"workflow", "source", "destinations", "resources"}):
            raise ValueError("A subscription and exact execution manifest are required.")
        manifest = copy.deepcopy(manifest)
        fingerprint = digest(manifest)
        candidate = {**context, "subscription": subscription, "manifest": manifest, "manifest_digest": fingerprint}
        if not self.resources(candidate, None, {}):
            raise PermissionError("A subscription resource is unavailable.")
        identity = "studio.authority.request:" + digest({"scope": context["scope"], "subscription": subscription, "key": key})
        with self.lock, studio_epoch.changing(self.root) as (fd, epochs), self.graph.transaction():
            current = self._owner(principal)
            if current != context or not self.resources(candidate, None, {}):
                raise PermissionError("Subscription authority changed before Start completed.")
            previous = self.graph.get_doc(identity)
            if previous:
                if previous["manifest"] != fingerprint:
                    raise Conflict("This Start identity already names another subscription configuration.")
                grant = self.graph.get_doc("studio.authority.grant:" + previous["grant"])
                anchor = epochs["pools"].get(context["pool"])
                epoch = anchor["epoch"] if anchor and anchor["active"] else None
                if (not grant or grant["revoked"] or grant["epoch"] != epoch
                        or any(grant[name] != context[name] for name in ("pool", "device", "scope"))):
                    raise PermissionError("This Start was revoked. Use a new explicit Start identity.")
                return copy.deepcopy(grant)
            epoch = studio_epoch.activate(fd, epochs, context["pool"])
            grant = {**candidate, "id": secrets.token_hex(16), "epoch": epoch, "revoked": False, "created_at": time.time()}
            grant.pop("active")
            ident = "studio.authority.grant:" + grant["id"]
            self.graph.upsert_node({"id": ident, "kind": "studio.authority.grant", "label": subscription})
            self.graph.put_doc(ident, grant)
            self.graph.put_doc(identity, {"manifest": fingerprint, "grant": grant["id"]})
            self._audit("subscription.delegated", grant)
            return copy.deepcopy(grant)

    def get(self, principal: dict) -> dict:
        """A service identity is valid only under the current device and revocation epoch."""
        context = self._context()
        ident = principal.get("grant_id", "")
        if not isinstance(ident, str) or len(ident) != 32:
            raise PermissionError("Unknown Studio service identity.")
        with self.lock:
            grant = self.graph.get_doc("studio.authority.grant:" + ident)
        if (not grant or principal != self.principal(grant) or grant["revoked"]
                or any(grant[key] != context[key] for key in ("pool", "device", "scope"))
                or grant["epoch"] != studio_epoch.read(self.root, context["pool"])
                or digest(grant["manifest"]) != grant["manifest_digest"]):
            raise PermissionError("This subscription's execution authority was revoked.")
        return copy.deepcopy(grant)

    def authorize(self, principal: dict, node: dict | None, inputs: dict) -> bool:
        try:
            grant = self.get(principal)
            if node is not None:
                nodes = grant["manifest"]["workflow"]["graph"]["nodes"]
                declared = next((item for item in nodes if item["id"] == node.get("id")), None)
                if declared is None or digest(declared) != digest(node):
                    return False
            return bool(self.resources(grant, node, inputs))
        except (KeyError, OSError, ValueError, RuntimeError, StopIteration):
            return False

    def revoke(self, principal: dict, ident: str) -> dict:
        context = self._owner(principal)
        with self.lock, self.graph.transaction():
            key = "studio.authority.grant:" + ident
            grant = self.graph.get_doc(key)
            if not grant or any(grant[name] != context[name] for name in ("scope", "pool", "device")):
                raise KeyError("No such subscription authority.")
            if not grant["revoked"]:
                grant["revoked"] = True
                self.graph.put_doc(key, grant)
                self._audit("subscription.revoked", grant)
            return copy.deepcopy(grant)

    def epoch(self, principal: dict) -> str:
        context = self._owner(principal)
        return studio_epoch.read(self.root, context["pool"])
