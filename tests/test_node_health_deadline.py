"""A node that answers slowly is as late as one that does not answer: a whole exchange has one deadline.

PLUMBING ONLY: the other end here is a plain socket that sends a framed reply one byte at a time, because a real node never does.
"""

import json
import socket
import struct
import tempfile
import threading
import time
from pathlib import Path

import pytest

from poolhouse import node_health
from poolhouse.platform import is_windows


def dribbling_node(state: Path, gap_s: float) -> threading.Thread:
    """Serve one request on ``state``'s node socket and send the reply a byte at a time, ``gap_s`` apart."""
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(node_health.socket_path(state)))
    listener.listen(1)

    def serve() -> None:
        with listener:
            client, _ = listener.accept()
            with client:
                (size,) = struct.unpack(">I", client.recv(4))
                client.recv(size)
                body = json.dumps({"ok": True, "result": {"node": "poolhouse-node"}}).encode()
                try:
                    for byte in struct.pack(">I", len(body)) + body:
                        client.sendall(bytes([byte]))
                        time.sleep(gap_s)
                except OSError:
                    return  # the caller gave up

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    return worker


@pytest.mark.skipif(is_windows(), reason="the Unix socket exchange")
def test_a_reply_that_arrives_a_byte_at_a_time_runs_out_the_exchange_deadline():
    with tempfile.TemporaryDirectory(prefix="nh-", dir="/tmp") as folder:
        state = Path(folder)
        dribbling_node(state, gap_s=0.3)
        began = time.monotonic()
        with pytest.raises(TimeoutError):
            node_health.exchange(state, node_health.request_body("hello"), timeout=1.0)
        assert time.monotonic() - began < 2.5
