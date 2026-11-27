"""Who a shell command runs as, set inside its shell, so that no hook changes a command's text.

The Bash hook records, for each Bash and Monitor call, who issued it. The line `loader` gives, which the session start hook appends to
the file Claude Code runs in every such shell before the command (`CLAUDE_ENV_FILE`), runs this file there with the shell's own text on
standard input: it finds the record of the command that shell is about to run and prints the lines that name its caller. A shell that
cannot be told apart from a subagent's names nobody, never the session that started the subagent.

A record holds a digest of the command, not its text, and names only: the lines a shell runs are made here, each value quoted, so a
record cannot put other code in a shell. It runs in the shell of the command, so it imports the standard library only and is given the
directory of the records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

LEAD_SELECTORS = ("CLAUDE_CODE_SESSION_ID", "POOLHOUSE_SESSION_ID", "POOLHOUSE_SESSION_HARNESS", "CODEX_THREAD_ID")
"""How the harness names the session that started a subagent. The shell of a subagent inherits them; its lines take them out, so a command
that drops its own marker finds no identity to fall back on, and not the lead's."""

KEPT_S = 3600
"""How long a record waits for its shell: a call refused after the hook ran leaves one behind."""


def exports(native: str, name: str = "", board: str = "") -> str:
    """The lines that make a shell the subagent the board knows by ``native``, under ``name`` when the board gave it one."""
    lines = [f"export POOLHOUSE_SUBAGENT={shlex.quote(native)}", "unset POOLHOUSE_WORKSPACE_TOKEN",
             *(f"unset {selector}" for selector in LEAD_SELECTORS)]
    lines.append(f"export POOLHOUSE_WORKSPACE_AGENT={shlex.quote(name)}" if name else "unset POOLHOUSE_WORKSPACE_AGENT")
    if board:
        lines.append(f"export POOLHOUSE_BOARD={shlex.quote(board)}")
    return "; ".join(lines)


NOBODY = exports("unidentified")
"""The lines of a shell whose caller cannot be told: the board has no such subagent, so a workspace command is refused with that reason."""


def folder(root: Path, session: str) -> Path:
    return root / "shell" / hashlib.sha256(session.encode()).hexdigest()[:24]


def installed(root: Path, session: str) -> bool:
    """Whether the shells of ``session`` run the `loader` line, so that a record made for it reaches the command."""
    return bool(session) and (folder(root, session) / "installed").is_file()


def mark_installed(root: Path, session: str) -> None:
    where = folder(root, session)
    where.mkdir(parents=True, exist_ok=True, mode=0o700)
    (where / "installed").touch(mode=0o600)


def loader(root: Path) -> str:
    """The one line the file of a session's shell environment holds. A session that resumed holds it several times, between repeats of its
    own exports, so the first run keeps its answer in a shell variable and each later one applies it again."""
    program = shlex.quote(str(Path(__file__).resolve()))
    asked = f"{shlex.quote(sys.executable)} -I {program} {shlex.quote(str(root.resolve()))}"
    return (f'if [ -f {program} ]; then _poolhouse_caller="${{_poolhouse_caller-$(printf %s "${{ZSH_EXECUTION_STRING-'
            f'${{BASH_EXECUTION_STRING-}}}}" | {asked} || printf %s {shlex.quote(NOBODY)})}}"; eval "$_poolhouse_caller"; fi')


def digest(command: str) -> str:
    """A digest of a command with what a shell's quoting and the harness's wrapping add taken out: quotes, backslashes, blanks and the
    redirect from the empty input."""
    plain = re.sub(r"[\s'\"\\]", "", command).replace("</dev/null", "")
    return hashlib.sha256(plain.encode("utf-8", "surrogatepass")).hexdigest()


def asked_for(shell_text: str) -> set[str]:
    """The digest of each command the text of a harness shell may be about to run: what follows `&& eval`, without the line that records
    the working directory afterwards."""
    plain = (re.sub(r"[\s'\"\\]", "", shell_text[found.end():]).replace("</dev/null", "")
             for found in re.finditer(r"&&\s*eval\s", shell_text))
    return {hashlib.sha256(re.sub(r"&&pwd-P>\|[^&;|]*-cwd$", "", one).encode("utf-8", "surrogatepass")).hexdigest() for one in plain}


def keep(path: Path, held: dict) -> None:
    """Write one record whole and private."""
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(held))
    temporary.chmod(0o600)
    temporary.replace(path)


def record(root: Path, session: str, command: str, cwd: str, caller: tuple[str, str, str]) -> None:
    """Keep who issued ``command``: ``caller`` is the harness id, board name and board of a subagent, all empty for the session itself.
    A part that is not text raises, since no shell could be named by it."""
    if not all(isinstance(part, str) for part in caller):
        raise TypeError("a caller is named by text")
    agent, name, board = caller
    mark_installed(root, session)
    keep(folder(root, session) / f"call-{time.time_ns()}-{os.getpid()}.json",
         {"agent": agent, "name": name, "board": board, "command": digest(command), "cwd": cwd, "at": time.time()})


def waiting(where: Path) -> list[tuple[Path, dict]]:
    """The records no shell has taken yet, oldest first; one older than `KEPT_S` is dropped."""
    out = []
    for path in sorted(where.glob("call-*.json")):
        try:
            held = json.loads(path.read_text())
            old = time.time() - float(held["at"]) > KEPT_S
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if old:
            drop(path)
        else:
            out.append((path, held))
    return out


def drop(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        print(f"poolhouse: the record of a command stays ({type(error).__name__})", file=sys.stderr)


def nobody(why: str) -> str:
    print(f"poolhouse: this shell has no workspace identity ({why}), so a workspace command in it is refused.", file=sys.stderr)
    return NOBODY


def resolve(root: Path, session: str, shell_text: str, cwd: str) -> str:
    """The lines that name the caller of the command in ``shell_text``: empty for the session itself, `NOBODY` when records of two callers
    match it, or none does while a subagent's command is still waiting for its shell.

    Each shell takes one record. Where two callers match, the newest is taken and the others are marked, so every shell of that overlap
    names nobody and none of them is given what is left of another's."""
    records = waiting(folder(root, session))
    wanted = asked_for(shell_text)
    found = [(path, held) for path, held in records if held.get("command") in wanted]
    if not found:
        if any(held.get("agent") for _, held in records):
            return nobody("no record of its caller matches this command while a helper's command is waiting")
        return ""
    if len({held.get("agent") for _, held in found}) > 1:
        here = [(path, held) for path, held in found if os.path.realpath(str(held.get("cwd"))) == os.path.realpath(cwd)]
        found = here if len({held.get("agent") for _, held in here}) == 1 else found
    if len({held.get("agent") for _, held in found}) > 1 or any(held.get("mixed") for _, held in found):
        for path, held in found[:-1]:
            keep(path, {**held, "mixed": True})
        drop(found[-1][0])
        return nobody("two callers issued this same command in this folder at once")
    path, held = found[0]
    drop(path)
    agent = str(held.get("agent") or "")
    return exports(agent, str(held.get("name") or ""), str(held.get("board") or "")) if agent else ""


def main(argv: list[str]) -> int:
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if session:
        print(resolve(Path(argv[0]), session, sys.stdin.read(), str(Path.cwd())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
