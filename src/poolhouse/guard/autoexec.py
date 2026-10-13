"""Writes that put code where something runs it later: git hooks, build and test files, shell startup files, agent settings.

`path_reason` says why a path runs by itself; `write_findings` reads the files a Write, Edit, MultiEdit,
NotebookEdit or apply_patch call would change and returns one finding per file.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from poolhouse import home, secretpaths
from poolhouse.guard import fetchexec
from poolhouse.guard.harm import Finding, outside, resolve
from poolhouse.guard.verbs import words_of

__all__ = ["path_reason", "write_findings", "written"]

U, D = "unsure", "destructive"
HOOKS = "runs when git commits, merges or pushes"
AGENT = "changes what the coding agent may run or is told to do"
BUILD = "runs when the project is built or tested"
SHELL_START = "runs every time a shell starts"
CI = "runs on the build servers"
EDITOR = "runs when an editor opens the project"
RUNS = {
    (".git", "hooks"): HOOKS, (".git", "config"): "sets commands git runs on its own", (".github", "workflows"): CI,
    (".github", "actions"): CI, (".local", "bin"): "puts a program on the command search path",
    (".cargo", "config.toml"): BUILD, (".cargo", "config"): BUILD, (".git", "info"): "sets commands git runs on its own",
    ("scripts", "test"): "runs when the project is tested", ("scripts", "land"): "runs when work is landed", ("scripts", "hooks"): HOOKS,
    (".config", "systemd"): "starts programs for the person", (".config", "autostart"): "starts programs at login",
}
FOLDERS = {
    ".githooks": HOOKS, ".husky": HOOKS, ".claude": AGENT, ".codex": AGENT, ".cursor": AGENT, ".agents": AGENT,
    ".vscode": EDITOR, ".idea": EDITOR, ".devcontainer": EDITOR, ".circleci": CI, ".buildkite": CI, ".direnv": SHELL_START,
    "launchagents": "runs at login", "launchdaemons": "runs at startup",
}
FILES = {
    **dict.fromkeys(("conftest.py", "setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "noxfile.py", "pytest.ini", "makefile", "gnumakefile",
                     "justfile", "taskfile.yml", "taskfile.yaml", "package.json", ".npmrc", ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs",
                     "cargo.toml", "build.rs", "build.gradle", "build.gradle.kts", "settings.gradle", "pom.xml", "gemfile",
                     "rakefile", "vagrantfile", "cmakelists.txt", "procfile", ".pre-commit-config.yaml"), BUILD),
    **dict.fromkeys(("sitecustomize.py", "usercustomize.py"), "runs every time python starts"),
    **dict.fromkeys((".envrc", ".tool-versions", "mise.toml", ".mise.toml"), "runs when a shell enters the folder"),
    **dict.fromkeys((".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".profile", ".zshrc", ".zshenv", ".zprofile", ".zlogin",
                     ".zlogout", ".cshrc", ".tcshrc", ".kshrc", "config.fish", ".vimrc", ".exrc", "init.vim", "init.lua", ".emacs", "init.el",
                     ".xinitrc", ".xprofile", "crontab"), SHELL_START),
    **dict.fromkeys((".gitconfig", ".gitmodules", ".gitattributes"), "sets commands git runs on its own"),
    **dict.fromkeys((".mcp.json", "claude.md", "agents.md", "gemini.md", ".cursorrules"), AGENT),
    **dict.fromkeys((".gitlab-ci.yml", ".travis.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", "jenkinsfile", ".drone.yml"), CI),
}
SUFFIX = ((".pth", "runs every time python starts"), (".mk", BUILD), (".gemspec", BUILD), (".plist", "can start a program when launchd loads it"),
          (".service", "can start a program when systemd loads it"), (".timer", "can start a program when systemd loads it"))
CONFIGS = re.compile(r"(?i).+\.config\.(?:js|mjs|cjs|ts)|\.(?:eslintrc|babelrc)\.(?:js|cjs)")
EDITS = frozenset({"edit", "write", "append", "create", "update", "patch", "replace", "insert", "save", "put"})
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})
PATH_KEYS = ("file_path", "path", "notebook_path", "filepath", "filename", "file")
TEXT_KEYS = ("content", "contents", "text", "file_text", "new_string", "new_text", "new_source", "data", "body")
BEGIN = "*** Begin Patch"
EXCERPT = 2
MAX_FILE = 1 << 20
SHOWN = 90


def _parts(path: str, roots: Sequence[str]) -> tuple[str, ...]:
    full = resolve(path, roots)
    for root in roots:
        base = Path(os.path.normpath(home.expand(root)))
        if full == base or base in full.parents:
            return tuple(p.lower() for p in full.relative_to(base).parts)
    user = home.user_home()
    parts = full.relative_to(user).parts if user in full.parents else full.parts
    return tuple(p.lower() for p in parts)


def _real(path: str, roots: Sequence[str]) -> tuple[str, list[str]]:
    full = resolve(path, roots)
    try:
        return os.path.realpath(full), [os.path.realpath(home.expand(r)) for r in roots]
    except (OSError, ValueError):
        return str(full), list(roots)


def path_reason(path: str, roots: Sequence[str] = ()) -> str:
    """Why a write to ``path`` runs by itself later (relative to the project root, or the home folder
    outside it, a link being followed), or empty when it does not."""
    return _reason(path, roots) or _reason(*_real(path, roots))


def _reason(path: str, roots: Sequence[str]) -> str:
    parts = _parts(path, roots)
    if not parts:
        return ""
    for run, why in RUNS.items():
        if any(parts[at:at + len(run)] == run for at in range(len(parts) - len(run) + 1)):
            return why
    name = parts[-1]
    folder = next((FOLDERS[p] for p in parts[:-1] if p in FOLDERS), "")
    if folder:
        return folder
    if name in FILES:
        return FILES[name]
    if CONFIGS.fullmatch(name):
        return BUILD
    return next((why for suffix, why in SUFFIX if name.endswith(suffix) and name != suffix), "")


def _patch(text: str) -> Iterator[tuple[str, str]]:
    """The ``(path, added lines)`` of each file an ``apply_patch`` body adds or updates."""
    path, lines = "", []
    for line in text.splitlines():
        found = re.match(r"\*\*\* (?:Add|Update) File: (.+)$", line) or re.match(r"\*\*\* Move to: (.+)$", line)
        if found or line.startswith("*** "):
            if path:
                yield path, "\n".join(lines)
            path, lines = (found.group(1) if found else ""), []
        elif path and line.startswith("+"):
            lines.append(line[1:])
    if path:
        yield path, "\n".join(lines)


def _edits(args: Mapping[str, Any]) -> Iterator[str]:
    for edit in args.get("edits") or ():
        if isinstance(edit, Mapping):
            yield from (str(edit[k]) for k in ("new_string", "new_text") if k in edit)


def written(name: str, args: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The ``(path, text)`` pairs a write tool call would put on disk; empty for any other call."""
    pairs = [pair for v in args.values() if isinstance(v, str) and BEGIN in v for pair in _patch(v)]
    if name not in WRITE_TOOLS and not set(words_of(name)) & EDITS:
        return pairs
    text = "\n".join([*(str(args[k]) for k in TEXT_KEYS if isinstance(args.get(k), str)), *_edits(args)])
    return [*pairs, *((str(args[k]), text) for k in PATH_KEYS if isinstance(args.get(k), str) and args[k])]


def _excerpt(text: str) -> str:
    lines = [fetchexec.ascii_line(x, SHOWN) for x in text.splitlines() if x.strip()][:EXCERPT]
    return "; ".join(lines)


def escapes(path: str, roots: Sequence[str]) -> list[Finding]:
    """Findings for a write to ``path``: a credential file outside the project is forbidden, and a link that leads out of it is a question."""
    hit = secretpaths.path_hit(path, roots[0] if roots else "")
    if hit and secretpaths.beyond(hit.path, roots):
        return [Finding(D, f"writes a credential file ({hit.rule}) outside the project, which an agent never does", forbidden=True)]
    if roots and not outside(path, roots) and secretpaths.beyond(path, roots):
        return [Finding(D, f"writes through a link to {fetchexec.ascii_line(os.path.realpath(resolve(path, roots)), SHOWN)}, outside the project")]
    return []


def _merged(path: str, roots: Sequence[str], args: Mapping[str, Any]) -> tuple[str, str] | None:
    """The text of the file before and after an edit call, or None when the call is no edit or the file cannot be read."""
    edits = [args] if "old_string" in args else [e for e in args.get("edits") or () if isinstance(e, Mapping)]
    full = resolve(path, roots)
    try:
        if not edits or not full.is_file() or full.stat().st_size > MAX_FILE:
            return None
        before = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    after = before
    for edit in edits:
        if isinstance(edit.get("old_string"), str) and isinstance(edit.get("new_string"), str) and edit["old_string"]:
            after = after.replace(edit["old_string"], edit["new_string"], -1 if edit.get("replace_all") else 1)
    return before, after


def _flags(text: str, path: str, roots: Sequence[str], args: Mapping[str, Any], strict: bool) -> list[fetchexec.Flag]:
    """What ``text`` flags, plus what the whole file flags once the edit is applied that it did not flag before."""
    flags = fetchexec.scan(text, strict=strict)
    merged = _merged(path, roots, args)
    if merged is not None:
        seen = {f.text for f in fetchexec.scan(merged[0], strict=strict)} | {f.text for f in flags}
        flags += [f for f in fetchexec.scan(merged[1], strict=strict) if f.text not in seen]
    return flags


def write_findings(name: str, args: Mapping[str, Any], roots: Sequence[str] = ()) -> list[Finding]:
    """One finding for each file a write call would change that runs by itself or whose new text
    downloads and runs code, sends a credential out or hides its meaning."""
    found: list[Finding] = []
    for path, text in written(name, args):
        found += escapes(path, roots)
        why = path_reason(path, roots)
        flags = _flags(text, path, roots, args, bool(why))
        shown = fetchexec.describe(flags)
        base = fetchexec.ascii_line(Path(path).name or path, SHOWN)
        if why:
            found.append(Finding(U, f"writes {base}, which {why}" + (f": {shown}" if shown else f" (starts: {_excerpt(text)})" if text.strip() else "")))
        elif flags:
            found.append(Finding(U, f"writes code to {base} that {shown}"))
    return found
