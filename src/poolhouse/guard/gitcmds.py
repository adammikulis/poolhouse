"""What a git command does that runs a command, reaches a host nobody listed or prints a password.

`findings` reads the arguments after ``git``: settings that run commands, a repository fetched from a new host, a dump of the
settings and an address in the project's own remotes that holds a password. `options` splits ``git -c key=value ... subcommand``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from poolhouse import home
from poolhouse.guard.harm import Finding

__all__ = ["config_text", "findings", "options"]

U, D = "unsure", "destructive"
MAX_CONFIG = 1 << 18
EXEC_KEY = re.compile(
    r"core\.(?:hookspath|fsmonitor|pager|editor|sshcommand|askpass|gitproxy)|alias\..+|credential(?:\..+)?\.helper|diff\.external"
    r"|diff\..+\.(?:command|textconv)|merge\..+\.driver|(?:difftool|mergetool|man|browser)\..+\.cmd|filter\..+\.(?:clean|smudge|process)"
    r"|sequence\.editor|gpg(?:\..+)?\.program|include(?:if\..+)?\.path|url\..+\.insteadof|remote\..+\.(?:uploadpack|receivepack|proxy)"
    r"|(?:uploadpack|receive|pager|protocol)\..+|web\.browser|submodule\..+\.update")
GIT_RUNS = ("--upload-pack", "--receive-pack", "--exec=", "--exec-path", "ext::")
KNOWN_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "sr.ht", "dev.azure.com", "huggingface.co"})
SENSITIVE_KEYS = ("credential.helper", "remote.origin.url", "remote.origin.pushurl", "url.x.insteadof", "http.extraheader", "user.token",
                  "github.token", "core.password", "http.proxy")
PASSWORD_URL = re.compile(r"://[^/\s:@]+:[^/\s@]+@|://[^/\s:@]{20,}@")
SHOWS_REMOTE = re.compile(r"^remote\..*\.(?:push)?url$|^url\..*")
HOST = re.compile(r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/\s]+@)?([A-Za-z0-9.-]+)(?::|/|$)", re.I)


def options(args: list[str]) -> tuple[list[str], list[str]]:
    """The settings given with ``git -c`` and the words from the subcommand on."""
    keys, at = [], 0
    while at < len(args) and args[at].startswith("-"):
        word = args[at]
        if word == "-c" and at + 1 < len(args):
            keys.append(args[at + 1].partition("=")[0].lower())
            at += 1
        elif word.startswith("--config-env="):
            keys.append(word.split("=", 2)[1].lower())
        elif word in ("-C", "--git-dir", "--work-tree", "--namespace"):
            at += 1
        elif word.startswith("--exec-path"):
            keys.append("core.hookspath")
        at += 1
    return keys, args[at:]


def _git_dir(root: Path) -> Path | None:
    dot = root / ".git"
    try:
        if dot.is_dir():
            return dot
        text = dot.read_text(encoding="utf-8", errors="ignore").strip() if dot.is_file() else ""
        if not text.startswith("gitdir:"):
            return None
        where = Path(text.split(":", 1)[1].strip())
        where = where if where.is_absolute() else root / where
        common = where / "commondir"
        return (where / common.read_text(encoding="utf-8", errors="ignore").strip()).resolve() if common.is_file() else where
    except OSError:
        return None


def config_text(roots: Sequence[str]) -> str:
    """The text of the project's own git settings and the person's, empty when neither can be read."""
    files = [home.user_home() / ".gitconfig"]
    if roots and (folder := _git_dir(Path(roots[0]))):
        files.insert(0, folder / "config")
    out = []
    for file in files:
        try:
            if file.stat().st_size <= MAX_CONFIG:
                out.append(file.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(out)


def _host(word: str) -> str:
    if "://" not in word and not re.match(r"^[^@/\s]+@[^:/\s]+:", word):
        return ""
    found = HOST.match(word)
    return found.group(1).lower() if found else ""


def _listed(host: str, roots: Sequence[str]) -> bool:
    configured = {_host(m) for m in re.findall(r"url\s*=\s*(\S+)", config_text(roots))}
    return any(host == known or host.endswith("." + known) for known in KNOWN_HOSTS | configured if known)


def _sensitive(pattern: str) -> bool:
    try:
        rule = re.compile(pattern, re.I)
    except re.error:
        return True
    return any(rule.search(key) for key in SENSITIVE_KEYS)


def _settings(sub: str, more: list[str], words: list[str], roots: Sequence[str]) -> list[Finding]:
    if sub != "config":
        return []
    flags = set(more)
    regexp = next((more[i + 1] for i, a in enumerate(more[:-1]) if a in ("--get-regexp", "--get-urlmatch")), "")
    if flags & {"--list", "-l"} or (flags & {"--get-regexp", "--get-urlmatch"} and _sensitive(regexp or ".")):
        return [Finding(U, "git config prints settings, and some hold a token or a password")]
    shown = [w for w in words if SHOWS_REMOTE.search(w)] if flags & {"--get", "--get-all"} else []
    if shown and PASSWORD_URL.search(config_text(roots)):
        return [Finding(U, "git config prints an address that holds a password")]
    if len(words) >= 2 or "--add" in flags:
        key = words[0].lower() if words else ""
        if EXEC_KEY.fullmatch(key):
            return [Finding(D, f"sets {key}, a command git runs on its own")]
    return []


def _remotes(sub: str, more: list[str], words: list[str], roots: Sequence[str]) -> list[Finding]:
    found: list[Finding] = []
    prints = words[:1] in (["get-url"], ["show"]) or bool({"-v", "--verbose"} & set(more))
    if sub == "remote" and prints and PASSWORD_URL.search(config_text(roots)):
        found.append(Finding(U, "git remote prints an address that holds a password"))
    urls = words[2:] if sub == "remote" and words[:1] in (["add"], ["set-url"]) else words if sub in ("pull", "fetch", "ls-remote") else []
    for host in dict.fromkeys(_host(w) for w in urls):
        if host and not _listed(host, roots):
            found.append(Finding(U, f"git {sub} reaches {host}, a host this project does not list"))
    return found


def findings(args: list[str], roots: Sequence[str] = ()) -> list[Finding]:
    """The findings for the arguments after ``git``."""
    keys, rest = options(args)
    found = [Finding(D, f"sets {k}, a command git runs on its own") for k in keys if EXEC_KEY.fullmatch(k)]
    sub, more = (rest[0], rest[1:]) if rest else ("", [])
    words = [w for w in more if not w.startswith("-")]
    found += _settings(sub, more, words, roots) + _remotes(sub, more, words, roots)
    if sub in ("clone", "submodule") and (sub == "clone" or words[:1] in (["add"], ["update"], ["init"], ["sync"])):
        found.append(Finding(U, "downloads a repository whose hooks and build scripts are not read"))
    if sub == "bisect" and "run" in words[:1]:
        found.append(Finding(U, "runs a command at each step (git bisect run)"))
    if any(a.startswith(GIT_RUNS) for a in more):
        found.append(Finding(D, "names a program git runs to reach the remote"))
    return found
