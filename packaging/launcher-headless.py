"""Entry point for the frozen device daemon and Python subprocess commands."""

import multiprocessing
import sys


def daemon(argv):
    """The daemon command, which holds the pool actions; imported only by the start that serves."""
    from poolhouse.cli.daemon import main as composed
    return composed(argv)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from poolhouse.fleet.frozen_dispatch import main
    sys.exit(main(daemon_main=daemon))
