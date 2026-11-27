"""The harness records: the instruction and settings names each coding harness Poolhouse launches reads from a project.

`poolhouse.claude`, `poolhouse.codex`, `poolhouse.pi` and `poolhouse.opencode` each look their own record up here beside the argv they build, so a harness module
without a record fails at import. Publication applies no change to a settings name at any depth, because a harness reads hooks or
permissions from it, and applies an instruction file like any other file, naming it as one that runs later
(docs/design-isolated-launcher.md, 4.4). The file holds data only, so the hooks read it without importing a launcher. The instruction files
of a tool Poolhouse does not launch (Copilot, Windsurf, Aider, Continue, Cline, Cursor) sit in dot names.
"""

from __future__ import annotations

__all__ = ["RECORDS", "names", "settings"]

RECORDS = {
    "claude": ("CLAUDE.md", ".claude", ".mcp.json"),
    "codex": ("AGENTS.md", ".codex"),
    "pi": (".pi", "GEMINI.md"),
    "opencode": (".opencode", "opencode.json", "opencode.jsonc"),
}


def names() -> frozenset[str]:
    """Every name of every record, as the launchers spell them."""
    return frozenset(name for record in RECORDS.values() for name in record)


def settings() -> frozenset[str]:
    """The names of every record that are settings and not instructions (a Markdown file): a configuration file or folder a harness reads
    hooks, permissions, plugins or servers from."""
    return frozenset(name for name in names() if not name.endswith(".md"))
