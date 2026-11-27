"""The `hook-snippet` and `install-hooks` commands of `poolhouse-workspace`."""

from __future__ import annotations

import argparse

from poolhouse import agent_hooks, authority, home
from poolhouse.command import flag
from poolhouse.log import say, warn
from poolhouse.workspace import onboard

SNIPPET = [flag("tool", choices=agent_hooks.AGENTS)]
INSTALL = [flag("--project", required=True, help="the project folder whose own local settings get the hooks"),
           flag("--only", action="append", default=[], choices=agent_hooks.AGENTS,
                help="write one agent's hooks only")]


def snippet(args: argparse.Namespace) -> int:
    """Print the setting a tool needs so it runs `nudge`; writes nothing."""
    say(onboard.hook_snippet(args.tool, args.agent or "NAME"), end="")
    return 0


def install(args: argparse.Namespace) -> int:
    """Write the nudge hooks into one project's own settings; exit 1 when a file is not the project's own or cannot be read."""
    authority.require("workspace.setup", "workspace install-hooks")
    try:
        done = agent_hooks.install(home.expand(args.project), tuple(args.only) or agent_hooks.AGENTS)
    except (OSError, ValueError) as err:
        warn(f"the hooks were not written: {err}")
        return 1
    for agent, (path, events) in done.items():
        say(f"{agent}: {', '.join(events)} written to {path}")
    if "codex" in done:
        say("codex reads a project's .codex/config.toml only for a project it trusts")
    return 0
