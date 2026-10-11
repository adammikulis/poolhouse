"""The device daemon's command."""

import sys

from poolhouse import runtime
from poolhouse.fleet import daemon


def main(argv: list[str] | None = None) -> int:
    runtime.forward("poolhouse.cli.daemon", list(sys.argv[1:] if argv is None else argv))
    return daemon.run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
