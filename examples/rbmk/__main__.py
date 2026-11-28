"""``python -m examples.rbmk``: start the RBMK control room app and print its address."""

import sys

from .web.server import main

POOLHOUSE_ENTRY_POINT = "leases a real model on this computer and keeps the runs in the local run store"

sys.exit(main())
