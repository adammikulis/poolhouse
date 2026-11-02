"""run-script: the owner's one-time approval of one agent run of a release script, bound to the exact command, repository and tree."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shlex
import shutil
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home, person, protectedwrites
from poolhouse.sentinel.events import EventLog
from poolhouse.workspace import person_auth, person_store, person_targets

__all__ = ["KIND", "RUNS", "TICKET", "TICKET_DIR", "TICKET_ENV", "TICKET_MINUTES", "Approval", "Facts", "NotARun", "admit",
           "approved", "asked_exactly", "child_environment", "command", "descriptions", "echo_for", "facts", "launcher",
           "options", "parse", "propose", "question", "redeem", "session_project", "target"]

KIND = "run-script"
TICKET = "run-ticket"
TICKET_ENV = "POOLHOUSE_RUN_TICKET_FILE"
TICKET_DIR = "tickets"
TICKET_MINUTES = 1
"""The store counts whole minutes, and the Bash tool starts the script seconds after the guard hands the ticket over."""
CHECKED = ("scripts", "src")
"""The folders whose files are the code the approved script runs; any difference from the commit in them voids an approval."""
TRUSTED_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/homebrew/bin", "/usr/local/bin")
"""Where the system and Homebrew keep `gh` and `ssh-keygen`; an approved run finds its tools nowhere else."""
KEPT = ("USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM")
"""The variables an approved run passes on besides the agent markers, POOLHOUSE_ settings and the account's home; none redirects `gh`."""
LOADER_PREFIXES = ("DYLD_", "LD_", "PYTHON")
"""Variables that change what the interpreter or the loader runs; an approved run starts with none of them set."""
HEADER = "Run script"
"""The header of the approval question, fixed so the agent writes none of what the owner reads."""
ECHO = re.compile(r"authorize run-script (?P<target>\S+) (?P<minutes>\d+)m x(?P<uses>\d+) #(?P<tag>[0-9a-f]{12})")
TARGET = re.compile(r"(?P<top>[^|\s#]+)\|(?P<name>[a-z-]+)\|(?P<args>[^|\s#]+)\|(?P<slug>[\w.-]+/[\w.-]+)\|(?P<tree>[0-9a-f]{40})")
FULL_SHA = re.compile(r"[0-9a-f]{40}")
SECRET = re.compile(r"[0-9a-f]{32}")
SIMPLE = re.compile(r"[A-Za-z0-9_./ -]+")
GITHUB = re.compile(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?/?")
OPTIONS = ("--owner-review", "--strict-tags")
GUARDS_SHOWN = 8


class NotARun(ValueError):
    """A command that is not one of the runs an approval can cover."""


def _create_write(args: list[str], _slug: str) -> tuple[str, ...] | None:
    return ("create", "--write") if args == ["create", "--write"] else None


def _apply_here(args: list[str], slug: str) -> tuple[str, ...] | None:
    """The arguments of `github-protection-apply` that name the repository and apply, in one fixed order."""
    if args.count("--apply") != 1 or args.count("--repo") != 1 or args[args.index("--repo") + 1:][:1] != [slug]:
        return None
    rest = [a for a in args if a not in ("--apply", "--repo", slug)]
    if len(args) != 3 + len(rest) or any(a not in OPTIONS or rest.count(a) > 1 for a in rest):
        return None
    return ("--apply", "--repo", slug, *(o for o in OPTIONS if o in rest))


@dataclass(frozen=True, slots=True)
class Script:
    """A script an agent may run on the owner's approval: which arguments an approval covers and what the run does."""

    form: Callable[[list[str], str], tuple[str, ...] | None]
    does: str


RUNS = {
    "release-key": Script(_create_write,
                          "makes the release signing key, keeps it in this device's keystore, stores it as the release "
                          "environment's RELEASE_SIGNING_KEY secret of {slug} and sets RELEASE_KEY in "
                          "src/poolhouse/fleet/signing.py. The key is never printed."),
    "github-protection-apply": Script(_apply_here,
                                      "applies the GitHub protection target of docs/github-protection.md to {slug}: the "
                                      "rulesets, the release environment and the Actions settings. It saves the current "
                                      "settings first and prints how to restore them."),
}


@dataclass(frozen=True, slots=True)
class Facts:
    """What git and the checkout say about one run: where, which script and arguments, the repository, the commit, its tree and how the code differs from the remote's main."""

    top: str
    name: str
    args: tuple[str, ...]
    slug: str
    remote: str
    sha: str
    tree: str
    changed: str = "none"
    script_changed: str = "unchanged"
    guards: str = "none"


@dataclass(frozen=True, slots=True)
class Approval:
    """An agent's run the owner approved: the repository it is pinned to, the environment its children get and their empty `gh` configuration."""

    slug: str
    environment: dict[str, str]
    config: Path

    def close(self) -> None:
        """Remove the empty configuration folder."""
        shutil.rmtree(self.config, ignore_errors=True)


def parse(text: str) -> tuple[str, list[str]] | None:
    """The program and arguments of ``text`` when it is one plain command of words, paths and flags separated by spaces that names a covered script, else None."""
    plain = text.strip(" \t")
    if not SIMPLE.fullmatch(plain):
        return None
    words = [word for word in plain.split(" ") if word]
    return (words[0], words[1:]) if words and Path(words[0]).name in RUNS else None


def session_project(environ: Mapping[str, str] | None = None) -> str:
    """The primary checkout of the project this harness session was started in (``CLAUDE_PROJECT_DIR``), an empty string when it names none."""
    named = (os.environ if environ is None else environ).get("CLAUDE_PROJECT_DIR", "")
    return person_targets.inspect(named).project if named else ""


def _slug(url: str) -> str:
    found = GITHUB.fullmatch(url.strip())
    return found["slug"] if found else ""


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()  # git's object name; not a defence


def _committed(top: str) -> dict[str, tuple[str, str]]:
    """``{path: (mode, blob)}`` of the files of the checked folders in HEAD's tree, empty when git cannot list them."""
    listing = person_targets.run_git(top, "ls-tree", "-r", "-z", "HEAD", "--", *CHECKED)
    entries = {}
    for item in listing.split("\0"):
        meta, _, path = item.partition("\t")
        if path and len(meta.split()) == 3:
            mode, _kind, sha = meta.split()
            entries[path] = (mode, sha)
    return entries


def _file_problem(full: Path, rel: str, root: Path, entries: dict[str, tuple[str, str]]) -> str:
    """Why the file or link at ``full`` is not the committed ``rel``, an empty string when it is."""
    if rel not in entries:
        return f"{rel} is not a committed file"
    mode, sha = entries[rel]
    info = full.lstat()
    if stat.S_ISLNK(info.st_mode):
        inside = str(Path(os.path.realpath(full))).startswith(str(root) + os.sep)
        return "" if mode == "120000" and inside and _blob(os.fsencode(full.readlink())) == sha else f"{rel} is a link that is not the committed one"
    if not stat.S_ISREG(info.st_mode) or mode not in ("100644", "100755"):
        return f"{rel} is not the kind of file that was committed"
    if (mode == "100755") != bool(info.st_mode & 0o100):
        return f"{rel} has another executable bit than the commit"
    return "" if _blob(full.read_bytes()) == sha else f"{rel} differs from the commit"


def _problem(top: str) -> str:
    """The first reason the files of the checked folders are not exactly the commit's (hashed, not trusted from git's index), an empty string when they are."""
    entries = _committed(top)
    if not entries:
        return "git cannot list the committed files of scripts and src"
    root, seen = Path(top), set()
    for folder in CHECKED:
        if (root / folder).is_symlink() or not (root / folder).is_dir():
            return f"{folder} is not a plain folder"
        for here, dirs, files in os.walk(root / folder):
            links = [name for name in dirs if Path(here, name).is_symlink()]
            dirs[:] = [name for name in dirs if name not in links and name != "__pycache__"]
            for name in sorted([*files, *links]):
                rel = Path(here, name).relative_to(root).as_posix()
                why = _file_problem(Path(here, name), rel, root, entries)
                if why:
                    return why
                seen.add(rel)
    gone = sorted(set(entries) - seen)
    return f"{gone[0]} is missing" if gone else ""


def _guards(names: list[str]) -> str:
    held = protectedwrites.touches_guards(names)
    shown = ", ".join(held[:GUARDS_SHOWN]) + (f" and {len(held) - GUARDS_SHOWN} more" if len(held) > GUARDS_SHOWN else "")
    return shown if held else "none"


def facts(where: str | Path, script: str | Path, args: list[str] | tuple[str, ...], project: str = "") -> Facts | None:
    """The `Facts` of running ``script`` with ``args`` from the checkout holding ``where`` (a worktree of ``project`` when it is named); None when it is no covered run or git cannot vouch for it."""
    name = Path(script).name
    entry = RUNS.get(name)
    top = person_targets.run_git(where, "rev-parse", "--show-toplevel")
    if entry is None or not top:
        return None
    top = os.path.realpath(top)
    if os.path.realpath(Path(where) / script) != os.path.realpath(Path(top, "scripts", name)):
        return None
    if project and person_targets.inspect(top).project != project:
        return None
    remote = person_targets.inspect(top).remote
    slug = _slug(person_targets.run_git(top, "config", "--get", f"remote.{remote}.url"))
    sha = person_targets.run_git(top, "rev-parse", "--verify", "HEAD^{commit}")
    tree = person_targets.run_git(top, "rev-parse", "HEAD^{tree}")
    base = person_targets.run_git(top, "rev-parse", "--verify", f"refs/remotes/{remote}/main^{{commit}}")
    covered = entry.form(list(args), slug) if slug else None
    if covered is None or any(c.isspace() or c in "|#" for c in top):
        return None
    if not (FULL_SHA.fullmatch(sha) and FULL_SHA.fullmatch(tree) and FULL_SHA.fullmatch(base)) or _problem(top):
        return None
    names = person_targets.run_git(top, "diff", "--name-only", "--no-renames", base, sha).splitlines()
    return Facts(top, name, covered, slug, remote, sha, tree,
                 person_targets.run_git(top, "diff", "--shortstat", base, sha, "--", *CHECKED) or "none",
                 person_targets.run_git(top, "diff", "--shortstat", base, sha, "--", f"scripts/{name}") or "unchanged", _guards(names))


def target(found: Facts) -> str:
    """The target an approval is bound to: checkout, script, arguments, repository and tree."""
    return f"{found.top}|{found.name}|{','.join(found.args)}|{found.slug}|{found.tree}"


def command(found: Facts) -> str:
    """The command line the approval covers."""
    return " ".join([f"scripts/{found.name}", *found.args])


def question(found: Facts) -> str:
    """The approval question's text, made only of what git and the registry report."""
    base = f"{found.remote}/main"
    return (f"Run {command(found)}?\nRepository: {found.slug} (remote {found.remote})\nCheckout: {found.top}\n"
            f"Commit: {found.sha}, with every file of scripts and src exactly as committed\n"
            f"Changed since {base} in scripts and src: {found.changed}\n"
            f"This script since {base}: {found.script_changed}\n"
            f"Guards changed since {base}: {found.guards}\n"
            f"What it does: {RUNS[found.name].does.format(slug=found.slug)}\n"
            "The agent runs it once, as typed above; it cannot change the command, the commit or the repository after your answer.")


def options(found: Facts) -> tuple[str, str]:
    """The two answers: the approval is the first."""
    return f"Approve {command(found)}", "Do not run it"


def descriptions(found: Facts) -> tuple[str, str]:
    """What each answer says under its label."""
    return (f"Runs {command(found)} once, as written above, within {person_store.DEFAULT_MINUTES} minutes.",
            "Nothing runs and nothing changes.")


def asked_exactly(tool_input: object, text: str, found: Facts) -> bool:
    """Whether the AskUserQuestion that was asked holds only ``text`` with the header, labels and descriptions of this run, with no other question or option."""
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not (isinstance(questions, list) and len(questions) == 1 and isinstance(questions[0], dict)):
        return False
    only, given = questions[0], questions[0].get("options")
    wanted = list(zip(options(found), descriptions(found), strict=True))
    if not (isinstance(given, list) and all(isinstance(o, dict) and set(o) <= {"label", "description"} for o in given)):
        return False
    return (only.get("question") == text and only.get("header") == HEADER and not only.get("multiSelect")
            and set(only) <= {"question", "header", "options", "multiSelect"}
            and [(o.get("label"), o.get("description")) for o in given] == wanted)


def _tag(log: EventLog, where: str, minutes: int, uses: int) -> str:
    return log.mac(f"{KIND}|{where}|{minutes}|{uses}".encode())[:12]


def echo_for(log: EventLog, found: Facts, minutes: int = person_store.DEFAULT_MINUTES, uses: int = 1) -> str:
    """The question's final line: kind, target, life and uses, with a tag made under the store's key."""
    return f"authorize {KIND} {target(found)} {minutes}m x{uses} #{_tag(log, target(found), minutes, uses)}"


def approved(log: EventLog, text: str, label: str, project: str) -> Facts | None:
    """The `Facts` an answered question approves: a tagged echo made by this store, a checkout that is a worktree of the session's ``project``, the question git and the registry now render, and the approving ``label``."""
    *body, last = text.rstrip().splitlines() or [""]
    echoed = ECHO.fullmatch(last.strip())
    if not project or echoed is None or int(echoed["minutes"]) != person_store.DEFAULT_MINUTES or int(echoed["uses"]) != 1:
        return None
    if not hmac.compare_digest(_tag(log, echoed["target"], person_store.DEFAULT_MINUTES, 1), echoed["tag"]):
        return None
    parts = TARGET.fullmatch(echoed["target"])
    if parts is None:
        return None
    found = facts(parts["top"], Path(parts["top"], "scripts", parts["name"]), parts["args"].split(","), project)
    if found is None or target(found) != echoed["target"] or "\n".join(body).strip() != question(found):
        return None
    return found if label == options(found)[0] else None


def propose(argv: list[str], cwd: str, log: EventLog | None = None) -> tuple[int, str]:
    """The exit code and text of `run-approval propose NAME ARGS...`: the question, its echo line, the header and the two answers with their descriptions."""
    top = person_targets.run_git(cwd, "rev-parse", "--show-toplevel")
    found = facts(cwd, Path(top, "scripts", argv[0]), argv[1:], session_project()) if argv and top else None
    if found is None:
        why = _problem(os.path.realpath(top)) if top else ""
        return 1, ("git cannot show the facts of that run: it must be `release-key create --write` or `github-protection-apply "
                   "--apply --repo OWNER/NAME` (the origin's repository), in a worktree of this session's project with an "
                   "origin/main to compare against and with every file of scripts and src as committed"
                   + (f" ({why})" if why else ""))
    log = log or person_store.open_log()
    (yes, no), (yes_says, no_says) = options(found), descriptions(found)
    return 0, (f"{question(found)}\n{echo_for(log, found)}\n\nheader: {HEADER}\noptions: {yes} | {no}\n"
               f"descriptions: {yes_says} | {no_says}")


def _take_ticket() -> str:
    """The secret in the ticket file that ``TICKET_ENV`` names, read and removed; empty when the file is not one this store made (in its ticket folder, ours alone, plain)."""
    named = os.environ.pop(TICKET_ENV, "")
    path, folder = Path(named), person_store.directory() / TICKET_DIR
    if not named or os.path.realpath(path.parent) != os.path.realpath(folder):
        return ""
    try:
        info = path.lstat()
        mine = stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077 and info.st_size <= 64
        text = path.read_text(encoding="ascii") if mine else ""
        if mine:
            path.unlink()
    except (OSError, UnicodeError):
        return ""
    return text.strip()


def redeem(found: Facts, secret: str, session: str | None = None, log: EventLog | None = None) -> str:
    """Spend the ticket ``secret`` for the run ``found``; raises `person_auth.NotAuthorized` when no live ticket of this session matches."""
    log = log or person_store.open_log()
    if not SECRET.fullmatch(secret):
        raise person_auth.NotAuthorized("no ticket for this run")
    who = person_auth.Identity(person_auth.session_of_ancestry(log) if session is None else session)
    return person_auth.consume(TICKET, f"{target(found)}#{secret}", who, log=log)


def child_environment(config: Path, source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment an approved run gives its children: the account's home, the agent markers, POOLHOUSE_ settings, the system tool folders and an empty `gh` configuration folder."""
    found = os.environ if source is None else source
    kept = {k: v for k, v in found.items() if k in KEPT or k in person.AGENT_MARKERS or k.startswith("POOLHOUSE_")}
    kept.pop(TICKET_ENV, None)
    kept["HOME"] = str(home.account_roots()[0].parent)
    kept["PATH"] = os.pathsep.join(TRUSTED_DIRS)
    kept["GH_CONFIG_DIR"] = str(config)
    return kept


def launcher(found: Facts, typed: list[str], ticket: Path, python: str, source: Mapping[str, str]) -> str:
    """The one command line the main session's approved run starts as: a cleared environment, the interpreter isolated and no bytecode read from the checkout, and the ticket named by file."""
    env = {"HOME": str(home.account_roots()[0].parent), "PATH": os.pathsep.join(TRUSTED_DIRS), "CLAUDECODE": "1", TICKET_ENV: str(ticket)}
    env |= {k: source[k] for k in (*KEPT, "POOLHOUSE_HOME") if k in source}
    words = ["/usr/bin/env", "-i", *(f"{k}={v}" for k, v in env.items()), python, "-I", "-B", "-X", f"pycache_prefix={ticket}.pyc",
             str(Path(found.top, "scripts", found.name)), *typed]
    return " ".join(shlex.quote(word) for word in words)


def _how(found: Facts) -> str:
    return (f"the owner can approve this exact command for an agent: run scripts/hooks/run-approval propose {found.name} "
            f"{' '.join(found.args)}, ask its question with AskUserQuestion word for word, and run the command again after the answer")


def admit(script: str | Path, args: list[str], terminal: tuple[bool, bool] | None = None,
          session: str | None = None) -> Approval | None:
    """None when a person runs ``script``; an `Approval` when the owner's answer covers this agent's run; else raises `person.HumanRequired`."""
    secret = _take_ticket()
    try:
        person.require_person(f"scripts/{Path(script).name}", terminal)
    except person.HumanRequired as refused:
        found = facts(Path(script).parent, script, args)
        if found is None:
            raise
        loaders = sorted(k for k in os.environ if k.startswith(LOADER_PREFIXES))
        if loaders:
            raise person.HumanRequired(f"{refused}; an approved run starts with none of {', '.join(loaders)} set") from None
        try:
            redeem(found, secret, session)
        except (person_auth.NotAuthorized, person_store.Unreadable) as missing:
            raise person.HumanRequired(f"{refused}; {_how(found)}") from missing
        config = Path(tempfile.mkdtemp(prefix="gh-config-"))
        return Approval(found.slug, child_environment(config), config)
    return None
