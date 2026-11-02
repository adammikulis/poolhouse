"""``python -m examples.rbmk``: start the RBMK control room app and print its address."""

import sys

from .web.server import main

sys.exit(main())
