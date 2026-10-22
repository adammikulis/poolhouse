"""The recording: what the model said and when its answers landed, bound to the run it produced by a digest.

Events only. A recording holds no caption, blurb, label, verdict or score. `replay` re-runs the simulator and takes every model
answer from the recording; the console text of each call is checked by hash and the options (names and descriptions, in the
logged order) by value, so a recording that no longer matches the prompt builder is refused.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .decider.model import ModelStopped
from .decider.policy import ModelDecider
from .runner import Result, Runner, RunOptions
from .scenarios import Scenario

RECORDING = "rbmk-recording/2"


def dumps(doc: Any) -> str:
    """Stable JSON text."""
    return json.dumps(doc, indent=1)


def sha(text: str) -> str:
    """SHA-256 of a console text."""
    return hashlib.sha256(text.encode()).hexdigest()


def digest(res: Result) -> str:
    """The digest of a run: its true trace and its rows."""
    return sha(dumps({"trace": res.trace, "rows": res.rows}))


def recording_of(res: Result, scn: Scenario, backend: dict[str, Any], clocks: dict[str, Any]) -> dict[str, Any]:
    """The recording of a run: the model's answers with their timing and landing times, and the digest of the run."""
    calls = [{"n": d["n"], "tick": d["tick"], "due_s": d["due_s"],
              "calls": [{"key": c["key"], "state_sha256": sha(c["state_text"]), "answers": c["answers"],
                         "latency_ms": c["latency_ms"], "sent_at": c["sent_at"], "answered_at": c["answered_at"],
                         "batch_size": c.get("batch_size", 0)} for c in d["calls"]]}
             for d in res.decisions]
    return {"schema": RECORDING,
            "meta": {"scenario": scn.key, "seed": scn.seed, "controller": res.controller, "memory": res.memory,
                     "speed": res.speed, "backend": backend, "clocks": clocks, "finished": res.finished},
            "digest": digest(res), "decisions": calls}


class ReplayMismatch(ModelStopped):
    """A recording does not match the simulator or the questions asked."""


class ReplayAsker:
    """Answers from a recording; any difference in the console text or the options asked is an error."""

    def __init__(self, rec: dict[str, Any]) -> None:
        if rec.get("schema") != RECORDING:
            raise ReplayMismatch(f"not a {RECORDING} document")
        self.model = rec["meta"]["backend"]["model"]
        self.calls = {d["n"]: {c["key"]: c for c in d["calls"]} for d in rec["decisions"]}
        self.applied = {d["n"]: d["due_s"] for d in rec["decisions"]}

    def landing(self, n: int) -> float | None:
        """The simulated time the recorded run applied request ``n``."""
        return self.applied.get(n)

    def ask_batch(self, index: int, calls: list[tuple[str, str, dict[str, Any]]]
                  ) -> list[tuple[dict[str, dict[str, Any]], dict[str, Any]]]:
        """The recorded answers and timing for these calls, each checked against what is being asked now."""
        out = []
        for key, text, qs in calls:
            call = self.calls.get(index, {}).get(key)
            if call is None:
                raise ReplayMismatch(f"the recording has no call {key!r} for request {index}")
            if call["state_sha256"] != sha(text):
                raise ReplayMismatch(f"request {index} call {key!r}: the console text differs from the recording")
            for name, q in qs.items():
                got = call["answers"].get(name)
                if got is None or got["question"] != q.text or got["options"] != {o.name: o.description for o in q.options} \
                        or list(got["options"]) != [o.name for o in q.options]:
                    raise ReplayMismatch(f"request {index} call {key!r}: question {name!r} differs from the recording")
            out.append(({name: call["answers"][name] for name in qs},
                        {"sent_at": call["sent_at"], "answered_at": call["answered_at"], "batch_size": call.get("batch_size", 0)}))
        return out


def replay(rec: dict[str, Any], scn: Scenario) -> Result:
    """Re-run the simulator, taking every model answer from the recording; the digest must match the recording's."""
    asker = ReplayAsker(rec)
    meta = rec["meta"]
    runner = Runner(scn, ModelDecider(asker, scn.seed), RunOptions(meta["speed"], meta["memory"], asker.landing))
    res = runner.run()
    if digest(res) != rec["digest"]:
        raise ReplayMismatch("the replay differs from the run the recording was made from")
    return res
