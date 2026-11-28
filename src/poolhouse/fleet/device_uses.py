"""``/ui/device-uses``: what each device of the pool can do for a person now.

The words are ``contracts/device-uses.json``; which state a use is in comes from what this daemon holds.
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

from poolhouse import contracts, node_pool

from .environment import catalog_for

__all__ = ["DeviceUsesRoutes", "report"]

CONTRACT = "device-uses.json"
FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
FRAMEWORKS = {"apple": "mlx", "nvidia": "torch-cuda", "amd": "torch-rocm"}
"""The training framework library of each vendor (`fleet.environment.CATALOG`); any other trains with ``torch-cpu``."""
LIBRARY_CACHE_S = 30.0
"""How long the installed training libraries are remembered: reading them starts the job interpreter, which takes seconds,
and the page asks again every few seconds."""


def _libraries(ui: Any, vendor: str, fresh: bool) -> list[tuple[str, int]] | None:
    """``(name, size_mb)`` of each training library still to install; None when this daemon installs none."""
    environment = ui.environment
    if environment is None:
        return None
    held = ui.__dict__.get("_training_libraries")
    if held and not fresh and held[0] == vendor and time.monotonic() - held[1] < LIBRARY_CACHE_S:
        return held[2]
    wanted = ("core", FRAMEWORKS.get(vendor, "torch-cpu"))
    needed = [library for library in catalog_for(vendor) if library.name in wanted]
    try:
        have = environment.installed()
        missing = [(library.name, library.size_mb) for library in needed
                   if not environment.library_state(library, have)["installed"]]
    except (OSError, ValueError, subprocess.SubprocessError):
        missing = [(library.name, library.size_mb) for library in needed]
    ui.__dict__["_training_libraries"] = (vendor, time.monotonic(), missing)
    return missing


def _holdings(ui: Any, fresh: bool) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    """The state of each use this computer offers, the figures its words name, and what training still has to install."""
    device = ui.report() if callable(ui.report) else {}
    states: dict[str, str] = {"guests": "ready"}
    said: dict[str, Any] = {}
    install: dict[str, Any] = {}
    if ui.models is not None:
        count = len(ui.models.all())
        said.update(count=count, free=f"{ui.models.free_gb():.0f}")
        states["models"] = "ready" if count else "empty"
        states["benchmarks"] = "ready" if count else "needs_model"
        running = sorted({str(model) for server in (ui.serving.public() if ui.serving is not None else [])
                          for model in server.get("models") or []})
        said["model"] = ", ".join(running)
        states["chat"] = "running" if running else "downloaded" if count else "needs_model"
    if ui.runner is not None:
        jobs = [job.state for job in list(ui.runner.jobs.values())]
        said.update(running=sum(state in ("preparing", "launching", "running") for state in jobs), queued=jobs.count("queued"))
        states["jobs"] = "busy" if said["running"] or said["queued"] else "idle"
    if "asr" in (device.get("speech") or []):
        states["speech"] = "ready"
    missing = _libraries(ui, str(device.get("vendor") or "cpu"), fresh)
    if missing is not None:
        sources = str(getattr(ui.settings, "download_sources", "") or "")
        said["size"] = sum(size for _, size in missing)
        states["training"] = "ready" if not missing else "internet_off" if sources == "lan" else "install"
        install = {"install": [name for name, _ in missing], **({} if sources else {"sources": "both"})}
    return states, said, install


def _card(use: dict[str, Any], state: str, said: dict[str, Any]) -> dict[str, Any]:
    words = use["states"][state]
    action = dict(words["action"])
    if "copy" in action:
        action["copy"] = action["copy"].format_map(said)
    return {"id": use["id"], "group": use["group"], "view": use.get("view", ""), "title": use["title"],
            "summary": use["summary"], "state": state, "badge": words["badge"],
            "detail": words["detail"].format_map(said), "action": action}


def _this_device(words: dict[str, Any], ui: Any, fresh: bool) -> dict[str, Any]:
    states, said, install = _holdings(ui, fresh)
    cards = [_card(use, states[use["id"]], said) for use in words["uses"] if use["id"] in states]
    for card in cards:
        if "install" in card["action"]:
            card["action"].update(install)
    return {"id": "self", "self": True, "name": ui.name, "heading": words["heading"], "intro": words["intro"],
            "note": words["note"], "uses": cards}


def _other_device(words: dict[str, Any], row: dict[str, Any], own: str) -> dict[str, Any]:
    name, fingerprint = str(row["name"]), str(row["fingerprint"])
    connected = row["state"] == "connected"
    said = {"name": name, "device": fingerprint, "own": own}
    named = FINGERPRINT.match(fingerprint) and FINGERPRINT.match(own)
    return {"id": fingerprint, "self": False, "name": name, "connected": connected,
            "status": words["connected" if connected else "away"], "choose": words["choose"], "back": words["back"],
            "heading": words["heading"].format_map(said), "intro": words["intro"], "note": words["note"].format_map(said),
            "uses": [_card(use, "developer", said) for use in words["uses"]] if named else []}


def report(ui: Any, *, fresh: bool = False) -> dict[str, Any]:
    """This computer and every other device of its pool, each with its uses; ``fresh`` reads the installed libraries again."""
    words = contracts.load(CONTRACT)
    held = ui.pool_status()
    pool = held if isinstance(held, dict) else {}
    own = str(pool.get("fingerprint") or "")
    others = [row for row in node_pool.rows(pool) if row["state"] != "this machine"]
    return {"version": words["version"], "groups": words["groups"], "limit": words["limit"], "adding": words["adding"],
            "others_heading": words["other_device"]["list_heading"],
            "devices": [_this_device(words["this_device"], ui, fresh),
                        *(_other_device(words["other_device"], row, own) for row in others)]}


class DeviceUsesRoutes:
    """The list the Devices screen, first run and the add-device form show."""

    def route(self) -> bool:
        if self.path == "/ui/device-uses" and self.method == "GET":
            self.send(200, report(self.ui, fresh=self.asked("fresh") == "1"))
            return True
        return super().route()
