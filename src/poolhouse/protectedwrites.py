"""The paths an agent's shell may not write (the guards, the hook settings, git's hooks and config, the harness record's
code, the authority registry) or touch at all (the person folder and the session tokens), and the shell forms that
reach them. Every refusal is automatic and immediate: it says why and what to do instead, and nothing waits for anyone.
The person folder, the session tokens and the authority registry are protected everywhere; a caller that passes ``owns``
(whether a path lies in Poolhouse's own repository) limits every other rule to that repository.
Standard library only: the hook scripts load it by file through `worktreerules`."""

from __future__ import annotations

import fnmatch
import functools
import importlib.util
import itertools
import os
import re
import shlex
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

__all__ = ["TooComplex", "blanked", "command_texts", "guarded", "holds_secret", "other_agent", "other_texts", "refusal", "secret",
           "too_deep", "tool_refusal", "without_heredocs"]

_spec = importlib.util.spec_from_file_location("_poolhouse_protectedshell", Path(__file__).with_name("protectedshell.py"))
_shell = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _shell  # its dataclasses look themselves up there while the file runs
_spec.loader.exec_module(_shell)
blanked = _shell.blanked

GUARD_PATHS = (
    r"src/poolhouse/workspace/harness_ancestry\.py",
    r"scripts/hooks/(claude-harness-bind|harness-ancestry)",
    r"scripts/(release-key|github-protection-apply|github-protection|dev-branch-rule|agent-identity)", r"src/poolhouse/credentials/.*",
    r"\.git/(refs/remotes/.*|packed-refs)",
    r"\.git/(hooks(/.*)?|config(\.worktree)?|worktrees/[^/]+/config\.worktree)",
    r"\.gitconfig", r"\.config/git/(config|hooks(/.*)?)", r"\.poolhouse/(authority\.json|authority-audit\.jsonl)")
GUARDS = re.compile(r"(^|/)(" + "|".join(GUARD_PATHS) + ")$", re.I)
REGISTRY = re.compile(r"(^|/)" + GUARD_PATHS[-1] + "$", re.I)
SECRETS = re.compile(r"(^|/)\.poolhouse/(person|node/client|node/person)(/|$)", re.I)
HELD = tuple(tuple(path.split("/")) for path in (
    "src/poolhouse/workspace/harness_ancestry.py", "scripts/hooks/harness-ancestry",
    "scripts/hooks/claude-harness-bind", "scripts/release-key", "src/poolhouse/credentials/__init__.py",
    ".git/hooks/pre-push", ".git/config", ".config/git/config", ".gitconfig"))
KEPT = re.compile(r"\.poolhouse/(person|node/client|node/person|authority)", re.I)
MENTION = re.compile(r"\.git/hooks|" + KEPT.pattern, re.I)
"""What code run from a shell may not name: git's hooks, the owner's keys, the session tokens and the authority registry. Code that only names
git's settings or a guard's file runs (a test or a document does); writing those by redirect, copy, edit in place or `git config` is refused."""

NATIVE_SESSION = frozenset({"POOLHOUSE_SESSION_ID", "POOLHOUSE_SESSION_HARNESS", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"})
"""The variables by which a harness names the main session a shell belongs to. The stamp takes them out of a subagent's shell, so
a subagent that drops its own marker has no identity; a command that sets one would put the lead's back."""

WRAPPERS = frozenset({"sudo", "doas", "command", "exec", "nohup", "time", "nice", "ionice", "env", "setsid", "caffeinate",
                      "stdbuf", "unbuffer", "builtin", "timeout", "xargs"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
INLINE = {"python": "-c", "python3": "-c", "node": "-e", "nodejs": "-e", "bun": "-e", "ruby": "-e", "perl": "-e", "php": "-r",
          "lua": "-e", "osascript": "-e"}
EDITORS = frozenset({"vi", "vim", "nvim", "nano", "emacs", "ed", "ex", "patch", "sponge", "tee"})
MODIFIERS = frozenset({"rm", "unlink", "rmdir", "truncate", "touch", "chmod", "chown", "chattr", "setfacl", "xattr", "shred"})
IN_PLACE = frozenset({"sed", "awk", "gawk", "perl"})
COPIERS = frozenset({"cp", "install", "ln", "rsync", "cpio", "dd"})
DEEP_WITH_R = frozenset({"rm", "chmod", "chown", "chattr", "setfacl", "xattr", "shred"})
MODE = re.compile(r"[0-7]{3,4}|[ugoa]*[-+=][rwxXstugo]*(,[ugoa]*[-+=][rwxXstugo]*)*")
FIND_FILES = frozenset({"-fprint", "-fprint0", "-fprintf", "-fls"})
WALKERS = frozenset({"rg", "ag", "ack", "tar", "zip", "7z", "7za", "ditto", "cpio", "rsync"})
"""Commands that read every file under the folders they are given."""
GREPS = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "ack"})
"""Searches that look in the working folder when they are given no folder."""
SEARCH_VALUES = frozenset({"-A", "-B", "-C", "-m", "-d", "-D", "-g", "-t", "-T", "-j", "--include", "--exclude", "--exclude-dir",
                           "--max-count", "--glob", "--type", "--type-not", "--threads", "--context", "--after-context",
                           "--before-context"})
"""Options of a search that take a value, which is not a folder."""
FIND_LOGIC = frozenset({"-o", "-or", "!", "-not"})
READ_ONLY = frozenset({"wc", "grep", "egrep", "fgrep", "cat", "head", "tail", "ls", "stat", "du", "diff", "cmp", "sha1sum",
                       "sha256sum", "shasum", "md5", "md5sum", "od", "strings", "echo", "printf", "true", "basename", "dirname",
                       "realpath", "readlink", "test"})
FETCHERS = {"curl": ("-o", "--output"), "wget": ("-O", "--output-document")}
GIT_CONFIG = re.compile(r"^(core\.(hookspath|fsmonitor|sshcommand|askpass)|credential\..*helper|include(if\..*)?\.path)$", re.I)
GIT_ENV = re.compile(r"^GIT_CONFIG_(COUNT|KEY_\d+|VALUE_\d+|GLOBAL|SYSTEM|PARAMETERS)$")
GIT_PATHS = frozenset({"rm", "mv", "update-index", "clean"})
"""The git commands that remove or redirect a guard. `checkout` and `restore` put a committed copy back, in a worktree whose hooks are not the
ones that run; the primary checkout refuses them by its own rule (`worktreerules`)."""
GIT_TAKES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
MUTATING = frozenset({"PUT", "POST", "PATCH", "DELETE"})
API_VALUES = frozenset({"-X", "--method", "-H", "--header", "-f", "-F", "--field", "--raw-field", "--input", "-q", "--jq",
                        "-t", "--template", "--hostname", "--cache", "-p", "--preview"})
API_BODY = ("-f", "-F", "--field", "--raw-field", "--input")
ADMIN = re.compile(r"rulesets?|protection|secrets|variables|environments|collaborators|hooks|keys|invitations|merges"
                   r"|git/refs|releases|actions|dispatches|workflows|teams|memberships|transfer|rename|branches|rules|pulls/[^/]+/merge")
GRAPHQL = re.compile(r"BranchProtectionRule|Ruleset|mergePullRequest|enablePullRequestAutoMerge|updateRepository"
                     r"|deleteRepository|updateRef|createRef|deleteRef|Secret", re.I)
GH_BLOCKED: dict[str, frozenset[str] | None] = {
    "secret": None, "variable": None, "ruleset": None, "extension": None,
    "repo": frozenset({"edit", "delete", "rename", "archive", "transfer"}),
    "workflow": frozenset({"enable", "disable"}), "release": frozenset({"create", "delete", "edit", "upload"}),
    "auth": frozenset({"login", "logout", "refresh", "setup-git", "token", "switch"}),
    "alias": frozenset({"set", "import"}), "config": frozenset({"set"})}
CHECK_WORKFLOWS = frozenset({"ci.yml", "redteam.yml", "audit.yml", "pool-check.yml", "release-dry-run.yml"})
"""The workflows an agent may start by name: they only check. Any other can publish."""
READERS = re.compile(r"read|get|list|search|view|find|stat|head|tail|cat|show|describe|query|fetch")
DEPTH = 3
SCRIPT_BYTES = 65536
MOST_PATH = 4096
MOST_COMMAND = 200_000
MOST_EXPANSIONS = 256
MOST_DEPTH = 32
"""What the rules read in bounded work: a command (outside its heredoc bodies) of at most this many characters, a word that
expands to at most this many names, and a tool input nested at most this deep. More is refused, never read in part."""
PATH_KEY = re.compile(r"(path|paths|file|files|dir|dirs|directory|directories|folder|folders|cwd|root|source|destination|target"
                      r"|src|dst|location|locations)$")
COMMAND_KEY = re.compile(r"(command|commands|cmd|cmds|script|scripts|argv|commandline|cmdline)$|^(run|exec|execute|shell|bash|zsh|sh)$")
"""Keys of a tool input, with every character but a letter taken out and lower case, that hold a path or a command."""
TOO_COMPLEX = "is too long or too nested for the rules to read in bounded work: split it into smaller steps"
UNJUDGEABLE = (ArithmeticError, AttributeError, LookupError, OSError, RecursionError, TypeError, ValueError)
"""What text the rules cannot take raises in them (a NUL or half a character in a name, a command that is no text): it is refused.
Anything else ends the hook, which refuses the call too (`scripts/hooks/rules_loader.closed`)."""
REFUSED = ("refused to an agent: the guards, the hook settings, git's hooks and config and the authority registry decide what an "
           "agent may run, so an agent never changes them; leave them as they are and say in the report what should change")
NOT_READ = "refused to an agent: the person folder holds the owner's keys and the session tokens; read a file of the project instead"


def _roots(environ: Mapping[str, str]) -> list[str]:
    """The case-folded active state root, as a list of at most one."""
    named = environ.get("POOLHOUSE_HOME")
    return [os.path.normpath(named).casefold()] if named else []


def secret(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` lies in the person folder or the session tokens."""
    if SECRETS.search(path):
        return True
    places, low = ("/person", "/node/client", "/node/person"), path.casefold()
    return any(low == root + p or low.startswith(root + p + "/") for root in _roots(environ or os.environ) for p in places)


def _holds_a_guard(path: str) -> bool:
    """Whether ``path`` is a folder that holds one of the guard files, as the repository root or as `scripts/hooks`."""
    folder = path.rstrip("/")
    low = folder.casefold()
    for held in HELD:
        for i in range(len(held)):
            head = "/".join(held[:i])
            inside = not head or low == head or low.endswith("/" + head)
            if inside and os.path.lexists(f"{folder}/{'/'.join(held[i:])}"):
                return True
    return False


def file_guarded(path: str, environ: Mapping[str, str] | None = None, owns: Callable[[str], bool] | None = None) -> bool:
    """Whether ``path`` is itself protected: the authority registry, or (where ``owns`` reaches) a guard, a hook setting,
    git's hooks or config."""
    if REGISTRY.search(path) or any(path.casefold() in (f"{root}/authority.json", f"{root}/authority-audit.jsonl")
                                    for root in _roots(environ or os.environ)):
        return True
    return bool(GUARDS.search(path)) and (owns is None or owns(path))


def guarded(path: str, environ: Mapping[str, str] | None = None, owns: Callable[[str], bool] | None = None) -> bool:
    """Whether a write to ``path`` (a file, or a folder holding one) is refused to an agent: the guards, settings, git's hooks."""
    return file_guarded(path, environ, owns) or (_holds_a_guard(path) and (owns is None or owns(path)))


class TooComplex(ValueError):
    """The input is past what the rules read in bounded work (`MOST_COMMAND`, `MOST_EXPANSIONS`, `MOST_DEPTH`)."""


def _braces(word: str) -> list[str]:
    """The names a word with `{a,b}` groups stands for; `TooComplex` past `MOST_EXPANSIONS` of them."""
    names = [word]
    while True:
        found = next(((i, m) for i, name in enumerate(names) if (m := re.search(r"\{([^{}]*,[^{}]*)\}", name))), None)
        if found is None:
            return names
        i, group = found
        names[i:i + 1] = [names[i][:group.start()] + part + names[i][group.end():] for part in group.group(1).split(",")]
        if len(names) > MOST_EXPANSIONS:
            raise TooComplex(f"a word with more than {MOST_EXPANSIONS} brace expansions")


def _script_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:SCRIPT_BYTES] if path.is_file() else ""
    except OSError:
        return ""


def _fits(name: str, pattern: str) -> bool:
    """Whether the glob ``pattern`` matches ``name``, whatever the case of either."""
    try:
        return fnmatch.fnmatchcase(name.casefold(), pattern.casefold())
    except re.error:  # before Python 3.10, a range that runs backwards (`[t-3]`) raises; later ones match no name with it
        return False


class Shell:
    """What the command line being read knows: the working folder, the variables set so far and what it may do."""

    def __init__(self, here: Path, environ: Mapping[str, str], released: Callable[[str], str], owns: Callable[[str], bool] | None) -> None:
        self.here, self.environ, self.released, self.owns, self.variables = here, environ, released, owns, dict[str, str]()

    def repository(self, moved: str = "") -> bool:
        """Whether the repository's rules apply to a command run in the working folder (or in ``moved`` from it)."""
        return self.owns is None or self.owns(str(self.here / self.home(moved)))

    def move(self, folder: str, physical: bool = False) -> None:
        """Change the working folder as `cd` does: a step up (`..`) is taken in the name by default and, with ``physical``
        (`cd -P`), in the folder a link leads to."""
        target = self.here / self.home(folder)
        self.here = Path(os.path.realpath(target) if physical else os.path.normpath(target))

    def home(self, word: str) -> str:
        """``word`` with a leading `~` and the variables set so far expanded."""
        known = {**self.environ, **self.variables}
        word = re.sub(r"\$\{?(\w+)\}?", lambda m: known.get(m.group(1), m.group(0)), word)
        return known.get("HOME", "~") + word[1:] if word == "~" or word.startswith("~/") else word

    def paths(self, word: str) -> list[str]:
        """The absolute paths ``word`` names: variables, `~`, braces and globs expanded, links resolved through the deepest
        folder that exists."""
        out: list[str] = []
        for one in _braces(self.home(word)):
            # the kernel follows a link before it takes a `..`, so the place is found from the name as written, not the collapsed one
            joined = os.path.normpath(self.here / one)
            real = os.path.realpath(self.here / one)
            out += [joined, real]
            if re.search(r"[*?\[]", one):
                out += [*self.matches(joined), *self.matches(real)]
        return out

    def matches(self, pattern: str) -> list[str]:
        """The existing paths a glob ``pattern`` matches with their links resolved, and the guard files it would match if
        they existed."""
        anchor = Path(pattern).anchor
        try:
            found = [str(p) for p in Path(anchor).glob(pattern[len(anchor):])] if anchor else []
        except (ValueError, OSError, re.error):  # a pattern the matcher cannot take (`a**b`, `a[t-3]`) names no existing file
            found = []
        roots = [self.here, *self.here.parents]
        guards = [os.path.normpath(root / "/".join(held)) for root in roots for held in HELD]
        return [*found, *map(os.path.realpath, found), *(g for g in guards if _fits(g, pattern))]

    def hits(self, words: list[str], test: Callable[[str], bool]) -> str:
        """The first of ``words`` that names a path for which ``test`` holds, else an empty string."""
        return next((w for w in words if any(test(path) for path in self.paths(w))), "")

    def guarded(self, words: list[str], deep: bool = False) -> str:
        """The first of ``words`` that names a guard (with ``deep``, also a folder holding one)."""
        test = guarded if deep else file_guarded
        return self.hits(words, lambda p: test(p, self.environ, self.owns))

    def landing(self, sources: list[str], dest: str) -> list[str]:
        """The files a copy of ``sources`` to ``dest`` writes: ``dest`` itself, or a file of each name inside a folder."""
        out: list[str] = []
        for place in self.paths(dest):
            inside = os.path.lexists(place) and Path(place).is_dir()  # `lexists` is False for a name too long, which `is_dir` raises on
            out += [f"{place}/{Path(s).name}" for s in sources] if inside else [place]
        return out

    def lands_on(self, sources: list[str], dests: list[str]) -> str:
        """The first guard file that a copy or move of ``sources`` to ``dests`` writes, else an empty string."""
        written = [p for dest in dests for p in self.landing(sources, dest)]
        return next((p for p in written if file_guarded(p, self.environ, self.owns)), "")


def _transfer(args: list[str]) -> tuple[list[str], list[str]]:
    """The sources and the destinations of a cp, mv, install or ln command line (``-t`` names the destination folder)."""
    plain = [a for a in args if not a.startswith("-")]
    into = [a.split("=", 1)[1] for a in args if a.startswith("--target-directory=")]
    into += [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-t"]
    return ([a for a in plain if a not in into], into) if into else (plain[:-1], plain[-1:])


def _copy_hit(verb: str, args: list[str], shell: Shell) -> str:
    """The first file a cp, install, ln, mv or dd writes that is a guard, else an empty string."""
    if verb == "dd":
        written = [p for a in args if a.startswith("of=") for p in shell.paths(a[3:])]
        return next((p for p in written if file_guarded(p, shell.environ, shell.owns)), "")
    return shell.lands_on(*_transfer(args))


def _move_hit(args: list[str], shell: Shell) -> str:
    """The first guard (or folder holding one) that a `mv` takes away, or the first guard file it writes."""
    sources, _ = _transfer(args)
    return shell.guarded(sources, deep=True) or _copy_hit("mv", args, shell)


def _walks(verb: str, args: list[str], fed: bool = False) -> bool:
    """Whether the command reads what is under the folders it is given: a search or an archive of a tree, a recursive copy, or a
    find that runs a command on what it finds (``fed``: whose names go to another command, through `xargs` or a substitution)."""
    if verb in WALKERS:
        return True
    if verb in GREPS:
        return _recursive(args)
    if verb in ("cp", "scp"):
        return _recursive(args) or any(re.fullmatch(r"-[a-zA-Z]*a[a-zA-Z]*", a) for a in args)
    return verb == "find" and (fed or bool(_shell.FIND_EXEC.intersection(args)))


def _folders(verb: str, args: list[str], shell: Shell) -> list[str]:
    """The folders a walking command reads under: its folder operands, and the working folder when a search is given none. A
    `-C` (`--directory`) moves the working folder for what follows, as it does for `tar`."""
    if verb == "find":
        return _find_starts(args)
    operands: list[str] = []
    searching = verb in GREPS
    asked = not any(a in ("-e", "-f", "--regexp", "--file") or a.startswith(("--regexp=", "--file=")) for a in args)
    i = 0
    while i < len(args):
        word = args[i]
        if word in ("-C", "--directory") and not searching and i + 1 < len(args):
            shell.move(args[i + 1])
            i += 2
        elif word.startswith("--directory=") and not searching:
            shell.move(word.split("=", 1)[1])
            i += 1
        elif word in SEARCH_VALUES and searching:
            i += 2
        else:
            if not word.startswith("-"):
                operands.append(word)
            i += 1
    return [*operands, "."] if searching and len(operands) <= int(asked) else operands


def _recursive(args: list[str]) -> bool:
    return any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", a) or a == "--recursive" for a in args)


def _find_starts(args: list[str]) -> list[str]:
    """The folders a `find` searches: the words before its first primary, or the working folder when it names none."""
    body = [a for a in args if a not in ("-H", "-L", "-P")]
    return list(itertools.takewhile(lambda a: not a.startswith(("-", "!", "(")), body)) or ["."]


def _find_hit(args: list[str], shell: Shell) -> str:
    """The guard a `find` writes with `-fprint`, or the folder it may reach a guard in when it deletes or runs a command that
    is not a plain reader; name patterns limit it only when no `-o`, `-or`, `!` or `-not` joins or negates the tests."""
    written = shell.guarded([args[i + 1] for i, a in enumerate(args[:-1]) if a in FIND_FILES])
    verbs = [args[i + 1] for i, a in enumerate(args[:-1]) if a in _shell.FIND_EXEC]
    if written or ("-delete" not in args and all(v in READ_ONLY for v in verbs)):
        return written
    patterns = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-name", "-iname", "-path", "-ipath", "-wholename")]
    names = [Path("/".join(held)) for held in HELD]
    limited = bool(patterns) and not FIND_LOGIC.intersection(args)
    could = not limited or any(_fits(text, p) for p in patterns for n in names for text in (n.name, str(n), "./" + str(n)))
    return shell.guarded(_find_starts(args), deep=True) if could else ""


def _only_adds(mode: str) -> bool:
    """Whether a chmod ``mode`` leaves the owner's read and execute permissions in place."""
    if mode[0].isdigit():
        return (int(mode[-3]) & 5) == 5
    clauses = [re.fullmatch(r"([ugoa]*)([-+=])[rwxXstugo]*", c) for c in mode.split(",")]
    return all(c and (c.group(2) == "+" or (c.group(1) != "" and set(c.group(1)) <= set("go"))) for c in clauses)


def _deep(verb: str, args: list[str]) -> bool:
    """Whether the folders named reach every file in them: a recursive rm, chmod or the like, other than a chmod that only
    adds permissions or leaves the owner's alone."""
    if verb not in DEEP_WITH_R or not _recursive(args):
        return False
    modes = [a for a in args if MODE.fullmatch(a)]
    return not (verb == "chmod" and modes and all(_only_adds(m) for m in modes))


def _in_place(args: list[str]) -> bool:
    return any(re.match(r"^-[a-zA-Z]*i", a) or a in ("--in-place", "inplace") for a in args)


def _fetched(verb: str, args: list[str]) -> list[str]:
    flags = FETCHERS.get(verb, ())
    named = [args[i + 1] for i, a in enumerate(args[:-1]) if a in flags]
    return named + [a.split("=", 1)[1] for a in args if a.startswith("--output=")]


def _writes(verb: str, args: list[str], shell: Shell) -> str:
    plain = [a for a in args if not a.startswith("-")]
    if verb in COPIERS:
        found = _copy_hit(verb, args, shell)
    elif verb == "mv":
        found = _move_hit(args, shell)
    elif verb == "find":
        found = _find_hit(args, shell)
    elif verb in MODIFIERS or verb in EDITORS:
        found = shell.guarded(plain, deep=_deep(verb, args))
    elif verb in IN_PLACE:
        found = shell.guarded(plain) if _in_place(args) else ""
    else:
        found = shell.guarded(_fetched(verb, args))
    return f"`{verb}` would change {found}: {REFUSED}." if found else ""


def _shell_code(args: list[str]) -> str:
    """The string a shell is asked to run with `-c` (or a bundled flag such as `-lc`), else an empty string."""
    for i, a in enumerate(args[:-1]):
        if re.fullmatch(r"-[a-z]*c", a):
            return args[i + 1]
    return ""


def _code(verb: str, args: list[str], bodies: list[str], shell: Shell) -> str:
    """Why interpreted code (inline, a heredoc or a script file) is refused: it names what `MENTION` lists."""
    if verb not in INLINE and verb not in SHELLS and verb not in ("source", "."):
        return ""
    flag = INLINE.get(verb, "")
    given = args[args.index(flag) + 1:][:1] if flag in args else []
    texts = [*given, *bodies]
    scripts = [a for a in args if not a.startswith("-")]
    if scripts and not shell.guarded(scripts[:1]):
        texts.append(_script_text(shell.here / scripts[0]))
    found = next((m.group(0) for t in texts for m in [(MENTION if shell.repository() else KEPT).search(t)] if m), "")
    return f"code run from a shell names {found}: {REFUSED}; use the file tools for anything else." if found else ""


def _first_words(words: list[str]) -> tuple[list[str], dict[str, str]]:
    """``words`` without leading wrappers, their flags and assignments, and those assignments."""
    assigned: dict[str, str] = {}
    i = 0
    while i < len(words):
        word, before = words[i], (words[i - 1].rsplit("/", 1)[-1] if i else "")
        if re.match(r"[A-Za-z_]\w*=", word):
            assigned[word.split("=", 1)[0]] = word.split("=", 1)[1]
        elif not (word.rsplit("/", 1)[-1] in WRAPPERS or (before in WRAPPERS and (word.startswith("-")
                                                                             or re.fullmatch(r"[\d.]+[smhd]?", word)))):
            break
        i += 1
    return words[i:], assigned


def _gh(args: list[str], shell: Shell) -> str:
    verbs = [a for i, a in enumerate(args) if not a.startswith("-") and args[i - 1:i] not in (["-R"], ["--repo"])]
    head, rest = (verbs[0] if verbs else ""), verbs[1:2]
    skips = any(a.startswith("--admin") for a in args) and "`--admin` merges past the required checks, which no agent does"
    if head == "pr" and rest == ["merge"] and (why := skips or shell.released(str(shell.here))):
        return ("`gh pr merge` is refused to an agent: into the development branch it needs a passed lead-review status on the head it names, elsewhere a "
                f"standing independent land-review accept (`poolhouse-workspace land-review`) and a clean checkout; {why}.")
    if head == "workflow" and rest == ["run"] and not (verbs[2:] and verbs[2] in CHECK_WORKFLOWS):
        return ("`gh workflow run` of anything but a workflow that checks is refused to an agent, since another can publish; name one "
                f"before any flag ({', '.join(sorted(CHECK_WORKFLOWS))}).")
    blocked = GH_BLOCKED.get(head, frozenset({"-"}))
    if blocked is None or (rest and rest[0] in blocked):
        return (f"`gh {head}` changes how the repository is protected or who may act on it: refused to an agent; "
                "leave the settings as they are and say in the report what should change.")
    return _gh_api(args) if head == "api" else ""


def _endpoint(args: list[str]) -> str:
    skip = False
    for a in args[1:]:
        if skip:
            skip = False
        elif a in API_VALUES:
            skip = True
        elif not a.startswith("-"):
            return a
    return ""


def _gh_api(args: list[str]) -> str:
    method = next((args[i + 1].upper() for i, a in enumerate(args[:-1]) if a in ("-X", "--method")), "")
    body = any(a in API_BODY or a.startswith(("--field=", "--raw-field=")) for a in args)
    endpoint = _endpoint(args)
    if endpoint == "graphql":
        return ("a GraphQL change to protection is refused to an agent; leave the settings as they are and say in the report "
                "what should change.") if GRAPHQL.search(" ".join(args)) else ""
    writes = method in MUTATING or (body and not method)
    if writes and (ADMIN.search(endpoint) or re.fullmatch(r"repos/[^/]+/[^/]+/?", endpoint)):
        return ("`gh api` writing to protection, secrets, releases, settings or a merge is refused to an agent; leave them as they are "
                "and say in the report what should change.")
    return ""


def _git_sub(args: list[str]) -> tuple[str, list[str], str]:
    """The git subcommand, the words after it, and the folder `-C` moved to."""
    moved, i = "", 0
    while i < len(args):
        if args[i] == "-C" and i + 1 < len(args):
            moved = args[i + 1]
        if args[i] in GIT_TAKES:
            i += 2
        elif args[i].startswith("-"):
            i += 1
        else:
            return args[i], args[i + 1:], moved
    return "", [], moved


def _git_config(args: list[str]) -> str:
    """Why a `git -c ...` or `git config ...` is refused: it moves where hooks run or what git runs."""
    if any(a == "-c" and GIT_CONFIG.match(args[i + 1].split("=", 1)[0]) for i, a in enumerate(args[:-1])):
        return "`git -c` that changes where hooks run or what git runs is refused to an agent: the hooks are the last checks."
    sub, rest, _ = _git_sub(args)
    key = next((a for a in rest if not a.startswith("-")), "")
    reading = any(a in ("--get", "--get-all", "--get-regexp", "-l", "--list", "--show-origin") for a in rest)
    removing = any(a.startswith("--unset") for a in rest)
    setting = len([a for a in rest if not a.startswith("-")]) > 1
    if sub == "config" and GIT_CONFIG.match(key) and not reading and (setting or removing):
        return f"`git config {key}` changes where hooks run or what git runs: refused to an agent, since the hooks are the last checks."
    return ""


def _git_paths(args: list[str], shell: Shell) -> str:
    sub, rest, moved = _git_sub(args)
    if sub not in GIT_PATHS:
        return ""
    plain = [a for a in rest if not a.startswith("-")]
    named = rest[rest.index("--") + 1:] if "--" in rest else (plain if sub in ("rm", "mv") else [])
    here = shell.here
    if moved:
        shell.move(moved)
    try:
        if sub == "mv":
            found = shell.guarded(named[:-1], deep=True) or shell.lands_on(named[:-1], named[-1:])
        else:
            found = shell.guarded(named, deep=sub == "rm")
    finally:
        shell.here = here
    return f"`git {sub}` would change {found}: {REFUSED}." if found else ""


def _fetch(words: list[str], shell: Shell) -> str:
    text = " ".join(words)
    if shell.repository() and "api.github.com" in text and re.search(r"-X\s*(PUT|POST|PATCH|DELETE)|--request\s+(PUT|POST|PATCH|DELETE)|\s-d\b|--data", text):
        return ("writing to the GitHub API by hand skips the low-right identity the agents hold: refused to an agent; "
                "push and publish through `scripts/agent-identity`.")
    return _writes(words[0].rsplit("/", 1)[-1], words[1:], shell)


def _cd_folder(args: list[str]) -> tuple[str, bool]:
    """The folder a `cd` goes to and whether it follows links before a `..` (`-P`); the flags (`-P`, `-L`, `-e`, `-@`) and
    `--` are not the folder, and `cd -` (back to the earlier folder) names none."""
    physical, i = False, 0
    while i < len(args) and args[i].startswith("-") and args[i] != "-":
        if args[i] == "--":
            i += 1
            break
        for flag in args[i][1:]:
            physical = flag == "P" if flag in "PL" else physical
        i += 1
    return (args[i] if i < len(args) and args[i] != "-" else ""), physical


class Reader:
    """Reads one command line; `command` is the first reason a command in it is refused."""

    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.fed = False

    def command(self, text: str, depth: int = 0, fed: bool = False) -> str:
        """Why ``text``, a command line, may not run; an empty string when it may. ``fed``: the names it prints go to another
        command (it is the body of a substitution)."""
        text, bodies = _shell.heredocs(text)
        if len(text) > MOST_COMMAND:
            raise TooComplex(f"a command of {len(text)} characters")
        flat, subs = _shell.substitutions(_shell.flatten(text))
        for inner in subs:
            if depth < DEPTH and (why := self.command(inner, depth + 1, fed=True)):
                return why
        commands = _shell.commands(_shell.words(flat))
        before = self.fed
        self.fed = fed or any(word.rsplit("/", 1)[-1] == "xargs" for one in commands for word in one)
        try:
            for words in commands:
                if why := self.simple(words, bodies, depth):
                    return why
        finally:
            self.fed = before
        return ""

    def simple(self, words: list[str], bodies: list[str], depth: int) -> str:
        """Why one command may not run: a redirection into a guard, a secret named, or a verb that writes one."""
        return self.judge(*_shell.split_redirects(words), bodies, depth)

    def judge(self, words: list[str], written: list[str], read: list[str], bodies: list[str], depth: int) -> str:
        """Why the command ``words``, whose redirections write ``written`` and read ``read``, may not run."""
        shell = self.shell
        if blocked := shell.hits(written, lambda p: guarded(p, shell.environ, shell.owns) or secret(p, shell.environ)):
            return f"a redirection would write {blocked}: {REFUSED}."
        if blocked := shell.hits(read, lambda p: secret(p, shell.environ)):
            return f"a redirection would read {blocked}, in the person folder or a session token: {NOT_READ}."
        rest, assigned = _first_words(words)
        if not rest:
            shell.variables.update(assigned)
            return ""
        if shell.repository() and any(GIT_ENV.match(name) for name in assigned):
            return "GIT_CONFIG_* changes where hooks run: refused to an agent, since the hooks are the last checks."
        if mentioned := self.mentioned(rest):
            return f"{mentioned} is in the person folder or is a session token: {NOT_READ}."
        if reached := self._reaches(rest[0].rsplit("/", 1)[-1], rest[1:]):
            return (f"{reached} holds the person folder and the session tokens, and `{rest[0].rsplit('/', 1)[-1]}` would read what is "
                    f"under it: {NOT_READ}.")
        return self.verb(rest[0].rsplit("/", 1)[-1], rest, bodies, depth)

    def _reaches(self, verb: str, args: list[str]) -> str:
        """The folder a command reads under when that folder holds the person folder and the session tokens: the state folder, the
        home folder or one above it. Only a command that reads what is under a folder is asked (see `_walks`); naming, measuring,
        making, removing or handing a state folder to a program reads none of it. The working folder counts when a search is
        given no folder."""
        shell = self.shell
        if not _walks(verb, args, self.fed):
            return ""
        here = shell.here
        try:
            return shell.hits(_folders(verb, args, shell),
                              lambda p: holds_state(p, shell.environ) or above_state(p, shell.environ))
        finally:
            shell.here = here

    def mentioned(self, rest: list[str]) -> str:
        """The first word of the command that names the person folder or a session token, other than the text after `-m`."""
        found = self.shell.hits([a for a in rest if "=" not in a or "/" in a], lambda p: secret(p, self.shell.environ))
        message = any(a in ("-m", "--message") and rest[i + 1:i + 2] == [found] for i, a in enumerate(rest))
        return "" if message else found

    def verb(self, verb: str, rest: list[str], bodies: list[str], depth: int) -> str:
        """Why the command ``rest`` (its first word is ``verb``'s path) may not run."""
        shell, args = self.shell, rest[1:]
        if verb == "cd" and args:
            folder, physical = _cd_folder(args)
            if folder:
                shell.move(folder, physical)
            return ""
        if verb in SHELLS and (inner := _shell_code(args)) and depth < DEPTH:
            return self.command(inner, depth + 1) or _code(verb, args, bodies, shell)
        if verb == "eval" and depth < DEPTH:
            return self.command(" ".join(args), depth + 1)
        if verb == "gh":
            return _gh(args, shell) if shell.repository() else ""
        if verb == "git":
            return (_git_config(args) if shell.repository(_git_sub(args)[2]) else "") or _git_paths(args, shell)
        if verb in FETCHERS or verb in ("http", "https", "xh"):
            return _fetch(rest, shell)
        script = _script_text(shell.here / rest[0]) if "/" in rest[0] and not shell.guarded(rest[:1]) else ""
        return _writes(verb, args, shell) or _code(verb, args, bodies, shell) or (
            f"the script {rest[0]} names {m.group(0)}: {REFUSED}." if (m := (MENTION if shell.repository() else KEPT).search(script)) else "")


class ShellReader(Reader):
    """The same rules over the command as the shell reads it (`protectedshell.readings`), asked after `Reader` allows it, so
    they only add refusals: a protected path in a here-string or in a substitution held for later is read, and only the text of a
    git message is not a path."""

    def command(self, text: str, depth: int = 0, fed: bool = False) -> str:
        for read in _shell.readings(text):
            if len(read.kept) > MOST_COMMAND:
                raise TooComplex(f"a command of {len(read.kept)} characters")
            piped = fed or any(word.rsplit("/", 1)[-1] == "xargs" for one in read.commands for word in one.words)
            before = self.fed
            try:
                for one in read.commands:
                    self.fed = piped or one.fed
                    try:
                        if why := self.one(one, read.bodies, depth):
                            return why
                    except TooComplex:
                        if not one.loose:  # a stretch read with its quotes ignored is no word the shell expands
                            raise
            finally:
                self.fed = before
        return ""

    def one(self, one, bodies: list[str], depth: int) -> str:
        """Why one command (`protectedshell.Command`) may not run."""
        shell = self.shell
        for word in one.strings:  # the shell expands no glob or brace in a here-string
            if any(secret(place, shell.environ) for place in _places(shell.home(word), shell.here, shell.environ)):
                return f"a here-string names {word}, in the person folder or a session token: {NOT_READ}."
        for word in (*one.words, *one.strings):
            for inner in _shell.held(word) if depth < DEPTH else ():
                if why := self.one(inner, [], depth + 1):
                    return why
        return self.judge(one.words, one.written, one.read, bodies, depth)

    def verb(self, verb: str, rest: list[str], bodies: list[str], depth: int) -> str:
        return super().verb(_shell.interpreter(verb), rest, bodies, depth)

    def mentioned(self, rest: list[str]) -> str:
        records = rest[0].rsplit("/", 1)[-1] == "git" and _git_sub(rest[1:])[0] in ("commit", "tag", "merge", "notes")
        opened = [a for i, a in enumerate(rest) if not (records and rest[i - 1] in ("-m", "--message"))]
        return self.shell.hits([a for a in opened if "=" not in a or "/" in a], lambda p: secret(p, self.shell.environ))


def refusal(command: str, cwd: str, released: Callable[[str], str], environ: Mapping[str, str] | None = None,
            owns: Callable[[str], bool] | None = None) -> str:
    """Why the shell ``command``, run from ``cwd``, may not run; an empty string when it may. A command past the bounds of
    `MOST_COMMAND`, `MOST_EXPANSIONS` and `DEPTH` is refused, not read in part, and so is one the rules fail on. ``owns``
    limits the repository's rules."""
    asked = functools.lru_cache(maxsize=None)(released)  # the release check runs once for a folder, however often a merge is read
    try:
        for reader in (Reader, ShellReader):
            if why := reader(Shell(Path(cwd or Path.cwd()), environ or os.environ, asked, owns)).command(command):
                return why
        return ""
    except (TooComplex, RecursionError) as error:
        return f"the command {TOO_COMPLEX} ({error})."
    except UNJUDGEABLE as error:
        return f"the command could not be judged ({type(error).__name__}: {error}), so it does not run."


def without_heredocs(command: str) -> str:
    """``command`` without its heredoc bodies, which are data being written or code handed to an interpreter."""
    return _shell.heredocs(command)[0]


def other_texts(command: str, spell: Callable[[str], str] | None = None) -> list[str]:
    """The other ways ``command`` reads, for a rule that reads the text of `without_heredocs` (`protectedshell.other_texts`)."""
    return _shell.other_texts(command, without_heredocs(command), spell)


def other_agent(command: str, own: str) -> str:
    """Why a subagent named ``own`` may not run ``command``: it names another session by `--agent`, a token file, a token or
    `POOLHOUSE_WORKSPACE_AGENT`; an empty string when it does not."""
    try:
        return _named_by_the_line(command, own) or _named_as_the_shell_reads(command, own, 0)
    except UNJUDGEABLE as error:
        return f"you are {own}: the command could not be judged ({type(error).__name__}), so it does not run."


def _named_by_the_line(command: str, own: str) -> str:
    flat, subs = _shell.substitutions(_shell.flatten(_shell.heredocs(command)[0]))
    for inner in subs:
        if why := _named_by_the_line(inner, own):
            return why
    for words in _shell.commands(_shell.words(flat)):
        if why := _names_in(words, own):
            return why
        if (inner := _shell_code(words[1:])) and (why := _named_by_the_line(inner, own)):
            return why
    return ""


def _named_as_the_shell_reads(command: str, own: str, depth: int) -> str:
    for read in _shell.readings(command):
        for one in read.commands:
            if why := _names_in(one.words, own):
                return why
            if depth < DEPTH and (inner := _shell_code(one.words[1:])) and (why := _named_as_the_shell_reads(inner, own, depth + 1)):
                return why
    return ""


def _names_in(words: list[str], own: str) -> str:
    """Why the words of one command act as another session than ``own``."""
    if why := _sets_a_session(words, own):
        return why
    return next(filter(None, (_names_another(word, words[i + 1:i + 2], own) for i, word in enumerate(words))), "")


def _sets_a_session(words: list[str], own: str) -> str:
    """Why a command sets the variable that names the session which started the subagent ``own`` to a value: as an assignment
    in front of a command, after `env` or `export`, or on its own. An empty value only clears it, and a word of an argument that
    looks like an assignment is text."""
    rest, assigned = _first_words(words)
    if rest and rest[0].rsplit("/", 1)[-1] in ("export", "declare", "typeset", "readonly", "local"):
        assigned = {**assigned, **dict(w.split("=", 1) for w in rest[1:] if re.match(r"[A-Za-z_]\w*=", w))}
    named = next((name for name, value in assigned.items() if name in NATIVE_SESSION and value), "")
    if named:
        return f"you are {own}: {named} names the session that started you, and the harness names you in every command."
    return ""


def _names_another(word: str, after: list[str], own: str) -> str:
    name, equals, value = word.partition("=")
    if name == "--agent" and (value or after) and (value or after[0]) != own:
        return f"you are {own}: run as yourself, not as {value or after[0]}."
    if name == "POOLHOUSE_WORKSPACE_AGENT" and equals and value != own:
        return f"you are {own}: run as yourself, not as {value}."
    if name == "POOLHOUSE_SUBAGENT" and equals:
        return f"you are {own}: the harness names you in every command, and no command names another."
    if name in ("--token-file", "POOLHOUSE_WORKSPACE_TOKEN"):
        return f"you are {own}: another session's token is not yours to use."
    return ""


def too_deep(value: object, limit: int = MOST_DEPTH) -> bool:
    """Whether ``value`` (a decoded tool input) holds more than ``limit`` levels of mappings and lists; no recursion is used."""
    level = [value]
    for _ in range(limit + 1):
        level = [inner for one in level for inner in (one.values() if isinstance(one, Mapping)
                                                       else one if isinstance(one, (list, tuple)) else ())]
        if not level:
            return False
    return True


def _keyed(value: object, matches: re.Pattern[str], join: bool = False, named: bool = False, depth: int = 0) -> list[str]:
    """The strings under a key that ``matches`` (letters only, lower case), at any depth. With ``join``, a list of strings under
    such a key also stands for the command it spells as arguments."""
    if depth > MOST_DEPTH:
        raise TooComplex(f"a tool input nested more than {MOST_DEPTH} deep")
    if isinstance(value, str):
        return [value] if named else []
    if isinstance(value, Mapping):
        return [text for key, one in value.items()
                for text in _keyed(one, matches, join, named or bool(matches.search(re.sub("[^a-z]", "", str(key).lower()))), depth + 1)]
    if isinstance(value, (list, tuple)):
        words = [one for one in value if isinstance(one, str)]
        spelled = [shlex.join(words)] if join and named and words and len(words) == len(value) else []
        return [*spelled, *(text for one in value for text in _keyed(one, matches, join, named, depth + 1))]
    return []


def command_texts(given: Mapping[str, object]) -> list[str]:
    """The shell commands a tool input holds, however it spells them: under `command`, `cmd`, `script`, `argv`, `commands`,
    `run`... at any depth, as a string, as a list of commands or as the arguments of one."""
    return _keyed(given, COMMAND_KEY, join=True)


def _strings(value: object) -> list[str]:
    return _keyed(value, re.compile(""), named=True)


def _raw(text: str, here: Path, environ: Mapping[str, str]) -> Path:
    home = environ.get("HOME", "~")
    return here / (home + text[1:] if text == "~" or text.startswith("~/") else text)


def _place(text: str, here: Path, environ: Mapping[str, str]) -> str:
    return os.path.normpath(_raw(text, here, environ))


def _state_places(found: Mapping[str, str]) -> tuple[str, list[str]]:
    """The case-folded home folder and the state folders (the active root, and the one under the home folder)."""
    home = os.path.normpath(found["HOME"]).casefold() if found.get("HOME") else ""
    return home, [*_roots(found), *([f"{home}/.poolhouse"] if home else [])]


def holds_state(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` is the state folder that holds the person folder, or the folder of the session tokens' node."""
    folder = path.rstrip("/").casefold()
    _, roots = _state_places(environ or os.environ)
    return any(root == folder or f"{root}/node" == folder for root in roots)


def above_state(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` is the home folder, a folder above it, or a state folder: a tree the person folder sits in."""
    folder = path.rstrip("/").casefold()
    home, roots = _state_places(environ or os.environ)
    return any(own == folder or own.startswith(folder + "/") for own in [home, *roots] if own)


def holds_secret(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` is the person folder, a folder it sits in directly, or an ancestor of the home folder."""
    return holds_state(path, environ) or above_state(path, environ)


def _places(text: str, here: Path, environ: Mapping[str, str]) -> list[str]:
    """The place ``text`` names and, when a link is on the way, the place it leads to: a tool follows links, so both are the reach."""
    raw = _raw(text, here, environ)
    if len(text) > MOST_PATH or "\n" in text or "\0" in text:
        return [os.path.normpath(raw)]
    return [os.path.normpath(raw), os.path.realpath(raw)]


def _reaches_secrets(text: str, here: Path, environ: Mapping[str, str]) -> bool:
    return any(secret(place, environ) or holds_secret(place, environ)
               for place in _places(re.split(r"[*?\[{]", text)[0] or ".", here, environ))


def tool_refusal(name: str, given: Mapping[str, object], *, cwd: str = "", environ: Mapping[str, str] | None = None,
                 subagent: bool = False) -> str:
    """Why the tool call ``name`` with input ``given`` is refused: it drives the owner's browser, runs code outside the shell
    rules, stops a task from a subagent, or reaches the person folder or a guard; an empty string when it may run. A tool
    nobody has listed is read by the shape of its input: a string under a key that names a path is a path it reaches. An
    input nested past `MOST_DEPTH` is refused, not read in part."""
    try:
        return _tool_refusal(name, given, Path(cwd or Path.cwd()), environ or os.environ, subagent)
    except (TooComplex, RecursionError) as error:
        return f"the input of {name} {TOO_COMPLEX} ({error})."


def _tool_refusal(name: str, given: Mapping[str, object], here: Path, found: Mapping[str, str], subagent: bool) -> str:
    if too_deep(given):
        raise TooComplex(f"a tool input nested more than {MOST_DEPTH} deep")
    if name.startswith("mcp__claude-in-chrome__"):
        return "the owner's own browser is not driven by an agent: use a headless browser of your own."
    if name == "mcp__ide__executeCode":
        return "code run in the editor's kernel skips the shell rules: use Bash."
    keys = {"Read": ("file_path", "notebook_path"), "Glob": ("pattern", "path"), "Grep": ("path", "glob")}.get(name)
    writes = name.startswith("mcp__") and not READERS.search(name.rsplit("__", 1)[-1])
    if keys is not None:
        texts = [str(given[k]) for k in keys if given.get(k)]
    else:
        texts = _strings(given) if name.startswith("mcp__") else _keyed(given, PATH_KEY)
    for text in texts:
        for place in _places(text, here, found):
            if secret(place, found):
                return f"{text} is in the person folder or is a session token: {NOT_READ}."
            if writes and guarded(place, found):
                return f"{text} is a guard, a hook setting or the authority registry: {REFUSED}."
        if name in ("Glob", "Grep") and _reaches_secrets(text, here, found):
            return f"a search from {text} reaches the person folder: {NOT_READ}."
    return ""
