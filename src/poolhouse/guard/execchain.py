"""What a shell command does to bring in code and run it, open a way in or read a credential.

`review` returns findings for one command (its argv, where the project is, and whether another command
supplies its arguments): installs and clones, git settings that run commands, servers, writes to files that
run by themselves, and any word that names a credential file. `redirect` does the same for ``<``, ``>`` and ``>>``.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from poolhouse import secretpaths
from poolhouse.guard import autoexec, fetchexec, gitcmds, secretcmds, urlscan
from poolhouse.guard.harm import Finding, resolve
from poolhouse.guard.shellscan import has_expansion

__all__ = ["closed", "redirect", "review"]

U, D = "unsure", "destructive"
PY = re.compile(r"(?:python|pypy)[0-9.]*")
GREPS = frozenset({"grep", "egrep", "fgrep"})
ENVIRONMENT = re.compile(r"ENVIRON|\$ENV\b|\bgetenv\b|^\s*env\s*$|\|\s*env\b|\benv\s*\.")
IN_PLACE = re.compile(r"-[A-Za-z]*i[A-Za-z.]*|--in-place(?:=.*)?")
PATTERNS = GREPS | {"rg", "ag", "ack", "sed", "awk", "gawk", "mawk", "nawk", "jq", "yq"}
NAME_ONLY = frozenset({
    "ls", "stat", "file", "test", "[", "[[", "echo", "printf", "which", "type", "basename", "dirname", "realpath", "readlink", "touch",
    "mkdir", "chmod", "chown", "chgrp", "pwd", "tree", "du", "df", "wc", "rm", "rmdir", "unlink", "shred", "true", "false", "sleep",
    "date", "id", "whoami", "hostname", "uname", "find", "export", "unset", "set", "alias", "unalias", "history", "jobs", "wait",
})
GIT_NAMES = frozenset({
    "commit", "status", "check-ignore", "ls-files", "rm", "mv", "branch", "remote", "tag", "config", "fetch", "pull", "push", "merge",
    "rebase", "reset", "clean", "worktree", "submodule", "init", "clone", "switch", "stash", "reflog", "describe", "rev-parse", "ls-remote",
})
READERS = frozenset({
    "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "ag", "ack", "sed", "awk", "gawk", "mawk", "nawk", "jq", "yq",
    "od", "xxd", "hexdump", "strings", "base64", "openssl", "cp", "mv", "tar", "zip", "unzip", "7z", "7za", "gzip", "bzip2", "xz", "diff",
    "cmp", "sort", "uniq", "cut", "nl", "tac", "rev", "paste", "comm", "join", "fold", "column", "expand", "unexpand", "fmt", "tee",
    "md5sum", "shasum", "sha1sum", "sha256sum", "cksum", "rsync", "scp", "curl", "wget", "xargs",
})
RUNNERS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "ash", "python", "python3", "node", "nodejs", "ruby", "perl",
                     "php", "lua", "deno", "bun", "pwsh", "powershell", "awk", "gawk", "sed"})
TREE_NAMES = GREPS | {"rg", "ag", "ack", "ln", "cp", "mv", "rsync", "scp", "tar", "zip", "7z", "7za", "diff"}
WALKERS = frozenset({"tar", "zip", "7z", "7za", "rsync", "rclone", "ack"})
HIDDEN_FLAGS = {"rg": {"--hidden", "-uu", "-uuu", "--unrestricted", "-."}, "ag": {"--hidden", "-a", "-u", "--unrestricted", "--all-types"}}
INSTALL = frozenset({"install", "i", "add", "ci", "update", "upgrade", "reinstall", "dlx", "exec", "x", "create", "require", "download", "wheel", "run"})
FETCHING: dict[str, frozenset[str]] = {
    "pip": frozenset({"install", "download", "wheel"}), "pip3": frozenset({"install", "download", "wheel"}),
    "pipx": frozenset({"install", "run", "upgrade", "inject", "reinstall"}), "uv": frozenset({"add", "sync", "tool", "pip install", "pip sync"}),
    "poetry": frozenset({"install", "add", "update"}), "conda": INSTALL, "mamba": INSTALL, "micromamba": INSTALL,
    "npm": INSTALL - {"run"}, "yarn": INSTALL - {"run", "exec"}, "pnpm": INSTALL - {"run"}, "bun": INSTALL - {"run"},
    "cargo": frozenset({"run", "install", "add", "fetch", "update"}), "go": frozenset({"run", "install", "get", "generate"}),
    "gem": frozenset({"install", "update"}), "bundle": frozenset({"install", "update", "add"}),
    "composer": frozenset({"install", "require", "update", "create-project"}),
    "brew": frozenset({"install", "reinstall", "upgrade", "tap", "bundle"}),
    **dict.fromkeys(("apt", "apt-get", "aptitude", "dnf", "yum", "zypper", "pacman", "apk", "snap", "flatpak", "choco", "winget", "scoop", "port",
                     "nix-env", "nix"), frozenset({"install", "add", "upgrade", "dist-upgrade", "reinstall", "source"})),
}
BARE = frozenset({"yarn"})
RUN_PACKAGE = frozenset({"npx", "pnpx", "bunx", "uvx"})
SETUP = frozenset({"install", "develop", "build", "bdist", "bdist_wheel", "sdist", "egg_info"})
TUNNELS = frozenset({"ngrok", "cloudflared", "localtunnel", "lt", "serveo", "bore", "chisel", "frpc", "frps", "inlets", "pagekite", "tunnelto"})
SERVERS = frozenset({"httpd", "nginx", "lighttpd", "caddy", "miniserve", "http-server", "live-server", "simplehttpserver", "webfsd", "darkhttpd"})
SERVING_MODULES = frozenset({"http.server", "SimpleHTTPServer", "pyftpdlib", "smtpd"})
DETACH = frozenset({"nohup", "setsid", "disown", "daemonize", "at", "batch", "screen", "tmux", "systemd-run", "start-stop-daemon"})
DOWNLOADERS = frozenset({"curl", "wget", "aria2c", "http", "https", "xh"})
INERT = frozenset({
    ".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".txt", ".md", ".rst", ".html", ".htm", ".xml", ".yaml", ".yml", ".toml", ".log", ".png", ".jpg", ".jpeg", ".gif",
    ".svg", ".webp", ".ico", ".pdf", ".gz", ".tgz", ".zip", ".tar", ".bz2", ".xz", ".7z", ".gguf", ".safetensors", ".onnx", ".parquet", ".mp3", ".mp4", ".wav",
    ".css", ".woff", ".woff2", ".ttf", ".otf", ".patch", ".diff",
})
BUILD_FOLDERS = frozenset({"tests", "test", "scripts", "bin"})
PERSIST_READ = frozenset({"list", "print", "print-cache", "print-disabled", "blame", "dumpstate", "hostinfo", "managerpid", "manageruid", "managername",
                          "version", "help", "error", "resolveport", "examine"})
ENABLE_VERBS = frozenset({"enable", "reenable", "link", "edit", "set-default", "add-wants", "add-requires", "preset", "import-environment"})
SPLIT = re.compile(r"[@=:,]")
COMMAND_FLAGS = {
    "tar": ("--to-command", "--checkpoint-action", "--use-compress-program", "--rsh-command", "--info-script", "--new-volume-script", "-I", "-F"),
    "man": ("-P", "--pager", "-H", "--html", "-B", "--browser"), "zip": ("-TT", "--unzip-command"), "unzip": ("-TT",),
}


def closed(hit: secretpaths.Hit, roots: Sequence[str], verb: str = "reads") -> Finding:
    """The finding for a command or tool that ``verb`` a credential file; forbidden when the file is outside the project."""
    away = secretpaths.beyond(hit.path, roots)
    if hit.rule == secretpaths.TOO_LARGE:
        return Finding(U, f"searches a folder with {hit.rule} for credential files")
    return Finding(D, f"{verb} a credential file ({hit.rule}){' outside the project' if away else ''}", forbidden=away)


def _unseen(words: Iterable[str]) -> list[Finding]:
    return [Finding(U, f"has an invisible or direction-changing character ({fetchexec.ascii_line(w, 40)})")
            for w in words if any(unicodedata.category(c) == "Cf" for c in w)][:1]


def _python(args: list[str], words: list[str]) -> list[Finding]:
    if args[:1] == ["-m"] and len(args) > 1:
        if args[1] in SERVING_MODULES:
            return [Finding(D, "starts a server that shares this folder with other machines")]
        return _fetch(args[1], args[2:]) if args[1] in FETCHING or args[1] == "pipx" else []
    if words[:1] and words[0].endswith("setup.py") and set(words[1:3]) & SETUP:
        return [Finding(U, "runs setup.py, which builds and installs code that is not read")]
    return []


def _fetch(name: str, args: list[str]) -> list[Finding]:
    words = [a for a in args if not a.startswith("-")]
    if PY.fullmatch(name):
        return _python(args, words)
    if name in RUN_PACKAGE:
        return [Finding(U, f"{name} downloads a package and runs it, and its scripts are not read")]
    table = FETCHING.get(name)
    sub = " ".join(words[:2]) if name == "uv" and words[:1] == ["pip"] else next((w for w in words[:3] if w in (table or ())), "")
    if table is not None and (sub or (name in BARE and not words)):
        return [Finding(U, f"{name} {sub}".strip() + " downloads packages and runs their build scripts, which are not read")]
    return []


def _runs(name: str, args: list[str]) -> list[Finding]:
    flags = COMMAND_FLAGS.get(name, ())
    if any(a == f or (a.startswith((f + "=", f)) and len(f) > 2) for a in args for f in flags):
        return [Finding(U, f"{name} is given a command to run")]
    return []


def _serving(name: str, args: list[str]) -> list[Finding]:
    if name in TUNNELS or name in SERVERS or (name == "php" and "-S" in args) or (name == "busybox" and "httpd" in args) \
            or (name == "ruby" and "-run" in args and "httpd" in args):
        return [Finding(D, "starts a server or tunnel that shares files or a port with other machines")]
    if name in DETACH:
        return [Finding(U, f"{name} keeps a command running after the call ends, where nothing shows what it does")]
    return []


def _url_name(url: str) -> str:
    name = url.split("#", 1)[0].split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return name if name and "://" not in name else "index.html"


def _values(args: list[str], short: str, longs: tuple[str, ...]) -> list[str]:
    """What follows the option letter ``short`` (alone, ending a bundle or with its value attached) and the long options ``longs``."""
    out: list[str] = []
    for at, arg in enumerate(args):
        if arg.startswith("--"):
            out += [args[at + 1] for long in longs if arg == long and at + 1 < len(args)]
            out += [arg.split("=", 1)[1] for long in longs if arg.startswith(long + "=")]
        elif short and re.fullmatch(rf"-[A-Za-z]*{short}", arg):
            out += args[at + 1:at + 2]
        elif short and (attached := re.fullmatch(rf"-[A-Za-z]*{short}(.+)", arg)):
            out.append(attached.group(1))
    return out


def downloads(name: str, args: list[str]) -> list[str]:
    """The files a download command writes, as it names them; standard output is not a file."""
    urls = [a for a in args if urlscan.looks_like_url(a)]
    if name not in ("curl", "wget", "aria2c"):
        return []
    named = _values(args, "o" if name != "wget" else "O", ("--output", "--out") if name != "wget" else ("--output-document",))
    kept = [n for n in named if n and n != "-" and not n.startswith(("/dev/null", "/dev/stdout", "/dev/stderr"))]
    if name == "curl":
        remote = any(re.fullmatch(r"-[A-Za-z]*O[A-Za-z]*", a) or a in ("--remote-name", "--remote-name-all") for a in args)
        folder = next(iter(_values(args, "", ("--output-dir",))), "")
        return [str(Path(folder) / n) if folder else n for n in kept + ([_url_name(u) for u in urls] if remote else [])]
    if named:
        return kept
    folders = _values(args, "P" if name == "wget" else "d", ("--directory-prefix", "--dir"))
    return [str(Path(folders[0] if folders else "") / _url_name(u)) for u in urls]


def _runnable(dest: str, roots: Sequence[str]) -> bool:
    path = Path(dest)
    if not dest or dest == "-":
        return False
    full = resolve(dest, roots)
    executable = secretpaths.exists(full) and os.access(full, os.X_OK) and not secretpaths.is_dir(full)
    return (path.suffix.lower() not in INERT or bool({p.lower() for p in path.parts[:-1]} & BUILD_FOLDERS)
            or bool(autoexec.path_reason(dest, roots)) or executable)


def _saves(name: str, args: list[str], roots: Sequence[str], streaming: bool) -> list[Finding]:
    dests = downloads(name, args)
    if name == "tee" and streaming:
        dests = [a for a in args if not a.startswith("-")]
    if name == "curl" and any(a in ("-K", "--config") or re.fullmatch(r"-[A-Za-z]*K", a) for a in args):
        return [Finding(U, "curl takes its options from a file, which can name files to save, any of which can be run")]
    if name in ("wget", "aria2c") and (set(args) & {"-i", "--input-file", "-r", "--recursive", "-m", "--mirror", "-p"} or any(re.fullmatch(r"-[A-Za-z]*[irm]", a) for a in args)):
        return [Finding(U, f"{name} downloads many files, to names it does not show, any of which can be run")]
    return [Finding(U, f"saves a download as {fetchexec.ascii_line(Path(d).name or d, 60)}, a file that can be run") for d in dests if _runnable(d, roots)][:2]


def _persist(name: str, args: list[str]) -> list[Finding]:
    words = [a for a in args if not a.startswith("-")]
    verb = words[0] if words else ""
    if name == "launchctl" and verb not in PERSIST_READ:
        return [Finding(D, f"launchctl {verb} can make a program start at login or at once".replace("  ", " "))]
    if name == "systemctl" and (verb in ENABLE_VERBS or ("--user" in args and verb in ("start", "restart", "reload", "try-restart", "reload-or-restart"))):
        return [Finding(U, f"systemctl {verb} makes a service start by itself or at once")]
    return []


def _destinations(name: str, args: list[str]) -> list[str]:
    words = [a for a in args if not a.startswith("-")]
    if name in ("cp", "mv", "install", "ln", "rsync", "scp"):
        target = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-t", "--target-directory")), "")
        return [target or (words[-1] if words else "")]
    if name in ("tee", "truncate", "rm", "rmdir", "unlink", "shred", "touch"):
        return words
    if name == "dd":
        return [a[3:] for a in args if a.startswith("of=")]
    if name in ("sed", "perl") and any(IN_PLACE.fullmatch(a) for a in args):
        return words[1:] if name == "sed" else words
    if name in ("tar", "unzip"):
        return [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-C", "-d", "--directory")]
    return downloads(name, args)


def _writes(name: str, args: list[str], roots: Sequence[str], streaming: bool = False) -> list[Finding]:
    found = _saves(name, args, roots, streaming)
    for dest in _destinations(name, args):
        if not dest:
            continue
        found += autoexec.escapes(dest, roots)
        if why := autoexec.path_reason(dest, roots):
            found.append(Finding(U, f"writes {fetchexec.ascii_line(Path(dest).name or dest, 60)}, which {why}"))
    return found


def _candidates(name: str, args: list[str]) -> Iterator[tuple[str, bool]]:
    """The words of a command that may name a file, each with whether it is the program or pattern given to ``grep``, ``awk``, ``sed`` and the like."""
    skip = name in PATTERNS
    value = rest = False
    for word in args:
        if value:
            value = False
        elif word == "--" and not rest:
            rest = True
        elif word.startswith("-") and word != "-" and not rest:
            if skip and (word in ("-e", "--regexp") or re.fullmatch(r"-[A-Za-z]*e", word)):
                value, skip = True, False
            elif skip and (word in ("-f", "--file") or word.startswith("--regexp=")):
                skip = False
            if "=" in word and not word.startswith("--regexp="):
                yield word.split("=", 1)[1], False
        elif skip:
            skip = False
            yield word, True
        else:
            yield word, False


def _pieces(word: str) -> Iterator[str]:
    if urlscan.file_path(word):
        yield urlscan.file_path(word)
    if "://" in word:
        return
    yield word
    if SPLIT.search(word):
        yield from (p for p in SPLIT.split(word) if p)


def _bare(text: str) -> bool:
    return not re.search(r"[/~$*?\[{]", text)


def _word_hit(word: str, base: str, code: bool) -> secretpaths.Hit | None:
    if code or re.search(r"\s", word):
        if not code:
            return secretpaths.path_hit(word, base) if word.startswith(("/", "~", "./", "../")) else None
        return next((h for t in re.findall(r"[^\s'\"(),;=]+", word) if (h := secretpaths.holds_secrets(t, base))), None)
    hit = secretpaths.holds_secrets(word, base)
    if hit and hit.rule in secretpaths.WEAK and _bare(word) and not secretpaths.exists(hit.path):
        return None
    return hit


def _walks(name: str, args: list[str]) -> bool:
    flags = [a for a in args if a.startswith("-")]
    if name in GREPS:
        return any(a in ("-r", "-R", "--recursive") or re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", a) for a in flags) or "recurse" in args
    if name in HIDDEN_FLAGS:
        return bool(HIDDEN_FLAGS[name] & set(flags))
    if name in ("cp", "scp", "diff"):
        return any(a in ("-r", "-R", "-a", "--recursive") or re.fullmatch(r"-[A-Za-z]*[rRa][A-Za-z]*", a) for a in flags)
    return name in WALKERS


def _trees(name: str, args: list[str], roots: Sequence[str], deep: bool = True) -> list[Finding]:
    words = [w for w, program in _candidates(name, args) if not program and not has_expansion(w)] or ["."]
    found = []
    for word in words:
        full = resolve(secretpaths.expanded(word), roots)
        if not secretpaths.is_dir(full):
            continue
        if secretpaths.inside_home(full) and secretpaths.beyond(full, roots):
            found.append(Finding(D, "searches or copies the whole home folder, where credentials are kept", forbidden=True))
        elif deep and (hit := secretpaths.tree_hit(full)):
            found.append(closed(hit, roots, "searches or copies a folder that holds"))
    return found


def _safe_find(args: list[str], roots: Sequence[str]) -> bool:
    names = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-name", "-iname", "-path", "-ipath", "-wholename")]
    if any(a in ("-regex", "-iregex") for a in args):
        return False
    if names:
        return not any(secretpaths.find_hit(n) for n in names)
    starts = list(args[:next((i for i, a in enumerate(args) if a.startswith(("-", "(", "!"))), len(args))]) or ["."]
    return not any(secretpaths.tree_hit(resolve(s, roots)) for s in starts if secretpaths.is_dir(resolve(s, roots)))


def _safe_feeder(feeder: Sequence[str], roots: Sequence[str]) -> bool:
    if not feeder:
        return False
    name, args = Path(feeder[0]).name.lower(), list(feeder[1:])
    if name == "find":
        return _safe_find(args, roots)
    sub = next((a for a in args if not a.startswith("-")), "")
    if name == "git":
        return (sub in ("diff", "ls-tree")) or (sub == "ls-files" and not set(args) & {"-o", "--others", "-i", "--ignored"})
    if name == "ls":
        return not any((a.startswith("-") and not a.startswith("--") and set(a) & {"a", "A"}) or a == "--all" for a in args)
    return name in ("rg", "fd", "fdfind") and "--files" in args and not HIDDEN_FLAGS["rg"] & set(args)


def _file_flags(words: list[str]) -> list[str]:
    """The files a git subcommand that only names paths is told to read with ``-F``, ``-t`` or ``--template``."""
    out = [words[i + 1] for i, w in enumerate(words[:-1]) if w in ("-F", "-t", "--file", "--template")]
    return out + [w.split("=", 1)[1] for w in words if w.startswith(("--file=", "--template="))]


def _globs(args: list[str], roots: Sequence[str]) -> list[Finding]:
    """A ``-g`` pattern given to ``rg`` brings in hidden files that match it, so one that could match a credential file is a read of it."""
    found = [args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-g", "--glob", "--iglob")]
    found += [a.split("=", 1)[1] for a in args if a.startswith(("--glob=", "--iglob="))]
    return [closed(secretpaths.Hit(rule, resolve(".", roots)), roots) for g in found if not g.startswith("!") and (rule := secretpaths.find_hit(g))][:1]


def _reads(name: str, args: list[str], roots: Sequence[str], supplied: bool, feeder: Sequence[str]) -> list[Finding]:
    if name == "git":
        _, rest = gitcmds.options(args)
        args = _file_flags(rest[1:]) if rest and rest[0] in GIT_NAMES else rest[1:]
        if not rest or not args:
            return []
    if name in NAME_ONLY:
        return []
    base = roots[0] if roots else ""
    found, code = [], name in RUNNERS
    for word, program in _candidates(name, args):
        if program and name in GREPS | {"rg", "ag", "ack"}:
            continue
        if program and ENVIRONMENT.search(word):
            found.append(Finding(U, f"a {name} program that reads the environment, where keys are kept"))
        for piece in _pieces(word):
            if hit := _word_hit(piece, base, code or program):
                found.append(closed(hit, roots))
            elif name in READERS and not re.search(r"\s", piece) and has_expansion(secretpaths.expanded(piece)):
                found.append(Finding(U, "reads a path that a variable or command builds"))
    if name in HIDDEN_FLAGS:
        found += _globs(args, roots)
    if _walks(name, args) or name in TREE_NAMES:
        found += _trees(name, args, roots, _walks(name, args))
    if supplied and name in READERS and not _safe_feeder(feeder, roots):
        found.append(Finding(U, "reads files another command picks, which could include credential files"))
    return sorted({f.reason: f for f in found}.values(), key=lambda f: not f.forbidden)[:4]


def review(argv: Sequence[str], roots: Sequence[str] = (), supplied: bool = False, feeder: Sequence[str] = (), streaming: bool = False) -> list[Finding]:
    """Findings for one command: ``argv`` is its words, ``supplied`` says another command supplies its
    arguments (``xargs``, ``find -exec``), ``feeder`` is that command and ``streaming`` says its input is a download."""
    if not argv:
        return []
    name, args = Path(argv[0]).name.lower(), list(argv[1:])
    found = _unseen(argv)
    found += gitcmds.findings(args, roots) if name == "git" else _fetch(name, args)
    found += _serving(name, args) + _runs(name, args) + _persist(name, args) + _writes(name, args, roots, streaming) + secretcmds.review(name, args)
    if name in ("curl", "wget", "http", "https", "xh"):
        found += [Finding(U, f"fetches an address that {why}") for a in args if urlscan.looks_like_url(a) and (why := urlscan.reason(a))][:1]
    return found + _reads(name, args, roots, supplied, feeder)


def redirect(op: str, target: str, roots: Sequence[str] = (), fetching: bool = False) -> list[Finding]:
    """Findings for ``op target``: a ``<`` that reads a credential file, a ``>`` or ``>>`` onto a file that runs by itself,
    reaches a credential or leaves the project through a link, or saves a download (``fetching``) as a file that can be run."""
    if op.startswith("<"):
        hit = secretpaths.holds_secrets(target, roots[0] if roots else "")
        return [closed(hit, roots)] if hit else []
    if not target or target.startswith("&") or target.isdigit():
        return []
    found = autoexec.escapes(target, roots)
    if why := autoexec.path_reason(target, roots):
        found.append(Finding(U, f"writes {fetchexec.ascii_line(Path(target).name or target, 60)}, which {why}"))
    if fetching and _runnable(target, roots):
        found.append(Finding(U, f"saves a download as {fetchexec.ascii_line(Path(target).name or target, 60)}, a file that can be run"))
    return found
