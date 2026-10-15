"""Session-bound notification metadata and coding checkpoints."""

import sys
from pathlib import Path

from poolhouse.board.client import NodeError
from poolhouse.errors import Denied
from poolhouse.hook_diagnostics import reason
from poolhouse.log import say, warn
from poolhouse.workspace import (
    board_cli,
    integration_git as repo,
    limits,
    nudge,
    worktree_lifecycle,
)
from poolhouse.workspace.harness_session import inbox_of


def read(agent: str, cwd: Path) -> str:
    """Return unread metadata of the board of ``cwd`` under the agent's own token."""
    name, now, entries = inbox_of(agent, cwd, nudge.PAGE)
    return nudge.Waiting.of(board_cli.waiting_entries(entries, name, now)).line()


def compact(line: str) -> str:
    """Place urgent action before bounded sender details."""
    head, separator, urgent = line.partition('. A direct ')
    if separator:
        return ('A direct ' + urgent)[:180] + '. ' + head[:45]
    return line[:220]


def checkpoint(base: Path, actor: str) -> None:
    """Checkpoint changed commits and branches under the authenticated reader."""
    for scope in worktree_lifecycle.scopes(base, actor):
        path = Path(scope['path'])
        if not path.exists():
            continue
        commit = repo.git(path, 'rev-parse', 'HEAD')
        branch = repo.git(path, 'branch', '--show-current')
        if commit not in scope.get('commits', ()) or branch not in scope.get('branches', ()):
            worktree_lifecycle.remember(base, actor, str(path))


def notify(agent: str, cwd: Path) -> None:
    """Emit unread metadata before checkpointing the same authenticated identity."""
    line = compact(read(agent, cwd))
    if line:
        say(line, flush=True)
    checkpoint(limits.root(), agent)


if __name__ == '__main__':
    try:
        notify(sys.argv[1], Path(sys.argv[2]))
    except (Denied, NodeError, OSError, RuntimeError, ValueError, KeyError) as error:
        warn(reason(error))
        raise SystemExit(1) from None
