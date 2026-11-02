"""The other machines on this LAN, and how to run work on them."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_WHERE = {
    "BENCH_KIND": "calibration",
    "calibrate": "calibration",
    "measure": "calibration",
    "ChatError": "chat",
    "Target": "chat",
    "targets": "chat",
    "Conversation": "conversations",
    "Conversations": "conversations",
    "Message": "conversations",
    "Daemon": "api",
    "DaemonOptions": "daemon",
    "make_handler": "api",
    "load_or_create_token": "daemon",
    "serve": "daemon",
    "REPORT_GROUP": "device",
    "device_report": "device",
    "registered_reports": "device",
    "resolve_report": "device",
    "stdlib_device_report": "device",
    "safe_relpath": "files",
    "DaemonError": "jobs",
    "Job": "jobs",
    "JobRunner": "jobs",
    "Suggestion": "catalogue",
    "families": "catalogue",
    "family_of": "catalogue",
    "how_many": "catalogue",
    "is_unfiltered": "catalogue",
    "popular": "catalogue",
    "searched_count": "catalogue",
    "searched_families": "catalogue",
    "suggestions": "catalogue",
    "Downloads": "models",
    "Getting": "models",
    "Model": "models",
    "Models": "models",
    "draft_beside": "models",
    "ModelError": "weights",
    "resolve": "weights",
    "Candidate": "pool",
    "Requires": "pool",
    "Score": "pool",
    "candidates": "pool",
    "choose": "pool",
    "eligible": "pool",
    "soonest": "pool",
    "Rates": "rates",
    "Member": "remote",
    "MemberError": "remote",
    "Endpoint": "serving",
    "Served": "serving",
    "Serving": "serving",
    "discover_serving": "serving",
    "Placement": "work",
    "Unit": "work",
    "run": "work",
}

BENCH_KIND: Any
Candidate: Any
ChatError: Any
Conversation: Any
Conversations: Any
Daemon: Any
DaemonOptions: Any
DaemonError: Any
Downloads: Any
Endpoint: Any
Getting: Any
Job: Any
JobRunner: Any
Message: Any
Model: Any
ModelError: Any
Models: Any
Member: Any
MemberError: Any
Placement: Any
REPORT_GROUP: Any
Rates: Any
Requires: Any
Score: Any
Served: Any
Serving: Any
Suggestion: Any
Target: Any
Unit: Any
calibrate: Any
candidates: Any
choose: Any
device_report: Any
discover_serving: Any
draft_beside: Any
eligible: Any
families: Any
family_of: Any
how_many: Any
is_unfiltered: Any
load_or_create_token: Any
make_handler: Any
measure: Any
popular: Any
registered_reports: Any
resolve: Any
resolve_report: Any
run: Any
safe_relpath: Any
searched_count: Any
searched_families: Any
serve: Any
soonest: Any
stdlib_device_report: Any
suggestions: Any
targets: Any

__all__ = [
    'BENCH_KIND',
    'REPORT_GROUP',
    'Candidate',
    'ChatError',
    'Conversation',
    'Conversations',
    'Daemon',
    'DaemonError',
    'DaemonOptions',
    'Downloads',
    'Endpoint',
    'Getting',
    'Job',
    'JobRunner',
    'Member',
    'MemberError',
    'Message',
    'Model',
    'ModelError',
    'Models',
    'Placement',
    'Rates',
    'Requires',
    'Score',
    'Served',
    'Serving',
    'Suggestion',
    'Target',
    'Unit',
    'calibrate',
    'candidates',
    'choose',
    'device_report',
    'discover_serving',
    'draft_beside',
    'eligible',
    'families',
    'family_of',
    'how_many',
    'is_unfiltered',
    'load_or_create_token',
    'make_handler',
    'measure',
    'popular',
    'registered_reports',
    'resolve',
    'resolve_report',
    'run',
    'safe_relpath',
    'searched_count',
    'searched_families',
    'serve',
    'soonest',
    'stdlib_device_report',
    'suggestions',
    'targets',
]


def __getattr__(name: str) -> Any:
    """The named export, loading the submodule that defines it."""
    where = _WHERE.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{where}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
