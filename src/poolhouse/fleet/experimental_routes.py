"""Route classes from the experimental layer, loaded by name when the build holds them."""

from __future__ import annotations

from importlib import import_module

NAMES = ("poolhouse.fleet.gym_recording_routes:GymRecordingRoutes", "poolhouse.fleet.gym_routes:GymRoutes")
PREFIXES = ("poolhouse.fleet.gym_", "poolhouse.gym")


def load() -> tuple[type, ...]:
    """The route classes this build holds, in the order a request meets them."""
    found: list[type] = []
    for name in NAMES:
        module, _, attribute = name.partition(":")
        try:
            found.append(getattr(import_module(module), attribute))
        except ModuleNotFoundError as exc:
            if not (exc.name or "").startswith(PREFIXES):
                raise
    return tuple(found)
