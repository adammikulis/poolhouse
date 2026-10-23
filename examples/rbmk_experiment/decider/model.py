"""The decision model: questions asked of the released checkpoint through `poolhouse.decide`, answers checked.

The answer is always the most probable option. A failed, missing, oversized or malformed answer raises `ModelStopped`.
"""

from __future__ import annotations

import concurrent.futures
import math
import time
from typing import Any, Protocol

CALL_TIMEOUT_S = 120.0
KEPT = ("name", "kind", "choice", "scores", "certainty", "error", "p_true", "expected", "backend", "model", "latency_ms")


class ModelStopped(RuntimeError):
    """The decision model could not answer; the run stops and says why."""


class Asker(Protocol):
    """Anything that answers calls of named questions."""

    model: str

    def ask_batch(self, index: int, calls: list[tuple[str, str, dict[str, Any]]]
                  ) -> list[tuple[dict[str, dict[str, Any]], dict[str, Any]]]: ...


def check(name: str, answer: dict[str, Any], want: list[str]) -> None:
    """Refuse an answer that failed or is not a probability distribution over its options."""
    if answer.get("error"):
        raise ModelStopped(f"question {name!r} failed: {answer['error']}")
    scores = answer.get("scores") or {}
    if sorted(scores) != sorted(want):
        raise ModelStopped(f"question {name!r} returned options {sorted(scores)}, expected {sorted(want)}")
    values = list(scores.values())
    if not all(isinstance(v, float | int) and math.isfinite(v) and v >= 0 for v in values):
        raise ModelStopped(f"question {name!r} returned a probability that is not a finite number")
    if abs(sum(values) - 1.0) > 1e-6:
        raise ModelStopped(f"question {name!r} probabilities sum to {sum(values)}, not 1")
    if answer.get("choice") not in scores:
        raise ModelStopped(f"question {name!r} has no choice among its options")
    if not answer.get("model"):
        raise ModelStopped(f"question {name!r} came back without a model id")


def annotated(raw: dict[str, Any], qs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each `Answer` as a checked, JSON-ready dict that also carries its question text and option text."""
    out: dict[str, dict[str, Any]] = {}
    for name, q in qs.items():
        if name not in raw:
            raise ModelStopped(f"no answer for question {name!r}")
        public = raw[name].public()
        item = {k: public[k] for k in KEPT}
        item["question"] = q.text
        item["options"] = {o.name: o.description for o in q.options}
        check(name, item, [o.name for o in q.options])
        out[name] = item
    return out


def call_with_timeout(fn: Any, timeout: float, what: str) -> Any:
    """Run ``fn`` and wait at most ``timeout`` seconds; a failure or a hang is a `ModelStopped`."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        raise ModelStopped(f"the model call {what!r} did not answer within {timeout:.0f} s") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise ModelStopped(f"{type(exc).__name__}: {exc}") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class LiveAsker:
    """Asks the released decision checkpoint, with a timeout on every call."""

    def __init__(self, pointer: str = "", verify: bool = True, timeout: float = CALL_TIMEOUT_S) -> None:
        from poolhouse.decide import router

        self.timeout = timeout
        self.config = router.Config(backend="pointer", pointer=pointer)
        self.model = ""
        why = router.unavailable("pointer", self.config) if verify else ""
        if why:
            raise ModelStopped(f"the decision model is not available: {why}")

    def warm(self) -> float:
        """Load the weights with one throwaway question so that load time is not operator delay; returns seconds."""
        from poolhouse.decide import questions as q

        began = time.perf_counter()
        call_with_timeout(lambda: q.decide("warm-up", {"w": q.noul("Is this a warm-up?")}, config=self.config), 600.0, "warm-up")
        return time.perf_counter() - began

    def _tokens(self, text: str, qs: dict[str, Any]) -> dict[str, int]:
        from poolhouse.decide import pointer_prompt, questions, router

        tok = router.build("pointer", self.config)._loaded[0]
        shown = questions.prepare(text)
        return {name: len(tok(pointer_prompt.render(q.text, shown, q.options).text, add_special_tokens=False)["input_ids"])
                for name, q in qs.items()}

    def ask_batch(self, index: int, calls: list[tuple[str, str, dict[str, Any]]]
                  ) -> list[tuple[dict[str, dict[str, Any]], dict[str, Any]]]:
        """Answers for every ``(key, text, questions)`` call in one batched request; any failure or hang is `ModelStopped`."""
        from poolhouse.decide import questions

        sent = time.time()
        got = call_with_timeout(lambda: questions.decide_batch([(text, qs) for _, text, qs in calls], config=self.config),
                                self.timeout, "+".join(key for key, _, _ in calls))
        answered = time.time()
        out = []
        for (_, text, qs), raw in zip(calls, got, strict=True):
            answers = annotated(raw, qs)
            ids = {a["model"] for a in answers.values()}
            if len(ids) != 1:
                raise ModelStopped(f"the answers came from different models: {sorted(ids)}")
            self.model = ids.pop()
            for name, n in self._tokens(text, qs).items():
                answers[name]["tokens"] = n
            out.append((answers, {"sent_at": round(sent, 3), "answered_at": round(answered, 3),
                                  "batch_size": sum(len(qs) for _, _, qs in calls)}))
        return out
