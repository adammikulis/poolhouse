"""Readiness of the local Agents SDK runtime."""
import json
import platform
import sys
from importlib import import_module
from pathlib import Path

from poolhouse import provision
from poolhouse.log import say


def report():
    """Return the running immutable artifact's agent readiness and provenance."""
    error = problem()
    marker = Path(__file__).parent / "fleet" / "built-from"
    commit = marker.read_text().strip() if marker.is_file() else ""
    say(json.dumps({"commit": commit, "platform": sys.platform,
                      "machine": platform.machine(), "runtime_ready": not error,
                      "runtime_error": error}))
    return 1 if error else 0


def chat_runtime():
    """Load the actual optional Chat implementation without constructing a client."""
    return import_module("poolhouse.fleet.sdk_chat")


def _absent(*, install: bool) -> str:
    """Why the agent runtime, which is not here, cannot be had: empty when it is installed now (``install``) or, asked
    without installing, when it will be at first use."""
    needs = "Local chat and Qwen agents require the agent runtime (poolhouse[agents]), which could not be installed here: "
    if not install:
        why = provision.refusal()
        return needs + why if why else ""
    try:
        provision.ensure("agents", "agents")
    except ImportError as exc:
        return needs + str(exc)
    return ""


def problem(*, install=False):
    """Return a dependency error when the local chat runtime cannot import. With ``install`` a runtime that is not here
    is installed first; without it a runtime that can be installed at first use is no problem."""
    try:
        try:
            runtime = import_module("agents")
        except ModuleNotFoundError as exc:
            if exc.name != "agents":
                raise
            said = _absent(install=install)
            if said or not install:
                return said
            runtime = import_module("agents")
        for name in ("Agent", "ModelSettings", "OpenAIChatCompletionsModel", "RunConfig", "Runner"):
            getattr(runtime, name)
    except (ImportError, AttributeError):
        return "The installed chat runtime is incomplete or incompatible. Repair the app runtime."
    try:
        chat_runtime()
    except ImportError:
        return "The installed chat runtime is incomplete or incompatible. Repair the app runtime."
    return ""
