"""``poolhouse credentials``: set, list, unset and locate the credentials file."""

from __future__ import annotations

import argparse
import json
import sys

from poolhouse import credentials
from poolhouse.command import Group, flag, option
from poolhouse.log import say, warn

COMMANDS = Group(
    "poolhouse credentials",
    "Tokens and keys poolhouse uses. `set` reads the value from stdin only, "
    "never from an argument or a prompt, and nothing prints a value back. The file is "
    "~/.poolhouse/credentials.toml, mode 0600; POOLHOUSE_CREDENTIALS_FILE moves it.")

KEYCHAIN = flag("--keychain", action="store_true",
                help="the OS keychain, through the `keyring` package, instead of the file")


def _guarded(run):
    def inside(args: argparse.Namespace) -> int:
        try:
            return run(args)
        except credentials.CredentialError as exc:
            warn(f"poolhouse credentials: {exc}")
            return 1
    return inside


@COMMANDS.command("set", help="store NAME, reading the value from stdin",
                  options=(flag("name"), KEYCHAIN))
@_guarded
def put(args: argparse.Namespace) -> int:
    """Store the value read from stdin; a terminal is refused at once, nothing is asked."""
    if sys.stdin.isatty():
        raise credentials.CredentialError(
            f"the value is read from stdin only, never asked for: echo \"$VALUE\" | poolhouse credentials set {args.name}")
    value = sys.stdin.readline()
    say(f"{args.name} stored in {credentials.set(args.name, value, keychain=args.keychain)}")
    return 0


@COMMANDS.command("unset", help="remove NAME", options=(flag("name"), KEYCHAIN))
@_guarded
def drop(args: argparse.Namespace) -> int:
    """Remove a stored credential."""
    gone = credentials.unset(args.name, keychain=args.keychain)
    say(f"{args.name} removed" if gone else f"{args.name} was not stored")
    return 0


@COMMANDS.command("list", help="each name with where it comes from, never a value",
                  options=(option("json"),))
@_guarded
def listing(args: argparse.Namespace) -> int:
    """Print each credential's name and source."""
    rows = credentials.describe()
    if args.json:
        say(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        state = row["source"] if row["present"] else row.get("error", "not set")
        say(f"{row['name']:<24} {'set' if row['present'] else 'unset':<6} {state}")
    return 0


@COMMANDS.command("path", help="where the credentials file is")
def where(_args: argparse.Namespace) -> int:
    """Print the credentials file's path."""
    say(credentials.file_path())
    return 0


command = COMMANDS.run
