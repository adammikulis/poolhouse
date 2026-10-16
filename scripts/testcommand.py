"""The pytest options every ``scripts/test`` run shares."""

from __future__ import annotations

UNUSED_PLUGINS = ("-p", "no:langsmith_plugin")
"""Installed pytest plugins no test uses; each costs every pytest process, the controller and every worker, an import."""
