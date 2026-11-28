"""Start this phone's node under its supervisor and stay while it answers.

The app ends the whole guest when this exits, and starts it again. The node listens only where ``/work/node.json``
says (``{"listen": "ADDRESS:PORT"}``): with no such file nothing listens on any network, and the phone is a device
in no pool. ``/work/node-status.json`` holds the device's fingerprint and pool for the app to show.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from poolhouse import node_binary, node_health, node_launch, node_supervise
from poolhouse.files import write_json

BINARY = Path("/work/poolhouse-node")
CONFIG = Path("/work/node.json")
STATUS = Path("/work/node-status.json")


def main() -> int:
    state = node_launch.default_state()
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    listen = json.loads(CONFIG.read_text(encoding="utf-8")).get("listen", "") if CONFIG.is_file() else ""
    node_supervise.point(state, BINARY, node_binary.sha256(BINARY))
    extra = ["--listen", listen, "--presence", "auto"] if listen else ["--presence", "auto"]
    health = node_launch.ensure_node(state, extra=extra, wait_s=120)
    print(f"node answers: {json.dumps(health)[:400]}", flush=True)
    try:
        while node_health.node_health(state) is not None:
            status = node_health.call(state, "pool_status")
            write_json(STATUS, {"device": status.get("fingerprint") or "", "pool": status.get("pool") or ""})
            time.sleep(20)
    finally:
        STATUS.unlink(missing_ok=True)
    print("the node stopped answering", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
