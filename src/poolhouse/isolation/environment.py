"""The environment a confined harness runs with: a closed allow-list, never a copy of the launcher's own.

A variable the table does not name does not exist for any harness, whatever the caller's environment, a project file or a grant holds
(docs/design-isolated-launcher.md, 3.4). A toolchain grant contributes entries of ``PATH`` and no variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from poolhouse.isolation.spec import HARNESSES

__all__ = ["ALWAYS", "BY_HARNESS", "INTERACTIVE", "MODEL_VARS", "OFFLINE", "WINDOW", "Inputs", "allowed", "environment"]

ALWAYS = ("PATH", "HOME", "TMPDIR", "LANG", "POOLHOUSE_AGENT")
"""Set for every harness. ``POOLHOUSE_AGENT`` marks the tools the harness runs; no person check relies on it (3.5)."""
INTERACTIVE = ("TERM", "COLORTERM", "NO_COLOR")
"""Taken from the person's terminal, and only in an interactive launch."""
MODEL_VARS = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
              "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")
"""Each names the one model the bridge serves."""
OFFLINE = {
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_DISABLE_1M_CONTEXT": "1",
    "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
    "CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK": "1",
    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
    "DISABLE_TELEMETRY": "1",
}
"""What keeps Claude Code from calling out to anything but the bridge. The attribution header is a line at the start of the system
prompt that changes with every request, so a local server's prompt cache never matched past it: without it a second session on the same
server reuses the first one's prompt."""
WINDOW = ("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "CLAUDE_CODE_AUTO_COMPACT_WINDOW")
CALLER_SET = ("CLAUDE_CODE_EXTRA_BODY", "CLAUDE_CODE_EFFORT_LEVEL")
"""Passed on only where the caller sets them."""
MIN_COMPACT_WINDOW = 100000
"""The window from which Claude Code is told to compact; the value `claude.MIN_COMPACT_WINDOW` carries today."""
CODEX_KEY = "POOLHOUSE_API_KEY"
OPENCODE_OFFLINE = {"OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true", "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
                    "OPENCODE_DISABLE_CLAUDE_CODE": "true", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true", "OPENCODE_DISABLE_TERMINAL_TITLE": "true"}
"""What keeps OpenCode from calling out to anything but the bridge, and from reading the person's Claude Code files."""

BY_HARNESS: dict[str, tuple[str, ...]] = {
    "claude": ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_TMPDIR", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", *MODEL_VARS, *OFFLINE,
               *WINDOW, *CALLER_SET),
    "codex": ("CODEX_HOME", CODEX_KEY),
    "pi": ("PI_CODING_AGENT_DIR", "PI_OFFLINE", "POOLHOUSE_NONINTERACTIVE"),
    "opencode": ("OPENCODE_CONFIG", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", *OPENCODE_OFFLINE),
}
"""The variables a harness has besides the always set: the closed table of 3.4."""


@dataclass(frozen=True, slots=True)
class Inputs:
    """What the launcher knows when it builds the environment: the agent home, the directories of ``PATH``, the bridge and its token."""

    harness: str
    home: str
    path: Sequence[str] = ()
    base_url: str = ""
    token: str = ""
    alias: str = ""
    context: int = 0
    interactive: bool = False
    terminal: Mapping[str, str] = field(default_factory=dict)
    extras: Mapping[str, str] = field(default_factory=dict)


def allowed(harness: str, interactive: bool = False) -> frozenset[str]:
    """Every variable name a launch of ``harness`` may have; a name outside it never reaches the process."""
    if harness not in HARNESSES:
        raise ValueError(f"no coding harness called {harness}")
    return frozenset((*ALWAYS, *(INTERACTIVE if interactive else ()), *BY_HARNESS[harness]))


def _claude(inputs: Inputs) -> dict[str, str]:
    home = inputs.home
    out = {"CLAUDE_CONFIG_DIR": str(Path(home) / ".claude-config"), "CLAUDE_CODE_TMPDIR": str(Path(home) / "tmp"),
           "ANTHROPIC_BASE_URL": inputs.base_url.rstrip("/"), "ANTHROPIC_AUTH_TOKEN": inputs.token, **OFFLINE}
    out.update(dict.fromkeys(MODEL_VARS, inputs.alias))
    if inputs.context:
        out[WINDOW[0]] = str(int(inputs.context))
        if inputs.context >= MIN_COMPACT_WINDOW:
            out[WINDOW[1]] = str(int(inputs.context))
    out.update({name: inputs.extras[name] for name in CALLER_SET if inputs.extras.get(name)})
    return out


def _codex(inputs: Inputs) -> dict[str, str]:
    return {"CODEX_HOME": str(Path(inputs.home) / ".codex-home"), CODEX_KEY: inputs.token}


def _pi(inputs: Inputs) -> dict[str, str]:
    return {"PI_CODING_AGENT_DIR": str(Path(inputs.home) / ".pi-config"), "PI_OFFLINE": "1", "POOLHOUSE_NONINTERACTIVE": "1"}


def _opencode(inputs: Inputs) -> dict[str, str]:
    config = Path(inputs.home) / ".opencode-config"
    return {"OPENCODE_CONFIG": str(config / "opencode.json"), "XDG_CONFIG_HOME": str(config), "XDG_DATA_HOME": str(Path(inputs.home) / "data"),
            "XDG_CACHE_HOME": str(Path(inputs.home) / "cache"), "XDG_STATE_HOME": str(Path(inputs.home) / "state"), **OPENCODE_OFFLINE}


def environment(inputs: Inputs) -> dict[str, str]:
    """The environment of a launch: the always set, the terminal names of an interactive launch and the harness's own table. Nothing is
    read from the process environment, so no variable of the launcher, the person's shell or a project reaches the harness."""
    names = allowed(inputs.harness, inputs.interactive)
    made = {"PATH": os.pathsep.join(inputs.path), "HOME": inputs.home, "TMPDIR": str(Path(inputs.home) / "tmp"), "LANG": "en_US.UTF-8",
            "POOLHOUSE_AGENT": "1"}
    if inputs.interactive:
        made.update({name: inputs.terminal[name] for name in INTERACTIVE if inputs.terminal.get(name)})
    made.update({"claude": _claude, "codex": _codex, "pi": _pi, "opencode": _opencode}[inputs.harness](inputs))
    return {name: value for name, value in made.items() if name in names}
