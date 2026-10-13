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

from poolhouse import home
from poolhouse.guard import fetchexec
from poolhouse.guard.harm import Finding, resolve
from poolhouse.guard.verbs import words_of

__all__ = ["path_reason", "write_findings", "written"]

U = "unsure"
HOOKS = "runs when git commits, merges or pushes"
AGENT = "changes what the coding agent may run or is told to do"
BUILD = "runs when the project is built or tested"
SHELL_START = "runs every time a shell starts"
CI = "runs on the build servers"
EDITOR = "runs when an editor opens the project"
RUNS = {
    (".git", "hooks"): HOOKS, (".git", "config"): "sets commands git runs on its own", (".github", "workflows"): CI,
    (".github", "actions"): CI, (".local", "bin"): "puts a program on the command search path",
    (".cargo", "config.toml"): BUILD, (".cargo", "config"): BUILD,
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
    **dict.fromkeys((".gitconfig", ".gitmodules"), "sets commands git runs on its own"),
    **dict.fromkeys((".gitlab-ci.yml", ".travis.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", "jenkinsfile", ".drone.yml"), CI),
}
SUFFIX = ((".pth", "runs every time python starts"), (".mk", BUILD), (".gemspec", BUILD))
CONFIGS = re.compile(r"(?i).+\.config\.(?:js|mjs|cjs|ts)|\.(?:eslintrc|babelrc)\.(?:js|cjs)")
EDITS = frozenset({"edit", "write", "append", "create", "update", "patch", "replace", "insert", "save", "put"})
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})
PATH_KEYS = ("file_path", "path", "notebook_path", "filepath", "filename", "file")
TEXT_KEYS = ("content", "contents", "text", "file_text", "new_string", "new_text", "new_source", "data", "body")
BEGIN = "*** Begin Patch"
EXCERPT = 2
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
    patches = [v for v in args.values() if isinstance(v, str) and BEGIN in v]
    if patches:
        return [pair for patch in patches for pair in _patch(patch)]
    if name not in WRITE_TOOLS and not set(words_of(name)) & EDITS:
        return []
    path = next((str(args[k]) for k in PATH_KEYS if isinstance(args.get(k), str)), "")
    text = "\n".join([*(str(args[k]) for k in TEXT_KEYS if isinstance(args.get(k), str)), *_edits(args)])
    return [(path, text)] if path else []


def _excerpt(text: str) -> str:
    lines = [fetchexec.ascii_line(x, SHOWN) for x in text.splitlines() if x.strip()][:EXCERPT]
    return "; ".join(lines)


def write_findings(name: str, args: Mapping[str, Any], roots: Sequence[str] = ()) -> list[Finding]:
    """One finding for each file a write call would change that runs by itself or whose new text
    downloads and runs code, sends a credential out or hides its meaning."""
    found: list[Finding] = []
    for path, text in written(name, args):
        why = path_reason(path, roots)
        flags = fetchexec.scan(text, strict=bool(why))
        shown = fetchexec.describe(flags)
        base = fetchexec.ascii_line(Path(path).name or path, SHOWN)
        if why:
            found.append(Finding(U, f"writes {base}, which {why}" + (f": {shown}" if shown else f" (starts: {_excerpt(text)})" if text.strip() else "")))
        elif flags:
            found.append(Finding(U, f"writes code to {base} that {shown}"))
    return found
