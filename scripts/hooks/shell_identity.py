"""Who a shell command runs as, set inside its shell, so that no hook changes a command's text.

The Bash hook records, for each Bash and Monitor call, who issued it. The line `loader` gives, which the session start hook appends to
the file Claude Code runs in every such shell before the command (`CLAUDE_ENV_FILE`), runs this file there with the shell's own text on
standard input: it finds the record of the command that shell is about to run and prints the lines that name its caller. A shell that
cannot be told apart from a subagent's names nobody, never the session that started the subagent.

A record holds a digest of the command, not its text, and names only: the lines a shell runs are made here, each value quoted, so a
record cannot put other code in a shell. A call refused after the hook ran leaves a record no shell takes; the transcript Claude Code
writes shows that its call is over. It runs in the shell of the command, so it imports the standard library only and is given the
directory of the records.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

LEAD_SELECTORS = ("CLAUDE_CODE_SESSION_ID", "POOLHOUSE_SESSION_ID", "POOLHOUSE_SESSION_HARNESS", "CODEX_THREAD_ID")
"""How the harness names the session that started a subagent. The shell of a subagent inherits them; its lines take them out, so a command
that drops its own marker finds no identity to fall back on, and not the lead's."""

KEPT_S = 3600
"""How long a record names its caller. A shell that starts later names nobody."""

FORGOTTEN_S = 7 * 24 * 3600
"""How long a record no shell took stays at all."""

SETTLED_S = 60
"""How long after a call's result its shell has surely started, a background one too."""

READ_ANEW = ("startup", "resume", "clear", "fork")
"""The sources of a session start after which Claude Code reads the shell environment file anew. A compacted session keeps what it read
before (seen with 2.1.332), so a line added then counts only once a shell has run it."""

TRANSCRIPT_TAIL = 8 * 1024 * 1024
"""How much of the end of a transcript is searched for the result of a call."""


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
    """Whether the shells of ``session`` run the `loader` line, so that a record reaches its command: a shell has run it, or the
    session read it anew (`READ_ANEW`)."""
    return bool(session) and (folder(root, session) / "installed").is_file()


def mark_installed(root: Path, session: str) -> None:
    where = folder(root, session)
    where.mkdir(parents=True, exist_ok=True, mode=0o700)
    (where / "installed").touch(mode=0o600)


def loader(root: Path) -> str:
    """The one line the file of a session's shell environment holds. A session that resumed holds it several times, between repeats of its
    own exports, so the first run keeps its answer in a shell variable and each later one applies it again. The variable is the line's
    own: a line left by another checkout, interpreter or state folder keeps its answer to itself."""
    program = str(Path(__file__).resolve())
    asked = f"{shlex.quote(sys.executable)} -I {shlex.quote(program)} {shlex.quote(str(root.resolve()))}"
    kept = f"_h{hashlib.sha256(asked.encode()).hexdigest()[:8]}_poolhouse_caller"
    gone = shlex.quote(f"poolhouse: {program} is missing, so a helper's command in this shell runs as the session that started it; "
                       "restart or clear the session.")
    return (f'if [ -f {shlex.quote(program)} ]; then {kept}="${{{kept}-$(printf %s "${{ZSH_EXECUTION_STRING-'
            f'${{BASH_EXECUTION_STRING-}}}}" | {asked} || printf %s {shlex.quote(NOBODY)})}}"; eval "${kept}"; '
            f'elif [ -z "${{POOLHOUSE_SUBAGENT-}}" ]; then printf \'%s\\n\' {gone} >&2; fi')


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


def transcript_of(event: dict) -> str:
    """The file in which Claude Code writes the result of the call of the hook event ``event``: the session's transcript, or for a
    subagent the one it keeps beside it; an empty string when the event names none."""
    main, agent = str(event.get("transcript_path") or ""), str(event.get("agent_id") or "")
    if not main or not agent:
        return main
    return str(event.get("agent_transcript_path") or Path(main).with_suffix("") / "subagents" / f"agent-{agent}.jsonl")


def record(root: Path, session: str, event: dict, caller: tuple[str, str, str]) -> None:
    """Keep who issued the shell command of the hook event ``event``: ``caller`` is the harness id, board name and board of a subagent,
    all empty for the session itself. A part that is not text raises, since no shell could be named by it."""
    if not all(isinstance(part, str) for part in caller):
        raise TypeError("a caller is named by text")
    agent, name, board = caller
    where = folder(root, session)
    where.mkdir(parents=True, exist_ok=True, mode=0o700)
    for old in where.glob("call-*.json"):
        with contextlib.suppress(FileNotFoundError):   # a shell took it just now
            if time.time() - old.stat().st_mtime > FORGOTTEN_S:
                drop(old)
    keep(where / f"call-{time.time_ns()}-{os.getpid()}.json",
         {"agent": agent, "name": name, "board": board, "command": digest(str(event["tool_input"]["command"])),
          "cwd": str(event.get("cwd") or ""), "at": time.time(), "call": str(event.get("tool_use_id") or ""),
          "transcript": transcript_of(event)})


def waiting(where: Path) -> list[tuple[Path, dict]]:
    """The records no shell has taken yet, oldest first."""
    out = []
    for path in sorted(where.glob("call-*.json")):
        try:
            held = json.loads(path.read_text())
            held["at"] = float(held["at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out.append((path, held))
    return out


def drop(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        print(f"poolhouse: the record of a command stays ({type(error).__name__})", file=sys.stderr)


def results(transcript: str) -> list[bytes]:
    """The lines near the end of ``transcript`` that hold the result of a tool call; none when it cannot be read."""
    try:
        with Path(transcript).open("rb") as held:
            held.seek(max(0, os.fstat(held.fileno()).st_size - TRANSCRIPT_TAIL))
            return [line for line in held.read().split(b"\n") if b'"tool_result"' in line]
    except (OSError, ValueError):
        return []


def over(call: str, lines: list[bytes]) -> bool:
    """Whether ``lines`` show that the tool call ``call`` is over, so that no shell of it is still to start: its result is an error
    (refused or denied, it never ran), or it was answered longer ago than a shell takes to start."""
    for line in (line for line in reversed(lines) if call.encode() in line):
        try:
            entry = json.loads(line)
            answered = [part for part in entry["message"]["content"]
                        if part.get("type") == "tool_result" and part.get("tool_use_id") == call]
            if answered:
                return bool(answered[0].get("is_error")) or (
                    time.time() - datetime.fromisoformat(str(entry["timestamp"]).replace("Z", "+00:00")).timestamp() > SETTLED_S)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return False


def still_to_run(records: list[tuple[Path, dict]]) -> list[tuple[Path, dict]]:
    """``records`` without those whose call is over, which are dropped; each transcript is read once."""
    read: dict[str, list[bytes]] = {}
    left = []
    for path, held in records:
        call, transcript = str(held.get("call") or ""), str(held.get("transcript") or "")
        if call and transcript not in read:
            read[transcript] = results(transcript)
        if call and over(call, read[transcript]):
            drop(path)
        else:
            left.append((path, held))
    return left


def nobody(why: str) -> str:
    print(f"poolhouse: this shell has no workspace identity ({why}), so a workspace command in it is refused.", file=sys.stderr)
    return NOBODY


@contextlib.contextmanager
def locked(where: Path) -> Iterator[None]:
    """One shell of a session at a time reads its records and takes one."""
    where.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (where / "lock").open("a+b") as held:
        if os.name == "nt":
            import msvcrt
            held.seek(0)
            msvcrt.locking(held.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                held.seek(0)
                msvcrt.locking(held.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(held, fcntl.LOCK_EX)
            yield


def expired(held: dict) -> bool:
    return time.time() - held["at"] > KEPT_S


def matching(where: Path, wanted: set[str]) -> list[tuple[Path, dict]]:
    """The records that match one of the digests ``wanted``. Where they leave the caller in doubt, those whose call is over are
    dropped first."""
    found = [(path, held) for path, held in waiting(where) if held.get("command") in wanted]
    doubt = (len({held.get("agent") for _, held in found}) > 1
             or any(held.get("mixed") or expired(held) for _, held in found))
    return still_to_run(found) if doubt else found


def helper_waits(where: Path) -> bool:
    """Whether a subagent's command is still waiting for its shell."""
    return bool(still_to_run([(path, held) for path, held in waiting(where) if held.get("agent") and not expired(held)]))


def taken(found: list[tuple[Path, dict]], cwd: str) -> str:
    """The lines for the shell that takes one of the records ``found``. Where two callers match, the newest is taken and the others
    are marked, so every shell of that overlap names nobody and none of them is given what is left of another's."""
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


def resolve(root: Path, session: str, shell_text: str, cwd: str) -> str:
    """The lines that name the caller of the command in ``shell_text``: empty for the session itself, `NOBODY` when records of two callers
    match it, when a subagent's matching record is older than `KEPT_S`, or when none matches while a subagent's command is still
    waiting for its shell. Each shell takes one record."""
    where = folder(root, session)
    with locked(where):
        found = matching(where, asked_for(shell_text))
        late = [(path, held) for path, held in found if expired(held)]
        for path, _ in late:
            drop(path)
        if any(held.get("agent") for _, held in late):
            return nobody("the record of its caller is older than an hour; run the command again")
        if found := [one for one in found if one not in late]:
            return taken(found, cwd)
        if helper_waits(where):
            return nobody("no record of its caller matches this command while a helper's command is waiting")
        return ""


def ran_here(root: Path, session: str) -> None:
    """Note that a shell of ``session`` runs the `loader` line. A shell that cannot write the note still gets its caller."""
    try:
        if not installed(root, session):
            mark_installed(root, session)
    except OSError as error:
        print(f"poolhouse: this shell could not note that it names its caller ({type(error).__name__})", file=sys.stderr)


def main(argv: list[str]) -> int:
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if session:
        print(resolve(Path(argv[0]), session, sys.stdin.read(), str(Path.cwd())))
        ran_here(Path(argv[0]), session)
    elif not os.environ.get("POOLHOUSE_SUBAGENT"):
        print("poolhouse: this shell names no Claude Code session, so a helper's command in it runs as the session that started it.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
