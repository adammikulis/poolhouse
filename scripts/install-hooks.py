"""Explicitly install this repository's Git hooks without changing another repository."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

HOOKS = ("pre-commit", "commit-msg", "pre-push", "post-merge", "post-commit")
MARKER = "# poolhouse managed hook"
SCOPE = "poolhouse.hooks.repository"


def _git(root: Path, *args: str, configured: bool = False) -> str:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if not configured:
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.check_output(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                                   env=environment, text=True, stderr=subprocess.DEVNULL).strip()


def _scope() -> tuple[Path, Path, Path]:
    source = Path(__file__).resolve().parent.parent
    common = Path(_git(source, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    current = Path(_git(Path.cwd(), "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if current != common or Path(_git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("run this repository's installer from its own checkout")
    hooks = Path(_git(source, "rev-parse", "--path-format=absolute", "--git-path", "hooks", configured=True)).resolve()
    if hooks != common / "hooks":
        raise ValueError("the configured hooks directory is custom; leaving it unchanged")
    primary = Path(_git(source, "worktree", "list", "--porcelain").splitlines()[0].removeprefix("worktree ")).resolve()
    return source, primary, common


def _ours(hook: Path, sources: set[Path]) -> bool:
    try:
        if hook.is_symlink():
            return (hook.parent / hook.readlink()).resolve() in sources
        if not hook.is_file() or hook.stat().st_nlink != 1 or hook.stat().st_size > 16384:
            return False
        lines = hook.read_text(encoding="utf-8").splitlines()
        if len(lines) != 4 or lines[:2] != ["#!/bin/sh", MARKER]:
            return False
        interpreter, command = shlex.split(lines[2]), shlex.split(lines[3])
        return (len(interpreter) == 2 and interpreter[0] == "export" and interpreter[1].startswith("PYTHON=/")
                and len(command) == 4 and command[:2] == ["exec", "sh"] and command[3] == "$@"
                and Path(command[2]).is_absolute() and Path(command[2]).resolve() in sources
                and lines[2] == "export PYTHON=" + shlex.quote(interpreter[1].removeprefix("PYTHON="))
                and lines[3] == f'exec sh {shlex.quote(command[2])} "$@"')
    except (OSError, ValueError, RuntimeError):
        return False


def install() -> None:
    try:
        source, primary, common = _scope()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else "run this repository's installer from its own checkout"
        raise SystemExit(f"install-hooks: {detail}") from None
    import envcheck

    status, text = envcheck.report(["commit"])
    if status:
        print(text, file=sys.stderr)
        raise SystemExit(status)
    destination = common / "hooks"
    destination.mkdir(parents=True, exist_ok=True)
    for name in HOOKS:
        hook = destination / name
        target = primary / "scripts" / "hooks" / name
        owned_sources = {target.resolve(), (source / "scripts" / "hooks" / name).resolve()}
        if os.path.lexists(hook) and not _ours(hook, owned_sources):
            print(f"install-hooks: {name} exists and is not ours; leaving it alone", file=sys.stderr)
            continue
        if hook.is_symlink():
            hook.unlink()
        hook.write_text("#!/bin/sh\n" + MARKER + "\n"
                        + f"export PYTHON={shlex.quote(Path(sys.executable).as_posix())}\n"
                        + f"exec sh {shlex.quote(target.as_posix())} \"$@\"\n", encoding="utf-8", newline="\n")
        hook.chmod(hook.stat().st_mode | 0o111)
        print(f"install-hooks: {name} -> scripts/hooks/{name}")
    _git(source, "config", "--local", SCOPE, str(common))


if __name__ == "__main__":
    install()
