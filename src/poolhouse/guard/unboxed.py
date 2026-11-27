"""The deterministic blocks of a delegate that runs without a sandbox, asked before every tool call and judged from the call alone.

Refused: the person folder, the session tokens, the authority registry and the guards (`poolhouse.protectedwrites`); a force push and a remote
deletion; a read of a credential store; a write outside the project, any write to the user's global configuration and a recursive deletion outside
the project. These are not a boundary: a program the model writes, or one it runs, reaches whatever the account reaches, and only a sandbox stops that.

One reading of a call gives both what refuses it (`refusal`) and what it writes or removes (`written`), blocked or not.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import PurePath
from typing import Any

from poolhouse import protectedwrites, secretpaths, worktreerules
from poolhouse.guard import shellscan

__all__ = ["Effect", "Ground", "effects", "refusal", "written"]


@dataclass(frozen=True, slots=True)
class Ground:
    """The ``root`` a delegate works in, the user's ``home`` and the ``state`` root the blocks protect, and the delegate's own ``scratch`` folder."""

    root: str
    home: str
    state: str
    scratch: str = ""


@dataclass(frozen=True, slots=True)
class Effect:
    """One thing a call would do that the blocks look at: why it is refused (empty when no block applies) and what it writes or removes (a path
    where one can be told, else the word that names it; empty for what writes nothing)."""

    why: str = ""
    wrote: str = ""


READS = {"Read": ("file_path", "path"), "Glob": ("path", "pattern"), "Grep": ("path", "glob"), "LS": ("path",)}
WRITES = ("Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch", "patch")
SHELL = ("Bash", "PowerShell")
UNDONE = frozenset({"patch", "ruby", "unzip"})
"""Programs that change files in the folder they run in, which no block reads the targets of."""
VALUED = "ughpCrtUDRT"
"""The letters of the options of `sudo` and its like that take a value, attached or the next word."""
VALUED_LONG = frozenset({"--user", "--group", "--host", "--prompt", "--chdir", "--role", "--type", "--other-user", "--chroot", "--command-timeout"})
CONFIG_PLAIN = frozenset({"--get", "--get-all", "--get-regexp", "--list", "-l", "--global", "--system", "--local", "--worktree", "--show-origin",
                          "--show-scope", "--name-only", "--null", "-z", "--includes", "--no-includes"})
"""The options of `git config` that take no value and change nothing: the only ones a call recognised as a read may have."""
GIT_VALUED = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--attr-source", "--super-prefix")
"""The options before git's subcommand that take the next word."""
LOOKS = ("-get", "-list", "-show", "-print", "-version", "-help")
"""How the options of `networksetup` and `systemsetup` that only read begin."""
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
    return next((each.why for each in effects(name, args, ground) if each.why), "")


def written(name: str, args: Mapping[str, Any], ground: Ground) -> list[str]:
    """What the call would write or remove, whether or not a block refuses it; empty for one that only reads and for what the line does not say
    (a program's own writes)."""
    if name in SHELL:
        here = _place(str(args["workdir"]), ground.root, ground) if args.get("workdir") else ground.root
        found = _line(str(args.get("command") or ""), ground, here, 0)
    else:
        found = effects(name, args, ground) if name in WRITES else iter(())
    return list(dict.fromkeys(each.wrote for each in found if each.wrote))


def effects(name: str, args: Mapping[str, Any], ground: Ground) -> Iterator[Effect]:
    """The effects of one call, in the order the blocks are asked."""
    if name in LEAD:
        return
    environ = {"HOME": ground.home, "POOLHOUSE_HOME": ground.state}
    if name in SHELL:
        line = str(args.get("command") or "")
        yield Effect(protectedwrites.refusal(line, ground.root, lambda _cwd: "an unattended delegate never publishes", environ))
        yield from _line(line, ground, ground.root, 0)
        return
    if name not in READS and name not in WRITES:
        yield Effect(f"{name} is not one of a delegate's tools")
        return
    yield Effect(protectedwrites.tool_refusal(name, args, cwd=ground.root, environ=environ))
    if name in READS:
        yield from (Effect(f"{text} {CREDENTIAL}") for key in READS[name] if (text := str(args.get(key) or "")) and _secret(text, ground.root, ground))
        return
    paths = [str(args[key]) for key in ("file_path", "path", "filePath", "notebook_path") if args.get(key)]
    for value in args.values():
        if isinstance(value, str) and "*** Begin Patch" in value:
            paths += worktreerules.patch_paths(value)
    if not paths:
        yield Effect(f"{name} names no file the blocks can check")
    for text in paths:
        place = _place(text, ground.root, ground)
        if place is None:
            yield Effect(f"{text} {UNREAD}", text)
        elif protectedwrites.guarded(place, environ) or protectedwrites.secret(place, environ):
            yield Effect(f"{text} is a guard, a hook setting, the person folder or the authority registry", place)
        else:
            yield Effect("" if _inside(place, ground) else f"{text} {OUTSIDE}", place)


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
    try:
        return os.path.realpath(PurePath(here or ground.root) / named)
    except ValueError:   # text no file system takes as a name
        return None


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


def _line(line: str, ground: Ground, here: str | None, depth: int) -> Iterator[Effect]:
    """The effects of the shell line ``line``, run from ``here`` (None once a `cd` cannot be told)."""
    if depth > DEPTH:
        yield Effect("nests commands too deeply for the blocks to read")
        return
    text = protectedwrites.without_heredocs(line)
    for found in SUBSTITUTION.finditer(text):
        yield from _line(found.group(1) or found.group(2) or "", ground, here, depth + 1)
    found_segments, problems = shellscan.segments(SUBSTITUTION.sub(" $SUBSTITUTED ", text))
    if text.strip() and not found_segments:
        yield Effect(f"cannot be read by the blocks: {problems[0].reason if problems else 'no command in it'}")
    for segment in found_segments:
        for operator, target in segment.redirects:
            reads = "<" in operator and ">" not in operator
            if reads and _secret(target, here, ground):
                yield Effect(f"{target} {CREDENTIAL}")
            if not reads and target not in NOWHERE and not target.lstrip("&").isdigit() and target != "-":
                yield _written(target, here, ground)
        words = list(segment.argv)
        while words and re.match(r"\w+=", words[0]):
            words.pop(0)
        if not words:
            continue
        if _program(words[0]) in ("cd", "pushd"):
            here = _place(next((w for w in words[1:] if not w.startswith("-")), ground.home), here, ground)
            continue
        yield from _command(words, ground, here, depth)


def _program(word: str) -> str:
    return PurePath(word.replace("\\", "/")).name.lower().removesuffix(".exe")


def _written(target: str, here: str | None, ground: Ground, why: str = OUTSIDE) -> Effect:
    """The write of ``target``, refused when it cannot be told or is outside the project."""
    if target.lower() in NOWHERE:
        return Effect()
    fixed = re.split(r"[*?\[{]", target)[0] if shellscan.has_glob(target) else target
    place = _place(fixed or ".", here, ground)
    if place is None:
        return Effect(f"{target} {UNREAD}", target)
    return Effect("" if _inside(place, ground) else f"{target} {why}", place)


def _command(words: list[str], ground: Ground, here: str | None, depth: int) -> Iterator[Effect]:
    """The effects of one command."""
    name, rest, fed = _program(words[0]), words[1:], False
    while name in LAUNCHERS:
        fed = fed or name == "xargs"
        while rest and (rest[0].startswith("-") or re.match(r"(\w+=|\d+(\.\d+)?[smhd]?$)", rest[0])):
            rest = rest[1:]
        if not rest:
            return
        words, name, rest = rest, _program(rest[0]), rest[1:]
    found = _acted((words[0], *rest), name, ground, here, (depth, fed))
    # a command that only looks is judged as any other and writes nothing
    yield from (replace(each, wrote="") for each in found) if _only_looks(name, rest) else found


def _takes_next(word: str) -> bool:
    """Whether the option ``word`` of `sudo` and its like takes the word after it: the first letter of a bundle that takes a value takes the rest
    of the bundle, or the next word when it ends it."""
    if word.startswith("--") or not word.startswith("-"):
        return word in VALUED_LONG
    at = next((index for index, letter in enumerate(word[1:], 1) if letter in VALUED), 0)
    return bool(at) and at == len(word) - 1


def _letters(rest: list[str], valued: str) -> Iterator[str]:
    """The option letters of ``rest``, skipping the values: a letter of ``valued`` takes what follows it in its word, else the next word."""
    skip = False
    for word in rest:
        if skip or not word.startswith("-") or word.startswith("--"):
            skip = False
            continue
        for index, letter in enumerate(word[1:], 1):
            yield letter
            if letter in valued:
                skip = index == len(word) - 1
                break


def _only_looks(name: str, rest: list[str]) -> bool:
    """Whether the program ``name`` with ``rest`` only reads: a listing or a test of an archive, a patch only tried, a setting only asked for."""
    if name == "unzip":
        return any(letter in "ltvpzZ" for letter in _letters(rest, "Pd"))
    if name == "patch":
        return bool({"--dry-run", "--check", "-C"} & set(rest))
    if name == "scutil":
        return "--get" in rest and "--set" not in rest
    if name in ("networksetup", "systemsetup"):
        options = [w.lower() for w in rest if w.startswith("-")]
        return bool(options) and all(w.startswith(LOOKS) for w in options)
    return False


def _as_another(name: str, rest: list[str], ground: Ground, here: str | None, depth: int) -> Iterator[Effect]:
    """The effects of a command run as another account: refused whatever it is, and writing what the command under it writes."""
    inline = next((rest[i + 1] for i, w in enumerate(rest[:-1]) if w in ("-c", "--command")), "") if name == "su" else ""
    if name == "runas":   # its command is one quoted word after the `/user:` options
        inline = next((w for w in reversed(rest) if not w.startswith("/")), "")
    inner = list(rest)
    while inner and (inner[0].startswith("-") or re.match(r"\w+=", inner[0])):
        inner = inner[2 if _takes_next(inner[0]) else 1:]
    found: Iterator[Effect] = iter(())
    if depth < DEPTH:
        found = _line(inline, ground, here, depth + 1) if inline else _command(inner, ground, here, depth + 1) if inner else found
    theirs = [each.wrote for each in found if each.wrote]
    yield from (Effect(f"{name} {GLOBAL}", each) for each in theirs or [""])


def _acted(words: tuple[str, ...], name: str, ground: Ground, here: str | None, run: tuple[int, bool]) -> Iterator[Effect]:
    """The effects of the program ``name`` run as ``words``; ``run`` is how deeply the line is nested and whether another command feeds it. A
    credential it names is refused first, and what it writes is still read after."""
    (depth, fed), rest = run, list(words[1:])
    if name in AS_ANOTHER:
        yield from _as_another(name, rest, ground, here, depth)
        return
    if name in SHELLS or name == "eval":
        inline = " ".join(rest) if name == "eval" else next((rest[i + 1] for i, w in enumerate(rest[:-1]) if re.fullmatch(r"-[a-z]*c", w)), "")
        if inline:
            yield from _line(inline, ground, here, depth + 1)
        return
    plain = [w for w in rest if not w.startswith("-")]
    lowered = [w.lower() for w in plain]
    named = (part for w in words for part in (w, w.split("=", 1)[-1], w.lstrip("@")) if part)
    if secret := next((each for each in named if ("/" in each or each.startswith((".", "~", "$HOME"))) and _secret(each, here, ground)), ""):
        yield Effect(f"{secret} {CREDENTIAL}")
    if any(all(each in [w.lower() for w in rest] for each in form) for form in CREDENTIAL_COMMANDS.get(name, ())):
        yield Effect(f"{name} {CREDENTIAL}")
    if why := _global(words, lowered, here, ground):
        yield Effect(why, name)
        return
    if name == "git":
        yield from _git(rest, ground, here)
        return
    if name == "find":
        yield from _find(rest, ground, here)
        return
    if name == "gh" and (plain[1:2] == ["delete"] or (plain[:1] == ["api"] and "DELETE" in (word.rpartition("=")[2].removeprefix("-X").upper()
                                                                                          for word in rest))):
        yield Effect(wrote="gh-delete")
    targets = _targets(name, rest, plain)
    if fed and (targets or name in EVERY | OWNED | COPIES):
        yield Effect(f"{name} takes its targets from another command: it {UNREAD}", targets[0] if targets else name)
        return
    recursive = any(w == "--recursive" or re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", w) for w in rest) or (name in ("rd", "rmdir") and "/s" in lowered)
    for target in targets:
        if name in ("rm", "rd", "rmdir", "del", "remove-item") and recursive:
            place = _place(re.split(r"[*?\[{]", target)[0] or ".", here, ground)
            if place is None:
                yield Effect(f"{target} {UNREAD}", target)
            else:
                spared = _inside(place, ground) and place != os.path.realpath(ground.root)
                yield Effect("" if spared else f"{target} {DELETES}", place)
        else:
            yield _written(target, here, ground)
    if name in UNDONE and not targets and (name == "patch" or (name == "unzip" and plain) or any(w.startswith(("-i", "-pi")) for w in rest)):
        yield Effect(wrote=here or ground.root)


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
        return _saved(name, rest)
    if name == "tar":
        named = [rest[i + 1] for i, w in enumerate(rest[:-1]) if w in ("-C", "--directory")]
        packs = any("c" in w for w in rest[:1]) or any(re.fullmatch(r"-[a-z]*c[a-z]*", w) for w in rest)
        return named + ([rest[i + 1] for i, w in enumerate(rest[:-1]) if re.fullmatch(r"-?[a-z]*f", w)] if packs else [])
    if name in ("unzip", "7z"):
        attached = "-o" if name == "7z" else "-d"
        return [rest[i + 1] for i, w in enumerate(rest[:-1]) if w == "-d"] + [w[2:] for w in rest if w.startswith(attached) and len(w) > 2]
    return []


def _saved(name: str, rest: list[str]) -> list[str]:
    """The files a download is saved to: the output option alone, at the end of options written together, or with its file attached."""
    letter, long = ("o", ("--output",)) if name == "curl" else ("O", ("--output-document", "--directory-prefix"))
    # only options that take no value may stand before the letter in one word, or the letter is another option's value
    together = re.compile(rf"-[sSfLkvIiqgnN#46]*{letter}(.*)" if name == "curl" else rf"-[qnvcNrSbd]*{letter}(.*)")
    found = []
    for at, word in enumerate(rest):
        option, equals, value = word.partition("=")
        if equals and option in long:
            found.append(value)
        elif word in long or (name == "wget" and word == "-P"):
            found += rest[at + 1:at + 2]
        elif match := together.fullmatch(word):
            found += [match[1]] if match[1] else rest[at + 1:at + 2]
    return [each for each in found if each and each != "-"]


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


def _config_reads(after: list[str]) -> bool:
    """Whether the words after `git config` are one of the forms known to read: the subcommand `get` or `list`, or options that take no value and
    then at most the key (none for a listing). Anything else counts as a change; nothing is guessed from what an option might mean."""
    if after[:1] in (["get"], ["list"]):
        return True
    at = next((index for index, word in enumerate(after) if word not in CONFIG_PLAIN), len(after))
    keys = after[at:]
    return len(keys) <= (0 if {"--list", "-l"} & set(after[:at]) else 1) and not any(word.startswith("-") for word in keys)


def _config_files(after: list[str]) -> list[str]:
    """Every word after `git config` that may be the file it is given: the value of `--file` by any beginning of its name, and what follows an `f`
    in a word of short options."""
    found = []
    for at, word in enumerate(after):
        option, equals, value = word.partition("=")
        if len(option) > 2 and "--file".startswith(option):
            found += [value] if equals else after[at + 1:at + 2]
        elif re.match(r"-[^-]", word) and "f" in word:
            attached = word.partition("f")[2]
            found += [attached] if attached else after[at + 1:at + 2]
    return found


def _config_changes(after: list[str], ground: Ground, here: str | None, tree: str) -> Effect:
    """The effect of a `git config` not known to read, in the repository at ``tree``: refused when it names the user's or the computer's
    configuration, or a file outside the project."""
    if any(len(word) > 2 and ("--global".startswith(word) or "--system".startswith(word)) for word in after):
        return Effect(f"git config {GLOBAL}", "git-config")
    named = [_written(each, _place(tree, here, ground), ground) for each in _config_files(after)]
    return next((each for each in named if each.why), named[0] if named else Effect(wrote=str(PurePath(tree) / ".git")))


def _git(rest: list[str], ground: Ground, here: str | None) -> Iterator[Effect]:
    sub, after, moved = "", [], ""
    at = 0
    while at < len(rest):
        if rest[at] in GIT_VALUED:
            moved = rest[at + 1] if rest[at] in ("-C", "--git-dir", "--work-tree") and at + 1 < len(rest) else moved
            at += 2
        elif rest[at].startswith(("--git-dir=", "--work-tree=")):
            moved, at = rest[at].partition("=")[2], at + 1
        elif rest[at].startswith("-"):
            at += 1
        else:
            sub, after = rest[at], rest[at + 1:]
            break
    flags = {w.split("=", 1)[0] for w in after if w.startswith("-")}
    short = {c for w in flags if re.fullmatch(r"-[A-Za-z]+", w) for c in w[1:]}
    plain = [w for w in after if not w.startswith("-")]
    tree = _place(moved or ".", here, ground) or moved or ground.root
    reading = sub == "config" and _config_reads(after)
    # `-e` of `git clean` takes the next word, so an `-n` there is a pattern and nothing is only tried
    looks = reading or (sub == "clean" and ("n" in _letters(after, "e") or "--dry-run" in flags))
    if sub == "push" and (flags & {"--force", "--force-with-lease", "--force-if-includes", "--mirror", "--delete", "--prune"} or short & {"f", "d"}
                          or any(w.startswith(("+", ":")) for w in plain)):
        yield Effect(f"git push {FORCE}", "git-push")
        return
    if sub == "push":   # whatever else refuses it, a push changes the remote
        yield Effect(wrote="git-push")
        return
    if sub == "config" and not reading:
        changed = _config_changes(after, ground, here, tree)
        yield changed
        if changed.why:
            return
    if moved and sub in GIT_WRITES and (effect := _written(moved, here, ground)).why:
        yield replace(effect, wrote="") if looks else effect
        return
    if sub in ("clone", "init") and len(plain) > (1 if sub == "clone" else 0):
        yield _written(plain[-1], here, ground)
    elif sub == "worktree" and plain[:1] == ["add"] and len(plain) > 1:
        yield _written(plain[1], here, ground)
    elif sub == "clean":
        removed = (_written(w, _place(moved or ".", here, ground), ground, DELETES) for w in plain)
        yield from (replace(each, wrote="") for each in removed) if looks else removed
        yield Effect(wrote="" if looks else tree)
    elif sub in worktreerules.CHANGES_THE_TREE and not (sub == "stash" and plain[:1] in (["list"], ["show"])):
        # `apply` changes files of the tree; every other of these writes the repository too
        yield Effect(wrote=tree if sub == "apply" else str(PurePath(tree) / ".git"))


def _find(rest: list[str], ground: Ground, here: str | None) -> Iterator[Effect]:
    starts = []
    for word in rest:
        if word.startswith("-") or word in ("(", "!"):
            break
        starts.append(word)
    acts = "-delete" in rest or any(w in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(rest)
                                    and _program(rest[i + 1]) in EVERY | OWNED | COPIES for i, w in enumerate(rest))
    if acts:
        yield from (_written(start, here, ground, DELETES) for start in (starts or ["."]))
