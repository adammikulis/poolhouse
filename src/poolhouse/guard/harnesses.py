"""The harness records: the instruction and settings names each coding harness Poolhouse launches reads from a project.

`poolhouse.claude`, `poolhouse.codex` and `poolhouse.pi` each look their own record up here beside the argv they build, so a harness module
without a record fails at import, and `guard.runs_later` refuses a change to any of these names at any depth (docs/design-isolated-launcher.md,
4.4.2). The file holds data only, so the hooks read it without importing a launcher. The instruction files of a tool Poolhouse does not
launch (Copilot, Windsurf, Aider, Continue, Cline, Cursor) sit in dot names and are non-plain by position.
"""

from __future__ import annotations

__all__ = ["RECORDS", "names"]

RECORDS = {
    "claude": ("CLAUDE.md", ".claude", ".mcp.json"),
    "codex": ("AGENTS.md", ".codex"),
    "pi": (".pi", "GEMINI.md"),
}


def names() -> frozenset[str]:
    """Every name of every record, as the launchers spell them."""
    return frozenset(name for record in RECORDS.values() for name in record)
