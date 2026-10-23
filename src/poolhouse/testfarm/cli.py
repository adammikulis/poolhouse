"""``poolhouse-test``: describe a project to the runner (``init``), validate that description (``check``) and list its units (``list``)."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from poolhouse import home
from poolhouse.command import Group, flag, option
from poolhouse.log import say, warn
from poolhouse.testfarm.core import project, select, state
from poolhouse.testfarm.model import ConfigError, exitcodes

SKELETON = """schema = 1

# The command that prints one test id per line, and the seconds it may run.
[list]
run = ["sh", "tests/list.sh"]
timeout = 60

# The command that runs the ids it is given ({ids} is replaced by them), and the seconds one run may take.
[run]
argv = ["sh", "{ids}"]
timeout = 600
"""
"""What `init` writes: the two commands every project gives, for the person to replace with its own."""


def state_location() -> Path:
    """The runner's state directory; it is never inside the pool's home, and a pool home that contains it is an error."""
    where = state.state_dir(home.user_home())
    if where.is_relative_to(home.state()) or home.state().is_relative_to(where):
        raise ConfigError(f"the runner's state location {where} is under the pool's home {home.state()}; the pool's home holds secrets "
                          "the runner's children must not reach, so move one of them")
    return where


def _root(args: argparse.Namespace) -> Path:
    """The project root: the first directory at or above ``--root`` with a testfarm.toml or a .git entry."""
    return project.find_root(Path(args.root))


def init(args: argparse.Namespace) -> int:
    """Write a testfarm.toml to fill in; an existing file is never overwritten."""
    target = Path(args.root).resolve() / project.CONFIG_NAME
    if target.exists():
        warn(f"poolhouse-test: {target} exists and is left as it is")
        return exitcodes.REFUSED
    target.write_text(SKELETON, encoding="utf-8")
    say(f"wrote {target}: put your own LIST and RUN commands in it, then run `poolhouse-test check`")
    return exitcodes.OK


def check(args: argparse.Namespace) -> int:
    """Validate the project file, run LIST and print what the runner found."""
    try:
        say("\n".join(project.check(_root(args), state_location())))
    except ConfigError as error:
        warn(f"poolhouse-test: {error}")
        return error.status
    return exitcodes.OK


def listing(args: argparse.Namespace) -> int:
    """Print the units LIST gives, narrowed by the selectors."""
    try:
        config = project.load(_root(args))
        chosen = select.select(project.list_units(_root(args), config).units, config, args.select)
    except ConfigError as error:
        warn(f"poolhouse-test: {error}")
        return error.status
    if args.json:
        say(json.dumps([dataclasses.asdict(unit) for unit in chosen], indent=2))
    else:
        say("\n".join(unit.name + (f"  ({len(unit.ids)} ids)" if len(unit.ids) > 1 else "") for unit in chosen))
    return exitcodes.OK


_ROOT = flag("--root", default=".", help="the project directory (default: the current directory)")

COMMAND = Group("poolhouse-test", "Run a project's tests: describe it in testfarm.toml, check the description, list its units.")
COMMAND.add("init", init, help="write a testfarm.toml to fill in", options=(_ROOT,))
COMMAND.add("check", check, help="validate testfarm.toml, run LIST and print the units found", options=(_ROOT,))
COMMAND.add("list", listing, help="print the units LIST gives", options=(
    _ROOT, flag("select", nargs="*", help="@SET, an id, a glob over ids or a unit name"), option("json")))
command = COMMAND.run


if __name__ == "__main__":
    sys.exit(command())
