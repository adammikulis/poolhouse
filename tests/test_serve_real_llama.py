"""A real llama-server behind the broker and the request queue.

Runs with ``--real-model`` through tests/real_model.py: the pinned small model, the machine's llama-server, and no other
llama-server running. A model or binary that is missing, or compute that is busy, fails the test and names the fix.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from poolhouse import gate
from poolhouse.client import Client
from poolhouse.decide.types import DecideError
from poolhouse.serve.process import pid_exists

pytest_plugins = ["real_model"]
pytestmark = [pytest.mark.slow, pytest.mark.real_model, pytest.mark.timeout(600)]
SRC = str(Path(__file__).resolve().parent.parent / "src")


def test_a_real_server_is_leased_queued_and_stopped(real):
    with real.serving("chat", context=512) as served:
        info = served.info
        assert info.pid and pid_exists(info.pid) and info.lease
        url = f"{info.base_url}/v1/chat/completions"
        assert gate.device_of(url) == "gpu"

        seen: list[list[int]] = []
        stop = threading.Event()

        def watch() -> None:
            while not stop.is_set():
                rows = gate.snapshot().get("gpu", [])
                if rows:
                    seen.append([r.get("pid") for r in rows])
                time.sleep(0.01)

        code = ("import sys\nfrom poolhouse.client import Client\n"
                "print(Client(sys.argv[1]).chat([{'role': 'user', 'content': 'Say hi.'}], "
                "max_tokens=24).content)\n")
        watcher = threading.Thread(target=watch)
        watcher.start()
        procs = [subprocess.Popen([sys.executable, "-c", code, info.base_url],
                                  env={**os.environ, "PYTHONPATH": SRC}, stdout=subprocess.PIPE,
                                  text=True) for _ in range(2)]
        outs = [p.communicate(timeout=300)[0] for p in procs]
        stop.set()
        watcher.join()
        assert all(p.returncode == 0 for p in procs), outs
        assert Client(info.base_url).chat([{"role": "user", "content": "hi"}], max_tokens=8)
        assert max(len(row) for row in seen) <= 2
    deadline = time.monotonic() + 15
    while pid_exists(info.pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not pid_exists(info.pid), "releasing the last lease stopped the server"


def test_a_decision_model_is_served_only_through_the_broker(real, tmp_path):
    """Labelled `decision`, the smallest local model still takes a lease, and the decide
    package then asks the server that lease returned."""
    from poolhouse.decide import router
    from poolhouse.hub import kinds

    model = real.model("decide")
    assert kinds.classify(path=model, held=[model.parent]) == "decision"
    assert kinds.classify(path=model, held=[tmp_path]) != "decision", "a label needs a registry"
    with real.serving("decide", context=512) as served:
        info = served.info
        assert info.lease and pid_exists(info.pid)
        config = router.Config(backend="logprob", url=info.base_url)
        assert router.unavailable("logprob", config) == ""
        # A model this small puts too little of its probability on a valid letter (the 270M measured 0.472-0.486 on
        # llama.cpp 0.5.0), under the decider's 0.5 floor. Failing closed on such a model is the decider working as
        # designed, so the call may end either way; what this test pins is that the question reached the server this
        # lease returned, and that the only other outcome is the documented refusal.
        try:
            got = router.decide("Which letter comes first?", {}, ["a", "b"], config=config)
        except DecideError as exc:
            assert "first token was not one of the option letters" in str(exc), exc
        else:
            assert got.choice in ("a", "b") and got.backend == "logprob"
