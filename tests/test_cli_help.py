"""Every ``poolhouse`` command answers ``--help``, and the docs name only flags that exist.

Nothing else in the suite catches a flag that does not exist. Three silent edits in one
afternoon left ``--sample`` and ``--short`` documented but never added to the bench, so
argparse matched ``--short`` to ``--shortlist`` by prefix and refused it with a message about
the wrong flag. A ``--help`` that is asserted is the cheapest guard there is: each command
here is imported and its ``main`` called, so a fresh checkout tests itself without being
installed. No model is served, and nothing under ``~/.poolhouse`` is touched.
"""

from __future__ import annotations

import argparse
import importlib
import re
import shlex
import tomllib
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
DOCS = REPO / "docs"


def _scripts() -> dict[str, str]:
    """``command -> module:attr`` for every entry point ``pyproject.toml`` installs."""
    table = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    return dict(table)


SCRIPTS = _scripts()
"""What is installed, read from the same table ``pip`` reads."""


def _main_of(command: str):
    module, _, attr = SCRIPTS[command].partition(":")
    return getattr(importlib.import_module(module), attr or "main")


class _Captured(Exception):
    """Raised inside ``main`` to hand its parser out before it parses anything."""

    def __init__(self, parser: argparse.ArgumentParser, arguments: list[str]) -> None:
        self.parser = parser
        self.arguments = arguments


def parser_of(command: str) -> argparse.ArgumentParser:
    """The parser a command builds, taken out of ``main`` at the moment it would parse.

    None of the entry points exposes its parser, and none should have to: the object is
    caught on its way into ``parse_known_args`` (which ``parse_args`` also goes through)
    and handed back before any argument is read.
    """
    return _capture_parser(command, []).parser


def _capture_parser(command: str, arguments: list[str]) -> _Captured:
    def grab(self: argparse.ArgumentParser, args=None, *_: object, **__: object) -> None:
        raise _Captured(self, list(arguments if args is None else args))

    # A launcher forwards to the machine's selected runtime by exec, which on a machine that
    # has one would replace the test process; reading a parser must never do that.
    with (mock.patch.object(argparse.ArgumentParser, "parse_known_args", grab),
          mock.patch("poolhouse.runtime.forward", return_value=False)):
        try:
            _main_of(command)(arguments)
        except _Captured as caught:
            return caught
    raise AssertionError(f"{command}'s main never parsed its arguments")


def parsers_of(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """Every parser under this one, by the subcommand path that reaches it; '' is the top."""
    found = {"": parser}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                for path, deeper in parsers_of(sub).items():
                    found[f"{name} {path}".strip()] = deeper
    return found


def flags_of(parser: argparse.ArgumentParser, *paths: str) -> set[str]:
    """The ``--flags`` accepted at the named subcommand paths, or everywhere under ``parser``.

    A subcommand also accepts the top parser's own options before it, so '' is always
    included when a path is asked for.
    """
    everything = parsers_of(parser)
    chosen = everything.values() if not paths else [everything[p] for p in {"", *paths}]
    return {s for one in chosen for s in one._option_string_actions if s.startswith("--")}


PARSERS = {command: parser_of(command) for command in SCRIPTS}
SUBCOMMANDS = [(command, path) for command, parser in PARSERS.items()
               for path in parsers_of(parser) if path]


@pytest.fixture(autouse=True)
def bench_at_home(tmp_path, monkeypatch):
    """The bench takes a lock under its home before measuring; keep that out of ~/.poolhouse."""
    monkeypatch.setenv("POOLHOUSE_BENCH_HOME", str(tmp_path / "bench"))


@pytest.mark.parametrize("command", sorted(SCRIPTS), ids=sorted(SCRIPTS))
def test_every_command_answers_help(command, capsys):
    with pytest.raises(SystemExit) as left:
        _main_of(command)(["--help"])
    assert left.value.code == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("command", sorted(c for c in SCRIPTS if c.startswith("poolhouse-")))
def test_the_umbrella_hands_back_the_same_help(command, capsys):
    """``poolhouse chat --help`` is ``poolhouse-chat --help``; ``poolhouse train run`` joins the words."""
    words = command[len("poolhouse-"):].split("-")
    with pytest.raises(SystemExit) as left:
        _main_of(command)(["--help"])
    assert left.value.code == 0
    direct = capsys.readouterr().out
    with pytest.raises(SystemExit) as left:
        _main_of("poolhouse")([*words, "--help"])
    assert left.value.code == 0
    assert capsys.readouterr().out == direct


@pytest.mark.parametrize(("command", "path"), SUBCOMMANDS,
                         ids=[f"{c} {p}" for c, p in SUBCOMMANDS])
def test_every_subcommand_answers_help(command, path, capsys):
    with pytest.raises(SystemExit) as left:
        _main_of(command)([*path.split(), "--help"])
    assert left.value.code == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("command", [c for c, p in PARSERS.items() if len(parsers_of(p)) > 1])
def test_the_top_level_help_names_every_subcommand(command, capsys):
    """A subcommand the usage line does not show is one nobody finds.

    A hand-written ``metavar="{a,b,c}"`` on the subparsers is a list that goes stale the
    day a fourth is added -- ``drafts`` and ``memory`` were both missing from their
    command's ``--help`` for exactly that reason.
    """
    with pytest.raises(SystemExit):
        _main_of(command)(["--help"])
    usage = capsys.readouterr().out.split("\n\n", 1)[0]
    everything = parsers_of(PARSERS[command])
    for path in everything:
        if not path:
            continue
        # A nested subcommand ('members list') is named on its parent's usage line
        # ('members --help'); a first-level one is named on the command's own.
        parent, _, name = path.rpartition(" ")
        shown = everything[parent].format_usage() if parent else usage
        assert re.search(rf"\b{re.escape(name)}\b", shown), \
            f"the usage line of {command} {parent} --help does not name its subcommand {name!r}".replace("  ", " ")


# -- the docs ------------------------------------------------------------------------

FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
CODE_SPAN = re.compile(r"`([^`]*)`")


def _pages() -> list[Path]:
    """The README, and every page under ``docs/`` it links to."""
    text = README.read_text()
    linked = [DOCS / name for name in re.findall(r"\]\(docs/([^)]+\.md)\)", text)]
    return [README, *[page for page in linked if page.is_file()]]


def _command_lines(text: str) -> list[tuple[int, str]]:
    """Every line of a fenced block that starts an ``poolhouse`` command, continuations joined."""
    found: list[tuple[int, str]] = []
    fenced = False
    pending: tuple[int, str] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            continue
        if pending:
            start, sofar = pending
            line, pending = f"{sofar} {line.strip()}", None
            number = start
        if line.rstrip().endswith("\\"):
            pending = (number, line.rstrip()[:-1])
            continue
        if line.strip().startswith("poolhouse"):
            found.append((number, line.strip()))
    return found


def _documented_line(line: str):
    words = shlex.split(line, comments=True, posix=True)
    command, rest = words[0], words[1:]
    if command not in SCRIPTS:
        return None
    captured = _capture_parser(command, rest)
    paths = parsers_of(captured.parser)
    prefixes = [' '.join(captured.arguments[:length]) for length in range(1, len(captured.arguments) + 1)]
    subs = tuple([path for path in prefixes if path in paths][-1:])
    flags = {word.strip('[]') for word in rest if FLAG.fullmatch(word.strip('[]'))}
    return command, subs, flags, captured.parser


def _documented() -> list[tuple[str, str, tuple[str, ...], set[str], argparse.ArgumentParser]]:
    """``(where, command, subcommand paths, flags, parser)`` for every documented flag.

    Two places: a line of a fenced code block, which names its subcommand and so is held to
    that subcommand's parser; and a table row opening with a command, held to the whole
    command. Prose is not scanned: "Serving a model" rightly names llama-server's own flags,
    the ones ``ServerSpec`` emits, and no parser here is meant to accept those.
    """
    found = []
    for page in _pages():
        where = page.relative_to(REPO)
        text = page.read_text()

        for number, line in _command_lines(text):
            documented = _documented_line(line)
            if documented is None:
                continue
            command, subs, flags, parser = documented
            if flags:
                found.append((f"{where}:{number}", command, subs, flags, parser))

        for number, line in enumerate(text.splitlines(), start=1):
            if not line.startswith("| `poolhouse"):
                continue
            command = line.split("`", 2)[1].split()[0]
            if command not in SCRIPTS:
                continue
            flags = {f for span in CODE_SPAN.findall(line) for f in FLAG.findall(span)}
            if flags:
                found.append((f"{where}:{number}", command, (), flags, PARSERS[command]))
    return found


DOCUMENTED = _documented()


def test_the_docs_were_read():
    """A regex that matches nothing would make the test below pass by saying nothing."""
    pages = {page.name for page in _pages()}
    assert {"install.md", "commands.md", "serving.md", "bench.md"} <= pages
    assert any(cmd == "poolhouse-serve" and subs == ("up",) for _, cmd, subs, _, _ in DOCUMENTED)
    assert any(cmd == "poolhouse-bench" and "--rates" in flags for _, cmd, _, flags, _ in DOCUMENTED)


@pytest.mark.parametrize(("where", "command", "subs", "flags", "parser"), DOCUMENTED,
                         ids=[f"{w} {c}" for w, c, _, _, _ in DOCUMENTED])
def test_every_flag_the_docs_name_is_one_the_command_accepts(where, command, subs, flags, parser):
    accepted = flags_of(parser, *subs)
    missing = flags - accepted
    assert not missing, (f"{where} documents {sorted(missing)} for {command} "
                         f"{' '.join(subs)}, which accepts none of them")


def test_documented_runtime_ensure_uses_real_delegated_nested_parser():
    command, subs, flags, parser = _documented_line(
        'poolhouse runtime ensure [--allow-unmerged --review-branch BRANCH]')
    assert command == 'poolhouse' and subs == ('ensure',)
    assert flags == {'--allow-unmerged', '--review-branch'}
    assert flags <= flags_of(parser, *subs)
    assert parser.parse_args(['ensure', '--review-branch', 'feat-x']).review_branch == 'feat-x'


def test_documented_runtime_ensure_refuses_unknown_bracketed_flag():
    command, subs, flags, parser = _documented_line('poolhouse runtime ensure [--unknown-runtime-option]')
    with pytest.raises(AssertionError, match='unknown-runtime-option'):
        test_every_flag_the_docs_name_is_one_the_command_accepts('fixture', command, subs, flags, parser)
    with pytest.raises(SystemExit) as left:
        parser.parse_args(['ensure', '--unknown-runtime-option'])
    assert left.value.code == 2


def test_documented_runtime_status_does_not_accept_ensure_options():
    command, subs, flags, parser = _documented_line('poolhouse runtime status --review-branch BRANCH')
    with pytest.raises(AssertionError, match='review-branch'):
        test_every_flag_the_docs_name_is_one_the_command_accepts('fixture', command, subs, flags, parser)


# -- the bench, where the last three bugs were -----------------------------------------

def test_every_measuring_subcommand_takes_a_sample_and_no_two_define_it_twice():
    """``drafts`` defines ``--sample`` on its own and the shared block adds it to the rest.

    If the shared block ever included ``drafts`` too, argparse would raise on the duplicate
    while building the parser, and ``parser_of`` would have failed before this test ran;
    what is asserted here is that each still accepts it.
    """
    from poolhouse.bench import MEASURING
    for sub in MEASURING:
        assert "--sample" in flags_of(PARSERS["poolhouse-bench"], sub), sub
    for sub in ("run", "sweep"):
        assert {"--short", "--smoke"} <= flags_of(PARSERS["poolhouse-bench"], sub), sub


def test_the_bench_refuses_an_abbreviated_flag_rather_than_guessing():
    """``--short`` bound to ``--shortlist`` by prefix was the bug; a prefix binds nothing now."""
    for path, parser in parsers_of(PARSERS["poolhouse-bench"]).items():
        assert parser.allow_abbrev is False, f"bench {path or 'top level'} allows abbreviation"
    with pytest.raises(SystemExit) as left:
        PARSERS["poolhouse-bench"].parse_args(["run", "x", "--shortl", "3"])
    assert left.value.code == 2


@pytest.mark.parametrize("sub", ("run", "sweep", "drafts"))
def test_the_help_of_a_measuring_subcommand_names_no_queue(sub, capsys):
    """``--no-queue`` is taken out of argv before the parser sees it, so only the parser
    can tell anyone it exists."""
    with pytest.raises(SystemExit):
        _main_of("poolhouse-bench")([sub, "--help"])
    assert "--no-queue" in capsys.readouterr().out
