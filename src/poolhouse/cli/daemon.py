"""The device daemon's command."""

import sys

from poolhouse import runtime
from poolhouse.api import pool
from poolhouse.board import model_uploads
from poolhouse.cli import phone_actions
from poolhouse.fleet import daemon


def main(argv: list[str] | None = None) -> int:
    runtime.forward("poolhouse.cli.daemon", list(sys.argv[1:] if argv is None else argv))
    return daemon.run(argv, pool_actions=pool, announcer=model_uploads.board_notice, phone_actions=phone_actions)


if __name__ == "__main__":
    raise SystemExit(main())
