"""The maintained detached coding entry point runs task assignments."""

import os
import sys

from poolhouse.workspace import limits, localagent, task_worker

__all__ = ['run_detached']


def run_detached(argv=None):
    """Run one saved coding worker against the task board of its project."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write('usage: python -m poolhouse.workspace.localcoding NAME\n')
        return 2
    os.environ['POOLHOUSE_AGENT'] = '1'
    os.environ['POOLHOUSE_NONINTERACTIVE'] = '1'
    task_worker.run(limits.root(), localagent.check_name(args[0]))
    return 0


if __name__ == '__main__':
    raise SystemExit(run_detached())
