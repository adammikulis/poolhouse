"""run-script: the owner's one-time approval of one agent run of a release script, bound to the exact command, repository and tree."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home, person, protectedwrites, runtime
from poolhouse.sentinel.events import EventLog
from poolhouse.workspace import person_auth, person_store, person_targets

__all__ = ["KIND", "RUNS", "TICKET", "TICKET_DIR", "TICKET_ENV", "TICKET_MINUTES", "Approval", "Facts", "NotARun", "Refused",
           "admit", "approved", "asked_problem", "child_environment", "command", "descriptions", "echo_for", "facts", "launcher",
           "options", "parse", "propose", "question", "redeem", "session_project", "target", "verify_loaded"]

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
PATHS_SHOWN = 20
"""How many changed files of scripts and src the question names; the count is always shown. A screen holds about this many lines."""
GUARDS_SHOWN = 8
ECHO = re.compile(r"authorize run-script (?P<target>\S+) (?P<minutes>\d+)m x(?P<uses>\d+) #(?P<tag>[0-9a-f]{12})")
TARGET = re.compile(r"(?P<top>[^|\s#]+)\|(?P<name>[a-z-]+)\|(?P<args>[^|\s#]+)\|(?P<slug>[\w.-]+/[\w.-]+)\|(?P<tree>[0-9a-f]{40})"
                    r"\|(?P<runtime>[0-9a-f]{12})")
FULL_SHA = re.compile(r"[0-9a-f]{40}")
SECRET = re.compile(r"[0-9a-f]{32}")
SIMPLE = re.compile(r"[A-Za-z0-9_./ -]+")
PLAIN_NAME = re.compile(r"[A-Za-z0-9_./+@-]+")
GITHUB = re.compile(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)(?P<slug>[\w.-]+/[\w.-]+?)(?:\.git)?/?")
OPTIONS = ("--owner-review", "--strict-tags")


class NotARun(ValueError):
    """A command that is not one of the runs an approval can cover."""


class Refused(ValueError):
    """A covered run that cannot be offered, approved or made, with the reason as one sentence."""


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
    """What git, the checkout and the selected runtime say about one run: where, which script and arguments, the repository, the commit, its tree, the interpreter and how the code differs from the remote's main."""

    top: str
    name: str
    args: tuple[str, ...]
    slug: str
    remote: str
    sha: str
    tree: str
    python: str = ""
    prefix: str = ""
    built: str = ""
    changed: str = "none"
    files: str = "none"
    script_changed: str = "unchanged"
    guards: str = "none"


@dataclass(frozen=True, slots=True)
class Approval:
    """An agent's run the owner approved: its facts, the environment its children get, their empty `gh` configuration and the interpreter flags they start with."""

    found: Facts
    environment: dict[str, str]
    config: Path

    @property
    def slug(self) -> str:
        """The repository the run is pinned to."""
        return self.found.slug

    def python_flags(self) -> list[str]:
        """The flags every Python child of the run starts with: isolated, writing no bytecode and reading none from the checkout."""
        return ["-I", "-B", "-X", f"pycache_prefix={self.config}.pyc"]

    def verify(self) -> None:
        """Raise `Refused` unless every Poolhouse module loaded so far came from the approved code."""
        verify_loaded(self.found)

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


def _file_problem(full: Path, rel: str, entries: dict[str, tuple[str, str]]) -> str:
    """Why the file at ``full`` is not the committed ``rel`` (links are none of the committed kind), an empty string when it is."""
    info = full.lstat()
    if stat.S_ISLNK(info.st_mode):
        return f"{rel} is a symbolic link; scripts and src hold none"
    if rel not in entries:
        return f"{rel} is not a committed file"
    mode, sha = entries[rel]
    if not stat.S_ISREG(info.st_mode):
        return f"{rel} is not the kind of file that was committed"
    if (mode == "100755") != bool(info.st_mode & 0o100):
        return f"{rel} has another executable bit than the commit"
    return "" if _blob(full.read_bytes()) == sha else f"{rel} differs from the commit"


def _raise(error: OSError) -> None:
    raise error


def _first_problem(top: str) -> str:
    entries = _committed(top)
    if not entries:
        return "git cannot list the committed files of scripts and src"
    for path, (mode, _sha) in sorted(entries.items()):
        if mode not in ("100644", "100755"):
            return f"{path} is committed as a {'symbolic link' if mode == '120000' else 'submodule'}; scripts and src hold none"
    root, seen = Path(top), set()
    for folder in CHECKED:
        if (root / folder).is_symlink() or not (root / folder).is_dir():
            return f"{folder} is not a plain folder"
        for here, dirs, files in os.walk(root / folder, onerror=_raise):
            if not os.access(here, os.R_OK | os.X_OK):
                return f"{Path(here).relative_to(root).as_posix()} is a folder that cannot be both listed and entered"
            links = [name for name in dirs if Path(here, name).is_symlink()]
            dirs[:] = [name for name in dirs if name not in links and name != "__pycache__"]
            for name in sorted([*files, *links]):
                rel = Path(here, name).relative_to(root).as_posix()
                if why := _file_problem(Path(here, name), rel, entries):
                    return why
                seen.add(rel)
    gone = sorted(set(entries) - seen)
    return f"{gone[0]} is missing" if gone else ""


def _problem(top: str) -> str:
    """The first reason the files of the checked folders are not exactly the commit's (every file hashed, every folder listed, none trusted from git's index), an empty string when they are; a folder or file that cannot be read is a reason."""
    try:
        return _first_problem(top)
    except OSError as error:
        return f"{error.filename or top} cannot be read ({error.strerror or error.__class__.__name__}), so scripts and src cannot be checked"


def _guards(names: list[str]) -> str:
    held = protectedwrites.touches_guards(names)
    shown = ", ".join(_shown(n) for n in held[:GUARDS_SHOWN]) + (f" and {len(held) - GUARDS_SHOWN} more" if len(held) > GUARDS_SHOWN else "")
    return shown if held else "none"


def _shown(name: str) -> str:
    """``name`` as the owner reads it: as it is when it is plain, else quoted with every odd character escaped."""
    return name if PLAIN_NAME.fullmatch(name) else ascii(name)


def _changes(top: str, base: str, sha: str) -> tuple[list[str], str]:
    """The names changed against ``base`` (all of them, for the guards) and the sentence naming the first changed files of the checked folders, read as git's NUL-separated list."""
    parts = person_targets.run_git(top, "diff", "-z", "--name-status", "--no-renames", base, sha).split("\0")
    changes = [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]
    code = [(status, path) for status, path in changes if path.split("/", 1)[0] in CHECKED]
    named = ", ".join(f"{status} {_shown(path)}" for status, path in code[:PATHS_SHOWN])
    more = f" and {len(code) - PATHS_SHOWN} more" if len(code) > PATHS_SHOWN else ""
    return [path for _status, path in changes], f"{len(code)}: {named}{more}" if code else "none"


def _runtime(top: str) -> runtime.Runtime:
    """The selected runtime when it passes its verification and was built from this checkout's src; else raises `Refused` saying which."""
    chosen = runtime.available()
    if chosen is None:
        raise Refused("the selected runtime does not pass its verification (poolhouse runtime ensure repairs it), and an approved run uses no other interpreter"
                      if runtime.selection_prefix() else
                      "no runtime is selected (poolhouse runtime ensure installs one), and an approved run uses no other interpreter")
    built = person_targets.run_git(top, "rev-parse", f"{chosen.commit}:src")
    if not FULL_SHA.fullmatch(built) or built != person_targets.run_git(top, "rev-parse", "HEAD:src"):
        raise Refused(f"the selected runtime was built from {chosen.commit[:12]}, whose src is not this checkout's src; "
                      "poolhouse runtime ensure builds the runtime of the commit to be run")
    return chosen


def facts(where: str | Path, script: str | Path, args: list[str] | tuple[str, ...], project: str = "") -> Facts | None:
    """The `Facts` of running ``script`` with ``args`` from the checkout holding ``where`` (a worktree of ``project`` when it is named); None when it is no covered run, `Refused` when it is one that cannot be offered."""
    name = Path(script).name
    entry = RUNS.get(name)
    top = person_targets.run_git(where, "rev-parse", "--show-toplevel")
    if entry is None or not top:
        return None
    top = os.path.realpath(top)
    if os.path.realpath(Path(where) / script) != os.path.realpath(Path(top, "scripts", name)):
        return None
    if project and person_targets.inspect(top).project != project:
        raise Refused("this checkout is not a worktree of the project this session was started in")
    remote = person_targets.inspect(top).remote
    slug = _slug(person_targets.run_git(top, "config", "--get", f"remote.{remote}.url"))
    if not slug:
        raise Refused(f"the {remote} remote is not a GitHub repository")
    covered = entry.form(list(args), slug)
    if covered is None:
        return None
    if any(c.isspace() or c in "|#" for c in top):
        raise Refused("the checkout's path holds a space, a '|' or a '#'")
    sha = person_targets.run_git(top, "rev-parse", "--verify", "HEAD^{commit}")
    tree = person_targets.run_git(top, "rev-parse", "HEAD^{tree}")
    base = person_targets.run_git(top, "rev-parse", "--verify", f"refs/remotes/{remote}/main^{{commit}}")
    if not (FULL_SHA.fullmatch(sha) and FULL_SHA.fullmatch(tree)):
        raise Refused("git cannot name this checkout's commit")
    if not FULL_SHA.fullmatch(base):
        raise Refused(f"{remote}/main is not in this checkout, so the code has nothing to be compared with; fetch it")
    if problem := _problem(top):
        raise Refused(problem)
    chosen = _runtime(top)
    names, files = _changes(top, base, sha)
    return Facts(top, name, covered, slug, remote, sha, tree, str(chosen.python), os.path.realpath(chosen.prefix), chosen.commit,
                 person_targets.run_git(top, "diff", "--shortstat", base, sha, "--", *CHECKED) or "none", files,
                 person_targets.run_git(top, "diff", "--shortstat", base, sha, "--", f"scripts/{name}") or "unchanged", _guards(names))


def target(found: Facts) -> str:
    """The target an approval is bound to: checkout, script, arguments, repository, tree and the interpreter that will run."""
    ran = hashlib.sha256(f"{found.python}\0{found.built}".encode()).hexdigest()[:12]
    return f"{found.top}|{found.name}|{','.join(found.args)}|{found.slug}|{found.tree}|{ran}"


def command(found: Facts) -> str:
    """The command line the approval covers."""
    return " ".join([f"scripts/{found.name}", *found.args])


def question(found: Facts) -> str:
    """The approval question's text, made only of what git, the runtime and the registry report."""
    base = f"{found.remote}/main"
    return (f"Run {command(found)}?\nRepository: {found.slug} (remote {found.remote})\nCheckout: {found.top}\n"
            f"Commit: {found.sha}, with every file of scripts and src exactly as committed\n"
            f"Interpreter: {found.python}, isolated (-I), from the runtime built from {found.built[:12]} whose src is this checkout's src\n"
            f"Code: the scripts of this checkout; the Poolhouse of that runtime or of this checkout's src, and nothing else\n"
            f"Changed since {base} (as last fetched) in scripts and src: {found.changed}\n"
            f"Changed files in scripts and src ({found.files})\n"
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


def asked_problem(tool_input: object, text: str, found: Facts) -> str:
    """One sentence on how the AskUserQuestion that was asked differs from ``text`` with the header, labels and descriptions of this run, an empty string when it is exactly that and nothing else."""
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list) or not questions or not all(isinstance(q, dict) for q in questions):
        return "the answer carries no record of what was asked"
    if len(questions) != 1:
        return "more than one question was asked together"
    only, given = questions[0], questions[0].get("options")
    if extra := sorted(map(str, set(only) - {"question", "header", "options", "multiSelect"})):
        return f"the question has an unexpected field ({', '.join(extra)})"
    if only.get("question") != text:
        return "the question text differs from the printed one"
    if only.get("header") != HEADER:
        return f"the header is not {HEADER!r}"
    if only.get("multiSelect"):
        return "the question allows more than one choice"
    if not isinstance(given, list) or not all(isinstance(o, dict) for o in given):
        return "the options are missing"
    if extra := sorted(str(k) for o in given for k in set(o) - {"label", "description"}):
        return f"an option has an unexpected field ({', '.join(extra)})"
    if [(o.get("label"), o.get("description")) for o in given] != list(zip(options(found), descriptions(found), strict=True)):
        return "the options, their order or their descriptions differ from the printed ones"
    return ""


def _tag(log: EventLog, where: str, minutes: int, uses: int) -> str:
    return log.mac(f"{KIND}|{where}|{minutes}|{uses}".encode())[:12]


def echo_for(log: EventLog, found: Facts, minutes: int = person_store.DEFAULT_MINUTES, uses: int = 1) -> str:
    """The question's final line: kind, target, life and uses, with a tag made under the store's key."""
    return f"authorize {KIND} {target(found)} {minutes}m x{uses} #{_tag(log, target(found), minutes, uses)}"


def approved(log: EventLog, text: str, label: str, project: str) -> Facts | None:
    """The `Facts` an answered question approves: a tagged echo made by this store, a checkout that is a worktree of the session's ``project``, the question the code now renders, and the approving ``label``; None when it is not an approval of ours, `Refused` when it is one that no longer holds."""
    *body, last = text.rstrip().splitlines() or [""]
    echoed = ECHO.fullmatch(last.strip())
    if not project or echoed is None or int(echoed["minutes"]) != person_store.DEFAULT_MINUTES or int(echoed["uses"]) != 1:
        return None
    if not hmac.compare_digest(_tag(log, echoed["target"], person_store.DEFAULT_MINUTES, 1), echoed["tag"]):
        return None
    parts = TARGET.fullmatch(echoed["target"])
    if parts is None or not label.startswith("Approve "):
        return None
    found = facts(parts["top"], Path(parts["top"], "scripts", parts["name"]), parts["args"].split(","), project)
    if found is None or target(found) != echoed["target"] or "\n".join(body).strip() != question(found):
        raise Refused("the checkout, the runtime or the repository is no longer what the question said")
    return found if label == options(found)[0] else None


def propose(argv: list[str], cwd: str, log: EventLog | None = None) -> tuple[int, str]:
    """The exit code and text of `run-approval propose NAME ARGS...`: the question, its echo line, the header and the two answers with their descriptions."""
    top = person_targets.run_git(cwd, "rev-parse", "--show-toplevel")
    try:
        found = facts(cwd, Path(top, "scripts", argv[0]), argv[1:], session_project()) if argv and top else None
    except Refused as why:
        return 1, f"git cannot offer that run: {why}"
    if found is None:
        return 1, ("that is not a run the owner can approve for an agent: it must be `release-key create --write` or "
                   "`github-protection-apply --apply --repo OWNER/NAME` (the origin's repository), run from a checkout")
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
    """The environment an approved run gives its children: the account's home, the agent markers, POOLHOUSE_ settings, the system tool folders, an empty `gh` configuration folder and no bytecode written or read."""
    found = os.environ if source is None else source
    kept = {k: v for k, v in found.items() if k in KEPT or k in person.AGENT_MARKERS or k.startswith("POOLHOUSE_")}
    kept.pop(TICKET_ENV, None)
    kept["HOME"] = str(home.account_roots()[0].parent)
    kept["PATH"] = os.pathsep.join(TRUSTED_DIRS)
    kept["GH_CONFIG_DIR"] = str(config)
    kept["PYTHONDONTWRITEBYTECODE"] = "1"
    kept["PYTHONPYCACHEPREFIX"] = f"{config}.pyc"
    return kept


def launcher(found: Facts, typed: list[str], ticket: Path, source: Mapping[str, str]) -> str:
    """The one command line the main session's approved run starts as: a cleared environment, the selected runtime's interpreter isolated and reading no bytecode from the checkout, and the ticket named by file."""
    env = {"HOME": str(home.account_roots()[0].parent), "PATH": os.pathsep.join(TRUSTED_DIRS), "CLAUDECODE": "1", TICKET_ENV: str(ticket)}
    env |= {k: source[k] for k in (*KEPT, "POOLHOUSE_HOME") if k in source}
    words = ["/usr/bin/env", "-i", *(f"{k}={v}" for k, v in env.items()), found.python, "-I", "-B", "-X", f"pycache_prefix={ticket}.pyc",
             str(Path(found.top, "scripts", found.name)), *typed]
    return " ".join(shlex.quote(word) for word in words)


def _code_roots(found: Facts) -> tuple[str, ...]:
    """Where the Poolhouse of an approved run may be loaded from: the hashed src of the checkout or the verified runtime built from that src."""
    return (os.path.realpath(Path(found.top, "src")), found.prefix)


def verify_loaded(found: Facts, roots: tuple[str, ...] | None = None, modules: Mapping[str, object] | None = None) -> None:
    """Raise `Refused` naming the first loaded `poolhouse` module that does not lie under ``roots`` (default `_code_roots`), or has no place to check."""
    allowed = roots or _code_roots(found)
    for name, module in list((sys.modules if modules is None else modules).items()):
        if name != "poolhouse" and not name.startswith("poolhouse."):
            continue
        given = getattr(module, "__file__", None)
        places = [given] if given else list(getattr(module, "__path__", None) or [])
        if not places:
            raise Refused(f"{name} was loaded from no place that can be checked")
        for place in places:
            if not any(Path(os.path.realpath(place)).is_relative_to(root) for root in allowed):
                raise Refused(f"{name} was loaded from {place}, outside the code the owner approved")


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
        try:
            found = facts(Path(script).parent, script, args)
        except Refused as why:
            raise person.HumanRequired(f"{refused}; {why}") from why
        if found is None:
            raise
        loaders = sorted(k for k in os.environ if k.startswith(LOADER_PREFIXES))
        if loaders:
            raise person.HumanRequired(f"{refused}; an approved run starts with none of {', '.join(loaders)} set") from None
        try:
            verify_loaded(found)
            redeem(found, secret, session)
        except Refused as why:
            raise person.HumanRequired(f"{refused}; {why}") from why
        except (person_auth.NotAuthorized, person_store.Unreadable) as missing:
            raise person.HumanRequired(f"{refused}; {_how(found)}") from missing
        config = Path(tempfile.mkdtemp(prefix="gh-config-"))
        return Approval(found, child_environment(config), config)
    return None
