"""The deterministic blocks of a delegate that runs without a sandbox, asked before every tool call and judged from the call alone.

Refused: the person folder, the session tokens, the authority registry and the guards (`poolhouse.protectedwrites`); a force push and a remote
deletion; a read of a credential store; a write outside the project, any write to the user's global configuration and a recursive deletion outside
the project. These are not a boundary: a program the model writes, or one it runs, reaches whatever the account reaches, and only a sandbox stops that.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from poolhouse import protectedwrites, secretpaths, worktreerules
from poolhouse.guard import shellscan

__all__ = ["Ground", "refusal"]


@dataclass(frozen=True, slots=True)
class Ground:
    """The ``root`` a delegate works in, the user's ``home`` and the ``state`` root the blocks protect, and the delegate's own ``scratch`` folder."""

    root: str
    home: str
    state: str
    scratch: str = ""


READS = {"Read": ("file_path", "path"), "Glob": ("path", "pattern"), "Grep": ("path", "glob"), "LS": ("path",)}
WRITES = ("Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch", "patch")
SHELL = ("Bash", "PowerShell")
LEAD = ("mcp__lead__ask", "mcp__lead__note")
NOWHERE = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "nul"})
OUTSIDE = "writes outside the project folder"
GLOBAL = "changes the user's global configuration, or installs for the whole computer"
DELETES = "deletes a folder tree outside the project"
UNREAD = "names its target through a variable, a substitution or another command, so where it writes cannot be told"
CREDENTIAL = "reads a credential store"
FORCE = "force-pushes or deletes something on a remote"
LAUNCHERS = frozenset({"env", "nohup", "time", "nice", "ionice", "timeout", "exec", "command", "builtin", "stdbuf", "setsid", "caffeinate", "unbuffer", "xargs"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
AS_ANOTHER = frozenset({"sudo", "doas", "su", "pkexec", "runas"})
EVERY = frozenset({"rm", "rmdir", "unlink", "shred", "mkdir", "touch", "truncate", "tee", "del", "erase", "rd", "remove-item"})
"""Programs every path argument of which is written or removed."""
OWNED = frozenset({"chmod", "chown", "chgrp"})
COPIES = frozenset({"cp", "install", "ln", "rsync", "mv"})
GIT_WRITES = worktreerules.CHANGES_THE_TREE | {"init", "clone", "rm", "mv", "clean", "config", "worktree", "branch", "tag"}
ALWAYS_GLOBAL: dict[str, tuple[str, ...]] = {
    "brew": ("install", "uninstall", "reinstall", "upgrade", "update", "tap", "untap", "link", "unlink", "services", "remove"),
    "apt": ("install", "remove", "purge", "upgrade", "update", "autoremove"), "apt-get": ("install", "remove", "purge", "upgrade", "update", "autoremove"),
    "dnf": ("install", "remove", "upgrade", "update"), "yum": ("install", "remove", "upgrade", "update"), "apk": ("add", "del", "upgrade"),
    "pacman": ("-s", "-r", "-u", "-sy", "-syu"), "zypper": ("install", "remove", "update"), "snap": ("install", "remove"), "port": ("install", "uninstall"),
    "winget": ("install", "uninstall", "upgrade"), "choco": ("install", "uninstall", "upgrade"), "scoop": ("install", "uninstall", "update"),
    "pipx": ("install", "uninstall", "inject", "upgrade", "ensurepath", "reinstall"), "gem": ("install", "uninstall", "update"),
    "cargo": ("install", "uninstall", "login"), "go": ("install",), "defaults": ("write", "delete", "import", "rename"),
    "launchctl": ("load", "unload", "bootstrap", "bootout", "enable", "disable", "submit", "setenv", "kickstart", "remove"),
    "systemctl": ("enable", "disable", "start", "stop", "restart", "mask", "link", "set-environment", "edit"), "reg": ("add", "delete", "import"),
}
"""Programs and the subcommands of each that change the computer or the user's settings wherever they run."""
ANY_USE_IS_GLOBAL = frozenset({"setx", "at", "batch", "networksetup", "scutil", "systemsetup", "visudo", "dscl"})
CREDENTIAL_COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "security": (("find-generic-password",), ("find-internet-password",), ("dump-keychain",), ("export",), ("find-key",)),
    "secret-tool": (("lookup",), ("search",)), "pass": (("show",),), "op": (("read",), ("item", "get")), "cmdkey": (("/list",),),
    "gh": (("auth", "token"), ("auth", "status", "--show-token")),
    "aws": (("configure", "get"), ("configure", "export-credentials")),
    "gcloud": (("auth", "print-access-token"), ("auth", "print-identity-token"), ("auth", "application-default")),
    "az": (("account", "get-access-token"),),
}
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
DEPTH = 3


def refusal(name: str, args: Mapping[str, Any], ground: Ground) -> str:
    """Why the tool call ``name`` with ``args`` is refused, empty when no block applies; a tool a delegate is not given is refused."""
    if name in LEAD:
        return ""
    environ = {"HOME": ground.home, "POOLHOUSE_HOME": ground.state}
    if name in SHELL:
        line = str(args.get("command") or "")
        return protectedwrites.refusal(line, ground.root, lambda _cwd: "an unattended delegate never publishes", environ) or _line(line, ground, ground.root, 0)
    if name not in READS and name not in WRITES:
        return f"{name} is not one of a delegate's tools"
    if why := protectedwrites.tool_refusal(name, args, cwd=ground.root, environ=environ):
        return why
    if name in READS:
        return next((f"{text} {CREDENTIAL}" for key in READS[name] if (text := str(args.get(key) or "")) and _secret(text, ground.root, ground)), "")
    paths = [str(args[key]) for key in ("file_path", "path", "filePath", "notebook_path") if args.get(key)]
    for value in args.values():
        if isinstance(value, str) and "*** Begin Patch" in value:
            paths += worktreerules.patch_paths(value)
    if not paths:
        return f"{name} names no file the blocks can check"
    for text in paths:
        place = _place(text, ground.root, ground)
        if place is None:
            return f"{text} {UNREAD}"
        if protectedwrites.guarded(place, environ) or protectedwrites.secret(place, environ):
            return f"{text} is a guard, a hook setting, the person folder or the authority registry"
        if not _inside(place, ground):
            return f"{text} {OUTSIDE}"
    return ""


def _expanded(text: str, ground: Ground) -> str:
    """``text`` with the user's home written out for a leading ``~``, ``$HOME`` or ``${HOME}``."""
    return re.sub(r"^(~(?=/|$)|\$HOME(?=/|$)|\$\{HOME\}(?=/|$))", lambda _: ground.home, text)


def _place(text: str, here: str | None, ground: Ground) -> str | None:
    """Where ``text`` leads from ``here``, links followed; None when it cannot be told."""
    named = _expanded(text, ground)
    if not named or shellscan.has_expansion(named) or "\0" in named or len(named) > 4096 or named.startswith("~"):
        return None
    if here is None and not PurePath(named).is_absolute():
        return None
    return os.path.realpath(PurePath(here or ground.root) / named)


def _under(place: str, folder: str) -> bool:
    return bool(folder) and PurePath(place).is_relative_to(os.path.realpath(folder))


def _inside(place: str, ground: Ground) -> bool:
    return _under(place, ground.root) or _under(place, ground.scratch)


def _secret(text: str, here: str | None, ground: Ground) -> bool:
    named = _expanded(text, ground)
    base = here or ground.root
    if secretpaths.path_hit(named, base):
        return True
    return shellscan.has_glob(named) and secretpaths.glob_hit(named, base) is not None


def _line(line: str, ground: Ground, here: str | None, depth: int) -> str:
    """Why the shell line ``line``, run from ``here`` (None once a `cd` cannot be told), is refused."""
    if depth > DEPTH:
        return "nests commands too deeply for the blocks to read"
    text = protectedwrites.without_heredocs(line)
    for found in SUBSTITUTION.finditer(text):
        if why := _line(found.group(1) or found.group(2) or "", ground, here, depth + 1):
            return why
    found_segments, problems = shellscan.segments(SUBSTITUTION.sub(" $SUBSTITUTED ", text))
    if text.strip() and not found_segments:
        return f"cannot be read by the blocks: {problems[0].reason if problems else 'no command in it'}"
    for segment in found_segments:
        for operator, target in segment.redirects:
            reads = "<" in operator and ">" not in operator
            if reads and _secret(target, here, ground):
                return f"{target} {CREDENTIAL}"
            writes = not reads and target not in NOWHERE and not target.lstrip("&").isdigit() and target != "-"
            if writes and (why := _written(target, here, ground)):
                return why
        words = list(segment.argv)
        while words and re.match(r"\w+=", words[0]):
            words.pop(0)
        if not words:
            continue
        if _program(words[0]) in ("cd", "pushd"):
            here = _place(next((w for w in words[1:] if not w.startswith("-")), ground.home), here, ground)
            continue
        if why := _command(words, ground, here, depth):
            return why
    return ""


def _program(word: str) -> str:
    return PurePath(word.replace("\\", "/")).name.lower().removesuffix(".exe")


def _written(target: str, here: str | None, ground: Ground, why: str = OUTSIDE) -> str:
    """Why ``target`` may not be written: it cannot be told, or it is outside the project."""
    if target.lower() in NOWHERE:
        return ""
    fixed = re.split(r"[*?\[{]", target)[0] if shellscan.has_glob(target) else target
    place = _place(fixed or ".", here, ground)
    if place is None:
        return f"{target} {UNREAD}"
    return "" if _inside(place, ground) else f"{target} {why}"


def _command(words: list[str], ground: Ground, here: str | None, depth: int) -> str:
    """Why one command is refused."""
    name, rest, fed = _program(words[0]), words[1:], False
    while name in LAUNCHERS:
        fed = fed or name == "xargs"
        while rest and (rest[0].startswith("-") or re.match(r"(\w+=|\d+(\.\d+)?[smhd]?$)", rest[0])):
            rest = rest[1:]
        if not rest:
            return ""
        words, name, rest = rest, _program(rest[0]), rest[1:]
    if name in AS_ANOTHER:
        return f"{name} {GLOBAL}"
    if name in SHELLS or name == "eval":
        inline = " ".join(rest) if name == "eval" else next((rest[i + 1] for i, w in enumerate(rest[:-1]) if re.fullmatch(r"-[a-z]*c", w)), "")
        return _line(inline, ground, here, depth + 1) if inline else ""
    plain = [w for w in rest if not w.startswith("-")]
    lowered = [w.lower() for w in plain]
    for path in (part for w in words for part in (w, w.split("=", 1)[-1], w.lstrip("@")) if part):
        if ("/" in path or path.startswith((".", "~", "$HOME"))) and _secret(path, here, ground):
            return f"{path} {CREDENTIAL}"
    if any(all(each in [w.lower() for w in rest] for each in form) for form in CREDENTIAL_COMMANDS.get(name, ())):
        return f"{name} {CREDENTIAL}"
    if why := _global((words[0], *rest), lowered, here, ground):
        return why
    if name == "git":
        return _git(rest, ground, here)
    if name == "find":
        return _find(rest, ground, here)
    targets = _targets(name, rest, plain)
    if fed and (targets or name in EVERY | OWNED | COPIES):
        return f"{name} takes its targets from another command: it {UNREAD}"
    recursive = any(w == "--recursive" or re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", w) for w in rest) or (name in ("rd", "rmdir") and "/s" in lowered)
    for target in targets:
        if name in ("rm", "rd", "rmdir", "del", "remove-item") and recursive:
            place = _place(re.split(r"[*?\[{]", target)[0] or ".", here, ground)
            if place is None:
                return f"{target} {UNREAD}"
            if not _inside(place, ground) or place == os.path.realpath(ground.root):
                return f"{target} {DELETES}"
        elif why := _written(target, here, ground):
            return why
    return ""


def _targets(name: str, rest: list[str], plain: list[str]) -> list[str]:
    """The paths the command ``name`` writes or removes, read from its arguments."""
    if name in EVERY:
        return [w for w in plain if not w.startswith("/s") or len(w) > 2]
    if name in OWNED:
        return plain[1:]
    if name in COPIES:
        into = next((rest[i + 1] for i, w in enumerate(rest[:-1]) if w in ("-t", "--target-directory")), "")
        return [*plain, into] if name == "mv" and into else plain if name == "mv" else [into or plain[-1]] if plain else []
    if name == "dd":
        return [w[3:] for w in rest if w.startswith("of=")]
    if name in ("sed", "perl") and any(w.startswith(("-i", "-pi", "--in-place")) for w in rest):
        return plain[1:] if not any(w in ("-e", "-f") for w in rest) else plain
    if name in ("curl", "wget"):
        flags = ("-o", "--output") if name == "curl" else ("-O", "--output-document", "-P", "--directory-prefix")
        return [rest[i + 1] for i, w in enumerate(rest[:-1]) if w in flags and rest[i + 1] != "-"]
    if name == "tar":
        named = [rest[i + 1] for i, w in enumerate(rest[:-1]) if w in ("-C", "--directory")]
        packs = any("c" in w for w in rest[:1]) or any(re.fullmatch(r"-[a-z]*c[a-z]*", w) for w in rest)
        return named + ([rest[i + 1] for i, w in enumerate(rest[:-1]) if re.fullmatch(r"-?[a-z]*f", w)] if packs else [])
    if name in ("unzip", "7z"):
        return [rest[i + 1] for i, w in enumerate(rest[:-1]) if w == "-d"] + [w[2:] for w in rest if w.startswith("-o") and name == "7z"]
    return []


def _global(words: tuple[str, ...], plain: list[str], here: str | None, ground: Ground) -> str:
    """Why the command ``words`` changes the user's global configuration or the computer; ``plain`` are its arguments that are no options, lower case."""
    program, rest = words[0], list(words[1:])
    name = _program(program)
    refused = f"{name} {GLOBAL}"
    if name in ANY_USE_IS_GLOBAL or (name == "crontab" and "-l" not in rest):
        return refused
    if (plain and plain[0] in ALWAYS_GLOBAL.get(name, ())) or any(w.lower() in ALWAYS_GLOBAL.get(name, ()) for w in rest if name == "pacman"):
        return refused
    if name == "systemctl" and any(w in ALWAYS_GLOBAL[name] for w in plain):
        return refused
    if name in ("npm", "pnpm", "yarn", "bun"):
        everywhere = any(w in ("-g", "--global", "--location=global") for w in rest) or plain[:1] == ["global"]
        if everywhere and any(w in ("install", "i", "add", "remove", "rm", "uninstall", "un", "update", "up", "upgrade", "link") for w in plain):
            return refused
        if plain[:1] == ["config"] and any(w in ("set", "delete", "edit", "rm") for w in plain[1:]):
            return refused
    if name == "uv" and plain[:1] == ["tool"] and plain[1:2] != ["run"]:
        return refused
    if name in ("gcloud", "aws", "az") and ("set" in plain or plain[:1] in (["configure"], ["config"], ["login"])):
        return refused
    pip = re.fullmatch(r"pip[0-9.]*", name) is not None
    if re.fullmatch(r"python[0-9.]*", name) and rest[:2] == ["-m", "pip"]:
        pip, plain = True, [w.lower() for w in rest[2:] if not w.startswith("-")]
    if pip or (name == "uv" and plain[:1] == ["pip"]):
        verbs = plain[1:] if name == "uv" else plain
        if verbs[:1] == ["config"] and any(w in ("set", "unset", "edit") for w in verbs):
            return refused
        if verbs[:1] in (["install"], ["uninstall"]):
            own = "/" in program and _under(str(PurePath(here or ground.root) / program), ground.root)
            if "--user" in rest or "--break-system-packages" in rest or "--system" in rest or not own:
                return f"{name} {GLOBAL}: install with the project's own environment, by its path"
    return ""


def _git(rest: list[str], ground: Ground, here: str | None) -> str:
    sub, after, moved = "", [], ""
    at = 0
    while at < len(rest):
        if rest[at] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            moved = rest[at + 1] if rest[at] in ("-C", "--git-dir", "--work-tree") and at + 1 < len(rest) else moved
            at += 2
        elif rest[at].startswith("-"):
            at += 1
        else:
            sub, after = rest[at], rest[at + 1:]
            break
    flags = {w.split("=", 1)[0] for w in after if w.startswith("-")}
    short = {c for w in flags if re.fullmatch(r"-[A-Za-z]+", w) for c in w[1:]}
    plain = [w for w in after if not w.startswith("-")]
    if sub == "push" and (flags & {"--force", "--force-with-lease", "--force-if-includes", "--mirror", "--delete", "--prune"} or short & {"f", "d"}
                          or any(w.startswith(("+", ":")) for w in plain)):
        return f"git push {FORCE}"
    if sub == "config":
        reading = flags & {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin"} or (len(plain) < 2 and not flags & {"--unset", "--unset-all"})
        if not reading and flags & {"--global", "--system"}:
            return f"git config {GLOBAL}"
        named = next((after[i + 1] for i, w in enumerate(after[:-1]) if w in ("-f", "--file")), "")
        if not reading and named:
            return _written(named, here, ground)
    if moved and sub in GIT_WRITES and (why := _written(moved, here, ground)):
        return why
    if sub in ("clone", "init") and len(plain) > (1 if sub == "clone" else 0):
        return _written(plain[-1], here, ground)
    if sub == "worktree" and plain[:1] == ["add"] and len(plain) > 1:
        return _written(plain[1], here, ground)
    if sub == "clean":
        return next((why for w in plain if (why := _written(w, _place(moved or ".", here, ground), ground, DELETES))), "")
    return ""


def _find(rest: list[str], ground: Ground, here: str | None) -> str:
    starts = []
    for word in rest:
        if word.startswith("-") or word in ("(", "!"):
            break
        starts.append(word)
    acts = "-delete" in rest or any(w in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(rest)
                                    and _program(rest[i + 1]) in EVERY | OWNED | COPIES for i, w in enumerate(rest))
    if not acts:
        return ""
    return next((why for start in (starts or ["."]) if (why := _written(start, here, ground, DELETES))), "")
