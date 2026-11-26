"""``/ui/room``: sizing the wiring limit for a model. Read-only: Poolhouse never changes the limit."""

from __future__ import annotations

import platform
import urllib.parse

from poolhouse import hub
from poolhouse.serve import wired, wired_state

__all__ = ["RoomRoutes"]

LOOPBACK = "127.0.0.1"
PATH = "/ui/room"


def _origin_ok(origin: str, host: str) -> bool:
    """An Origin header, when there is one, must name the host the request was sent to."""
    return not origin or urllib.parse.urlparse(origin).netloc.lower() == host.lower()


def _machine(hooks: wired.Hooks) -> dict[str, object]:
    total = hooks.total_bytes()
    kept = wired_state.state(hooks)
    return {"supported": (hooks.system or platform.system()) == "Darwin", "total": total, "default_mb": int(total * 0.75) // wired.MIB,
            "max_mb": wired.max_mb(total), "min_mb": wired.MIN_MB,
            "warn_below_bytes": wired.warn_below_bytes(total),
            "reserve_bytes": wired.reserve_bytes(total), "live_mb": kept.live_mb,
            "kept": kept.kept, "kept_mb": kept.kept_mb, "drift": kept.drift}


class RoomRoutes:
    """Everything here is read-only."""

    def _hooks(self) -> wired.Hooks:
        return getattr(self.ui, "room_hooks", None) or wired.Hooks()

    def route(self) -> bool:
        if self.path == PATH and self.method == "GET":
            return self._room()
        return super().route()

    def _room(self) -> bool:
        hooks = self._hooks()
        installed = hub.discover(formats=("gguf",))
        out: dict[str, object] = {"machine": _machine(hooks),
                                  "kv_types": list(wired.KV_TYPES),
                                  "contexts": list(wired.CONTEXTS),
                                  "models": sorted({m.id for m in installed})}
        name = self.asked("model").strip()
        if name:
            try:
                # the asked name is only compared with what is installed; the file read is
                # the installed row's own path, never a path the request named
                row = next((m for m in installed if name in (m.id, m.name, str(m.path))), None)
                if row is None:
                    raise FileNotFoundError(f"{name} is not installed")
                ask = wired.Ask(int(self.asked("ctx", "0")), self.asked("kv", "q8_0"),
                                self.asked("mtp", "1") not in ("0", "false", "off"))
                out["plan"] = wired.plan(str(row.path), ask, hooks).as_dict()
            except (FileNotFoundError, ValueError) as no:
                self.send(400, {"error": str(no)[:300]})
                return True
        self.send(200, out)
        return True
