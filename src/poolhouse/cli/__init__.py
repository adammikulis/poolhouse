"""``poolhouse`` -- one command in front of every ``poolhouse-<command>``.

``poolhouse chat ...`` runs ``poolhouse-chat ...`` in this process; bare, it
starts the app the way ``poolhouse.fleet.launch`` does.
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from poolhouse.cli.registry import PREFIX, Command, registry
from poolhouse.log import say, warn

__all__ = ["PREFIX", "commands", "load", "main"]

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def commands() -> dict[str, str]:
    """The declared command names and lazy targets, including runtime."""
    return {word: command.target for word, command in registry(PYPROJECT).items()}


def load(target: str) -> Callable[..., int | None]:
    """The callable ``module:attr`` names."""
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr or "main")


def _parser(table: dict[str, Command]) -> argparse.ArgumentParser:
    words = sorted(word for word, command in table.items() if command.public)
    listed = "\n".join(f"  {w.replace('-', ' ')}" for w in words)
    ap = argparse.ArgumentParser(
        prog="poolhouse",
        usage="poolhouse [--list] [<command> [args...]]",
        description="Open Poolhouse, or run a command.",
        epilog=f"commands:\n{listed}\n\n"
               "poolhouse help <command> is that command's help; poolhouse help lists them.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list commands with descriptions")
    ap.add_argument("--all", action="store_true", help="include internal commands with --list")
    try:
        installed_version = version("poolhouse")
    except PackageNotFoundError:
        installed_version = "0+unknown"
    ap.add_argument("--version", action="version", version=f"Poolhouse {installed_version}")
    return ap


def _unknown(words: list[str], table: dict[str, Command]) -> int:
    asked = " ".join(words)
    spoken = {w.replace("-", " "): w for w in table}
    near = difflib.get_close_matches(asked, spoken, n=5, cutoff=0.4)
    near = near or [s for s in sorted(spoken) if s.startswith(words[0][:2])]
    hint = ", ".join(near) if near else "poolhouse --list shows them all"
    warn(f"poolhouse: '{asked}' is not a command; nearest: {hint}")
    return 2


def listing(table: dict[str, Command] | None = None, *, internal: bool = False) -> str:
    """Declared command names and descriptions without loading their handlers."""
    table = registry(PYPROJECT) if table is None else table
    width = max((len(word) for word in table), default=0)
    return "\n".join(f"{word.replace('-', ' '):<{width}}  {command.summary}"
                     for word, command in sorted(table.items()) if internal or command.public)


def _help_parser(table: dict[str, Command]) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="poolhouse help",
        usage="poolhouse help [--all] [<command>...]",
        description="List commands, or show help for a command.",
        epilog=listing(table), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="include internal commands")
    ap.add_argument("words", nargs="*", metavar="command")
    return ap


def help_main(argv: list[str] | None = None) -> int:
    """List commands or show one command's help."""
    argv = list(sys.argv[1:] if argv is None else argv)
    table = registry(PYPROJECT)
    ap = _help_parser(table)
    args = ap.parse_args(argv)
    words = list(args.words)
    if not words:
        if args.all:
            ap.epilog = listing(table, internal=True)
        say(ap.format_help())
        return 0
    for k in range(len(words), 0, -1):
        word = "-".join(words[:k])
        if word in table and word != "help":
            try:
                result = load(table[word].target)([*words[k:], "--help"])
            except SystemExit as left:
                return int(left.code or 0)
            return 0 if result is None else int(result)
    return _unknown(words, table)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    table = registry(PYPROJECT)
    if argv and not argv[0].startswith("-"):
        for k in range(len(argv), 0, -1):
            word = "-".join(argv[:k])
            if word in table:
                result = load(table[word].target)(argv[k:])
                return 0 if result is None else int(result)
        return _unknown(argv, table)

    parser = _parser(table)
    known, rest = parser.parse_known_args(argv)
    if known.all and not known.list:
        parser.error("--all requires --list")
    if known.list:
        say(listing(table, internal=known.all))
        return 0
    daemon_main = load("poolhouse.cli.daemon:main")
    return load("poolhouse.fleet.launch:main")(rest, daemon_main=daemon_main)
