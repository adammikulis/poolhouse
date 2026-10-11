"""Whether this process is the real installed software on the person's real home, and so may touch the machine's own keystore,
its administrator password dialog or `security`.

No environment variable or argument switches it on: a copied environment, a script that forgot a flag, a test child, a checkout
run with ``PYTHONPATH=src`` and a worktree all fail it by what they are. It is true only when the process is not a test run, its
state root (symlinks resolved) is the account's own ``~/.poolhouse`` (the account's home from the password database, whatever
``HOME`` says), and this code is the copy installed under that home's ``runtimes`` directory. `poolhouse.keystore` and every
place that starts ``osascript``, ``security`` or an administrator prompt ask first.
"""

from __future__ import annotations

import sys
from pathlib import Path

from poolhouse import home

__all__ = ["Refused", "allowed", "refusal", "require"]


class Refused(PermissionError):
    """This process may not touch the machine's own keystore, password dialogs or desktop; the message says why in one sentence."""


def _under_test() -> bool:
    return "pytest" in sys.modules or "_pytest" in sys.modules


def _module_file() -> Path:
    return Path(__file__)


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def refusal(*, under_test: bool | None = None, root: Path | None = None, module: Path | None = None) -> str:
    """Why this process may not touch the machine, in one plain sentence; an empty string when it may.

    ``under_test``, ``root`` (the real ``~/.poolhouse``) and ``module`` (where this code is installed) are for the tests of this
    function; a caller never passes them.
    """
    if _under_test() if under_test is None else under_test:
        return "this is a test run, which never touches this computer's keystore, password dialogs or desktop"
    real = _resolved(root or home.account_roots()[0])
    state = _resolved(home.home())
    if state != real:
        return f"the state directory is {state}, not your own {real}, so this is not the installed Poolhouse on this computer"
    installed = _resolved(real / "runtimes")
    if not _resolved(module or _module_file()).is_relative_to(installed):
        return f"this copy of Poolhouse is not the one installed under {installed}, so it does not touch this computer's keystore"
    return ""


def allowed() -> bool:
    """Whether this process is the installed software on the real home."""
    return not refusal()


def require(what: str) -> None:
    """Raise `Refused` naming ``what`` unless this process is the installed software on the real home."""
    why = refusal()
    if why:
        raise Refused(f"{what} is refused: {why}")
