"""``python -m poolhouse.bench``: what `detach` re-runs in its own session, and what
the ``python -m poolhouse.bench`` script is."""

from poolhouse.bench import main

POOLHOUSE_ENTRY_POINT = "measures real models leased on this computer and keeps the results"

raise SystemExit(main())
