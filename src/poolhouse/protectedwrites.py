"""The paths an agent's shell may not write (the guards, the hook settings, git's hooks and config, the person record's
code, the authority registry) or touch at all (the person record and the session tokens), and the shell forms that
reach them. Standard library only: the hook scripts load it by file through `worktreerules`."""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from collections.abc import Callable, Mapping
from pathlib import Path

__all__ = ["guarded", "holds_secret", "other_agent", "refusal", "secret", "tool_refusal", "touches_guards",
           "without_heredocs"]

GUARD_PATHS = (
    r"src/poolhouse/workspace/person_\w+\.py",
    r"scripts/hooks/(claude-user-prompt|person-consume|claude-bash-guard|claude-edit-guard|claude-tool-guard|pre-push"
    r"|rules_loader\.py)",
    r"\.claude/(settings(\.local)?\.json|(agents|commands|skills|hooks)(/.*)?)",
    r"\.git/(hooks(/.*)?|config(\.worktree)?|worktrees/[^/]+/config\.worktree)",
    r"\.gitconfig", r"\.config/git/(config|hooks(/.*)?)", r"\.poolhouse/(authority\.json|authority-audit\.jsonl)")
REVIEWED = (*GUARD_PATHS, r"scripts/hooks/(pre-commit|commit-msg|pushed|primary-only|no-real-names|no-data-files|budgets"
            r"|budgets-only-fall|weakened-assertions|guard_index\.py)", r"AGENTS\.md", r"CLAUDE\.md", r"\.github/.*",
            r"docs/github-protection\.md", r"scripts/github-protection[\w-]*", r"scripts/land[\w.-]*",
            r"src/poolhouse/(protectedwrites|worktreerules)\.py")
GUARDS = re.compile(r"(^|/)(" + "|".join(GUARD_PATHS) + ")$")
REVIEW = re.compile(r"(^|/)(" + "|".join(REVIEWED) + ")$")
SECRETS = re.compile(r"(^|/)\.poolhouse/(person|node/client|node/person)(/|$)")
HELD = tuple(tuple(path.split("/")) for path in (
    "src/poolhouse/workspace/person_store.py", "scripts/hooks/pre-push", "scripts/hooks/claude-bash-guard",
    "scripts/hooks/claude-edit-guard", "scripts/hooks/person-consume", "scripts/hooks/claude-user-prompt",
    "scripts/hooks/rules_loader.py", ".claude/settings.json", ".claude/settings.local.json", ".git/hooks/pre-push",
    ".git/config", ".config/git/config", ".gitconfig"))
MENTION = re.compile(
    r"claude-(bash|edit)-guard|claude-user-prompt|person-consume|rules_loader\.py|scripts/hooks/pre-push|\.git/(hooks|config)"
    r"|workspace/person_\w+|\.claude/(settings|agents|commands|skills|hooks)|core\.hooksPath|\.gitconfig"
    r"|\.poolhouse/(person|node/client|node/person|authority)")

WRAPPERS = frozenset({"sudo", "doas", "command", "exec", "nohup", "time", "nice", "ionice", "env", "setsid", "caffeinate",
                      "stdbuf", "unbuffer", "builtin", "timeout", "xargs"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
INLINE = {"python": "-c", "python3": "-c", "node": "-e", "nodejs": "-e", "bun": "-e", "ruby": "-e", "perl": "-e", "php": "-r",
          "lua": "-e", "osascript": "-e"}
EDITORS = frozenset({"vi", "vim", "nvim", "nano", "emacs", "ed", "ex", "patch", "sponge", "tee"})
MODIFIERS = frozenset({"rm", "unlink", "rmdir", "truncate", "touch", "chmod", "chown", "chattr", "setfacl", "xattr", "shred",
                       "mv"})
IN_PLACE = frozenset({"sed", "awk", "gawk", "perl"})
COPIERS = frozenset({"cp", "install", "ln", "rsync", "cpio", "dd"})
FETCHERS = {"curl": ("-o", "--output"), "wget": ("-O", "--output-document")}
GIT_CONFIG = re.compile(r"^(core\.(hookspath|fsmonitor|sshcommand|askpass)|credential\..*helper|include(if\..*)?\.path)$", re.I)
GIT_ENV = re.compile(r"^GIT_CONFIG_(COUNT|KEY_\d+|VALUE_\d+|GLOBAL|SYSTEM|PARAMETERS)$")
GIT_PATHS = frozenset({"checkout", "restore", "rm", "mv", "update-index", "clean"})
GIT_TAKES = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
WRITES_TO_A_FILE = re.compile(r"^(&?>>?|>\||&>>?)$")
REDIRECTS = re.compile(r"[<>]&?|<<-?|<<<|>&")
SPLIT = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "(", ")"})
MUTATING = frozenset({"PUT", "POST", "PATCH", "DELETE"})
API_VALUES = frozenset({"-X", "--method", "-H", "--header", "-f", "-F", "--field", "--raw-field", "--input", "-q", "--jq",
                        "-t", "--template", "--hostname", "--cache", "-p", "--preview"})
API_BODY = ("-f", "-F", "--field", "--raw-field", "--input")
ADMIN = re.compile(r"rulesets?|protection|secrets|variables|environments|collaborators|hooks|keys|invitations|merges"
                   r"|git/refs|releases|actions|dispatches|workflows|teams|memberships|transfer|rename|branches|rules")
GRAPHQL = re.compile(r"BranchProtectionRule|Ruleset|mergePullRequest|enablePullRequestAutoMerge|updateRepository"
                     r"|deleteRepository|updateRef|createRef|deleteRef|Secret", re.I)
GH_BLOCKED: dict[str, frozenset[str] | None] = {
    "secret": None, "variable": None, "ruleset": None, "extension": None,
    "repo": frozenset({"edit", "delete", "rename", "archive", "transfer"}),
    "workflow": frozenset({"run", "enable", "disable"}), "release": frozenset({"create", "delete", "edit", "upload"}),
    "auth": frozenset({"login", "logout", "refresh", "setup-git", "token", "switch"}),
    "alias": frozenset({"set", "import"}), "config": frozenset({"set"})}
READERS = re.compile(r"read|get|list|search|view|find|stat|head|tail|cat|show|describe|query|fetch")
DEPTH = 3
SCRIPT_BYTES = 65536
PERSON = "the guards, the hook settings, git's hooks and the person record are changed by a person at a terminal"


def _roots(environ: Mapping[str, str]) -> list[str]:
    named = environ.get("POOLHOUSE_HOME")
    return [os.path.normpath(named)] if named else []


def secret(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` lies in the person record or the session tokens."""
    if SECRETS.search(path):
        return True
    places = ("/person", "/node/client", "/node/person")
    return any(path == root + p or path.startswith(root + p + "/") for root in _roots(environ or os.environ) for p in places)


def _holds_a_guard(path: str) -> bool:
    """Whether ``path`` is a folder that holds one of the guard files, as the repository root or as `scripts/hooks`."""
    folder = path.rstrip("/")
    for held in HELD:
        for i in range(len(held)):
            head = "/".join(held[:i])
            inside = not head or folder == head or folder.endswith("/" + head)
            if inside and os.path.lexists(f"{folder}/{'/'.join(held[i:])}"):
                return True
    return False


def guarded(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether a write to ``path`` (a file, or a folder holding one) is a person's: the guards, settings, git's hooks."""
    if GUARDS.search(path) or _holds_a_guard(path):
        return True
    return any(path in (f"{root}/authority.json", f"{root}/authority-audit.jsonl") for root in _roots(environ or os.environ))


def touches_guards(paths: list[str]) -> list[str]:
    """The repository-relative ``paths`` a person reviews before they land: the guards, the settings, the rules."""
    return [p for p in paths if REVIEW.search(p)]


def _flatten(command: str) -> str:
    """The command with each newline outside quotes turned into a `;`."""
    out: list[str] = []
    quote = ""
    for char in command:
        if char == "\n" and not quote:
            char = ";"
        elif quote and char == quote:
            quote = ""
        elif not quote and char in "'\"":
            quote = char
        out.append(char)
    return "".join(out)


def _subshell(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        if depth == 0:
            return i
    return len(text) - 1


def _substitutions(text: str) -> tuple[str, list[str]]:
    """``text`` with each `$(...)`, `<(...)`, `>(...)` and backtick body replaced by a word, and the bodies."""
    bodies: list[str] = []
    while found := re.search(r"[$<>]\(", text):
        end = _subshell(text, found.end() - 1)
        bodies.append(text[found.end():end])
        text = text[:found.start()] + "SUB" + text[end + 1:]
    ticks = text.split("`")
    if len(ticks) > 2:
        bodies += ticks[1::2]
        text = "SUB".join(ticks[0::2])
    return text, bodies


def _heredocs(command: str) -> tuple[str, list[str]]:
    """``command`` without heredoc bodies, and the bodies."""
    kept: list[str] = []
    bodies: list[str] = []
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        kept.append(lines[i])
        opened = re.search(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?", lines[i])
        if opened:
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != opened.group(1):
                body.append(lines[i])
                i += 1
            bodies.append("\n".join(body))
        i += 1
    return "\n".join(kept), bodies


def _words(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return text.split()


def _commands(words: list[str]) -> list[list[str]]:
    out: list[list[str]] = [[]]
    for word in words:
        if word in SPLIT:
            out.append([])
        else:
            out[-1].append(word)
    return [one for one in out if one]


def _split_redirects(words: list[str]) -> tuple[list[str], list[str]]:
    """``words`` without redirections, and the files written by `>`, `>>`, `>|` and `&>`."""
    kept: list[str] = []
    written: list[str] = []
    i = 0
    while i < len(words):
        follows = i + 1 < len(words)
        if follows and WRITES_TO_A_FILE.match(words[i]):
            written.append(words[i + 1])
            i += 2
        elif follows and REDIRECTS.fullmatch(words[i]):
            i += 2
        else:
            kept.append(words[i])
            i += 1
    return kept, written


def _braces(word: str) -> list[str]:
    found = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not found:
        return [word]
    return [x for part in found.group(1).split(",") for x in _braces(word[:found.start()] + part + word[found.end():])]


def _script_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:SCRIPT_BYTES] if path.is_file() else ""
    except OSError:
        return ""


class Shell:
    """What the command line being read knows: the working folder, the variables set so far and what it may do."""

    def __init__(self, here: Path, environ: Mapping[str, str], released: Callable[[str], bool]) -> None:
        self.here, self.environ, self.released = here, environ, released
        self.variables: dict[str, str] = {}

    def move(self, folder: str) -> None:
        """Change the working folder as `cd` does."""
        self.here = Path(os.path.normpath(self.here / self.home(folder)))

    def home(self, word: str) -> str:
        """``word`` with a leading `~` and the variables set so far expanded."""
        known = {**self.environ, **self.variables}
        word = re.sub(r"\$\{?(\w+)\}?", lambda m: known.get(m.group(1), m.group(0)), word)
        return known.get("HOME", "~") + word[1:] if word == "~" or word.startswith("~/") else word

    def paths(self, word: str) -> list[str]:
        """The absolute paths ``word`` names: variables, `~`, braces and globs expanded, links resolved when present."""
        out: list[str] = []
        for one in _braces(self.home(word)):
            joined = os.path.normpath(self.here / one)
            out.append(joined)
            if re.search(r"[*?\[]", one):
                out += self.matches(joined)
            elif os.path.lexists(joined):
                out.append(os.path.realpath(joined))
        return out

    def matches(self, pattern: str) -> list[str]:
        """The existing paths a glob ``pattern`` matches, and the guard files it would match if they existed."""
        anchor = Path(pattern).anchor
        found = [str(p) for p in Path(anchor).glob(pattern[len(anchor):])] if anchor else []
        roots = [self.here, *self.here.parents]
        guards = [os.path.normpath(root / "/".join(held)) for root in roots for held in HELD]
        return found + [g for g in guards if fnmatch.fnmatchcase(g, pattern)]

    def hits(self, words: list[str], test: Callable[[str], bool]) -> str:
        """The first of ``words`` that names a path for which ``test`` holds, else an empty string."""
        return next((w for w in words if any(test(path) for path in self.paths(w))), "")

    def guarded(self, words: list[str]) -> str:
        """The first of ``words`` that names a guard or a folder holding one."""
        return self.hits(words, lambda p: guarded(p, self.environ))


def _targeted(args: list[str]) -> list[str]:
    plain = [a for a in args if not a.startswith("-")]
    named = [a.split("=", 1)[1] for a in args if a.startswith("--target-directory=")]
    named += [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-t"]
    return [f"{d}/{Path(src).name}" for d in named for src in plain if src != d] + named


def _destinations(verb: str, args: list[str]) -> list[str]:
    if verb == "dd":
        return [a[3:] for a in args if a.startswith("of=")]
    plain = [a for a in args if not a.startswith("-")]
    return _targeted(args) or plain[-1:]


def _in_place(args: list[str]) -> bool:
    return any(re.match(r"^-[a-zA-Z]*i", a) or a in ("--in-place", "inplace") for a in args)


def _fetched(verb: str, args: list[str]) -> list[str]:
    flags = FETCHERS.get(verb, ())
    named = [args[i + 1] for i, a in enumerate(args[:-1]) if a in flags]
    return named + [a.split("=", 1)[1] for a in args if a.startswith("--output=")]


def _writes(verb: str, args: list[str], shell: Shell) -> str:
    plain = [a for a in args if not a.startswith("-")]
    if verb in COPIERS:
        targets = _destinations(verb, args)
    elif verb in MODIFIERS or verb in EDITORS:
        targets = plain
    elif verb in IN_PLACE:
        targets = plain if _in_place(args) else []
    elif verb == "find":
        targets = plain[:1] if any(a in ("-delete", "-exec", "-execdir") for a in args) else []
    else:
        targets = _fetched(verb, args)
    found = shell.guarded(targets)
    return f"`{verb}` would change {found}: {PERSON}." if found else ""


def _shell_code(args: list[str]) -> str:
    """The string a shell is asked to run with `-c` (or a bundled flag such as `-lc`), else an empty string."""
    for i, a in enumerate(args[:-1]):
        if re.fullmatch(r"-[a-z]*c", a):
            return args[i + 1]
    return ""


def _code(verb: str, args: list[str], bodies: list[str], shell: Shell) -> str:
    """Why interpreted code (inline, a heredoc or a script file) is refused: it names a guard, a secret or git's hooks."""
    if verb not in INLINE and verb not in SHELLS and verb not in ("source", "."):
        return ""
    flag = INLINE.get(verb, "")
    given = args[args.index(flag) + 1:][:1] if flag in args else []
    texts = [*given, *bodies]
    scripts = [a for a in args if not a.startswith("-")]
    if scripts:
        texts.append(_script_text(shell.here / scripts[0]))
    found = next((m.group(0) for t in texts for m in [MENTION.search(t)] if m), "")
    return f"code run from a shell names {found}: {PERSON}; use the file tools for anything else." if found else ""


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
    verbs = [a for a in args if not a.startswith("-")]
    head, rest = (verbs[0] if verbs else ""), verbs[1:2]
    if head == "pr" and rest == ["merge"] and not shell.released(str(shell.here)):
        return "`gh pr merge` merges into a protected branch: it needs the person's release approval (scripts/hooks/person-consume propose)."
    blocked = GH_BLOCKED.get(head, frozenset({"-"}))
    if blocked is None or (rest and rest[0] in blocked):
        return f"`gh {head}` changes how the repository is protected or who may act on it: a person runs it."
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
        return "a GraphQL change to protection is a person's." if GRAPHQL.search(" ".join(args)) else ""
    writes = method in MUTATING or (body and not method)
    if writes and (ADMIN.search(endpoint) or re.fullmatch(r"repos/[^/]+/[^/]+/?", endpoint)):
        return "`gh api` writing to protection, secrets, releases or settings is a person's."
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
        return "`git -c` that changes where hooks run or what git runs is refused: the hooks are the last checks."
    sub, rest, _ = _git_sub(args)
    key = next((a for a in rest if not a.startswith("-")), "")
    reading = any(a in ("--get", "--get-all", "--get-regexp", "-l", "--list", "--show-origin") for a in rest)
    removing = any(a.startswith("--unset") for a in rest)
    setting = len([a for a in rest if not a.startswith("-")]) > 1
    if sub == "config" and GIT_CONFIG.match(key) and not reading and (setting or removing):
        return f"`git config {key}` changes where hooks run or what git runs: a person sets it."
    return ""


def _git_paths(args: list[str], shell: Shell) -> str:
    sub, rest, moved = _git_sub(args)
    if sub not in GIT_PATHS:
        return ""
    plain = [a for a in rest if not a.startswith("-")]
    named = rest[rest.index("--") + 1:] if "--" in rest else (plain if sub in ("rm", "mv", "restore") else [])
    here = shell.here
    if moved:
        shell.move(moved)
    try:
        found = shell.guarded(named)
    finally:
        shell.here = here
    return f"`git {sub}` would change {found}: {PERSON}." if found else ""


def _fetch(words: list[str], shell: Shell) -> str:
    text = " ".join(words)
    if "api.github.com" in text and re.search(r"-X\s*(PUT|POST|PATCH|DELETE)|--request\s+(PUT|POST|PATCH|DELETE)|\s-d\b|--data", text):
        return "writing to the GitHub API by hand skips the low-right identity the agents hold: a person does it."
    return _writes(words[0].rsplit("/", 1)[-1], words[1:], shell)


class Reader:
    """Reads one command line; `command` is the first reason a command in it is refused."""

    def __init__(self, shell: Shell) -> None:
        self.shell = shell

    def command(self, text: str, depth: int = 0) -> str:
        """Why ``text``, a command line, may not run; an empty string when it may."""
        text, bodies = _heredocs(text)
        flat, subs = _substitutions(_flatten(text))
        for inner in subs:
            if depth < DEPTH and (why := self.command(inner, depth + 1)):
                return why
        for words in _commands(_words(flat)):
            if why := self.simple(words, bodies, depth):
                return why
        return ""

    def simple(self, words: list[str], bodies: list[str], depth: int) -> str:
        """Why one command may not run: a redirection into a guard, a secret named, or a verb that writes one."""
        shell = self.shell
        words, written = _split_redirects(words)
        if blocked := shell.hits(written, lambda p: guarded(p, shell.environ) or secret(p, shell.environ)):
            return f"a redirection would write {blocked}: {PERSON}."
        rest, assigned = _first_words(words)
        if not rest:
            shell.variables.update(assigned)
            return ""
        if any(GIT_ENV.match(name) for name in assigned):
            return "GIT_CONFIG_* changes where hooks run: the hooks are the last checks."
        mentioned = shell.hits([a for a in rest if "=" not in a or "/" in a], lambda p: secret(p, shell.environ))
        if mentioned and not self._message(rest, mentioned):
            return f"{mentioned} is the person record or a session token: an agent does not read or list it."
        return self.verb(rest[0].rsplit("/", 1)[-1], rest, bodies, depth)

    @staticmethod
    def _message(rest: list[str], word: str) -> bool:
        return any(a in ("-m", "--message") and rest[i + 1:i + 2] == [word] for i, a in enumerate(rest))

    def verb(self, verb: str, rest: list[str], bodies: list[str], depth: int) -> str:
        """Why the command ``rest`` (its first word is ``verb``'s path) may not run."""
        shell, args = self.shell, rest[1:]
        if verb == "cd" and args:
            shell.move(args[0])
            return ""
        if verb in SHELLS and (inner := _shell_code(args)) and depth < DEPTH:
            return self.command(inner, depth + 1) or _code(verb, args, bodies, shell)
        if verb == "eval" and depth < DEPTH:
            return self.command(" ".join(args), depth + 1)
        if verb == "gh":
            return _gh(args, shell)
        if verb == "git":
            return _git_config(args) or _git_paths(args, shell)
        if verb in FETCHERS or verb in ("http", "https", "xh"):
            return _fetch(rest, shell)
        script = _script_text(shell.here / rest[0]) if "/" in rest[0] else ""
        return _writes(verb, args, shell) or _code(verb, args, bodies, shell) or (
            f"the script {rest[0]} names {m.group(0)}: {PERSON}." if (m := MENTION.search(script)) else "")


def refusal(command: str, cwd: str, released: Callable[[str], bool], environ: Mapping[str, str] | None = None) -> str:
    """Why the shell ``command``, run from ``cwd``, may not run; an empty string when it may."""
    return Reader(Shell(Path(cwd or Path.cwd()), environ or os.environ, released)).command(command)


def without_heredocs(command: str) -> str:
    """``command`` without its heredoc bodies, which are data being written or code handed to an interpreter."""
    return _heredocs(command)[0]


def other_agent(command: str, own: str) -> str:
    """Why a subagent named ``own`` may not run ``command``: it names another session by `--agent`, a token file, a token or
    `POOLHOUSE_WORKSPACE_AGENT`; an empty string when it does not."""
    flat, subs = _substitutions(_flatten(_heredocs(command)[0]))
    for inner in subs:
        if why := other_agent(inner, own):
            return why
    for words in _commands(_words(flat)):
        for i, word in enumerate(words):
            if why := _names_another(word, words[i + 1:i + 2], own):
                return why
        if (inner := _shell_code(words[1:])) and (why := other_agent(inner, own)):
            return why
    return ""


def _names_another(word: str, after: list[str], own: str) -> str:
    name, _, value = word.partition("=")
    if name in ("--agent", "POOLHOUSE_WORKSPACE_AGENT") and (value or after) and (value or after[0]) != own:
        return f"you are {own}: run as yourself, not as {value or after[0]}."
    if name in ("--token-file", "POOLHOUSE_WORKSPACE_TOKEN"):
        return f"you are {own}: another session's token is not yours to use."
    return ""


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def _place(text: str, here: Path, environ: Mapping[str, str]) -> str:
    home = environ.get("HOME", "~")
    return os.path.normpath(here / (home + text[1:] if text == "~" or text.startswith("~/") else text))


def holds_secret(path: str, environ: Mapping[str, str] | None = None) -> bool:
    """Whether ``path`` is the person record's folder, a folder it sits in directly, or an ancestor of the home folder."""
    folder, found = path.rstrip("/"), environ or os.environ
    home = os.path.normpath(found["HOME"]) if found.get("HOME") else ""
    roots = [*_roots(found), *([f"{home}/.poolhouse"] if home else [])]
    if any(root == folder or f"{root}/node" == folder for root in roots):
        return True
    return any(own == folder or own.startswith(folder + "/") for own in [home, *roots] if own)


def _reaches_secrets(text: str, here: Path, environ: Mapping[str, str]) -> bool:
    place = _place(re.split(r"[*?\[{]", text)[0] or ".", here, environ)
    return secret(place, environ) or holds_secret(place, environ)


def tool_refusal(name: str, given: Mapping[str, object], *, cwd: str = "", environ: Mapping[str, str] | None = None,
                 subagent: bool = False) -> str:
    """Why the tool call ``name`` with input ``given`` is refused: it drives the owner's browser, runs code outside the shell
    rules, stops a task from a subagent, or reaches the person record or a guard; an empty string when it may run."""
    found, here = environ or os.environ, Path(cwd or Path.cwd())
    if name.startswith("mcp__claude-in-chrome__"):
        return "the owner's own browser is not driven by an agent: use a headless browser of your own."
    if name == "mcp__ide__executeCode":
        return "code run in the editor's kernel skips the shell rules: use Bash."
    if name == "TaskStop":
        return "only the main session stops a task." if subagent else ""
    keys = {"Read": ("file_path", "notebook_path"), "Glob": ("pattern", "path"), "Grep": ("path", "glob")}.get(name)
    if keys is None and not name.startswith("mcp__"):
        return ""
    texts = _strings(given) if keys is None else [str(given[k]) for k in keys if given.get(k)]
    for text in texts:
        place = _place(text, here, found)
        writes = name.startswith("mcp__") and not READERS.search(name.rsplit("__", 1)[-1])
        if secret(place, found) or (writes and guarded(place, found)):
            return f"{text} is the person record, a session token or a guard: " + PERSON + "."
        if name in ("Glob", "Grep") and _reaches_secrets(text, here, found):
            return f"a search from {text} reaches the person record: search a folder of the project."
    return ""
