"""Bounded typed questions to an explicitly owned loopback System One server."""

from __future__ import annotations

import ipaddress
import json
import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from poolhouse.http import ONCE, Options, ServerError, open_stream
from poolhouse.json_objects import unique_object


class SystemOneError(ValueError):
    """A System One request or answer failed validation or delivery."""


MAX_BYTES = 1_048_576
MAX_QUESTIONS = 256


def _constant(_value: str) -> None:
    raise SystemOneError("System One JSON must contain finite numbers")


def loads(raw: str | bytes) -> dict[str, Any]:
    """Parse a bounded strict JSON object."""
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_BYTES:
        raise SystemOneError("System One JSON exceeds the byte limit")
    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=_constant)
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SystemOneError("System One JSON is invalid") from exc
    if not isinstance(value, dict):
        raise SystemOneError("System One JSON must be an object")
    return value


def _number(value: Any, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SystemOneError("System One answer has an invalid number")
    if not lo <= value <= hi or not math.isfinite(value):
        raise SystemOneError("System One answer number is outside its bounds")
    return float(value)


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise SystemOneError("System One names must contain 1 to 200 characters")
    return value


def _content(value: Any) -> None:
    if not isinstance(value, (str, dict, list)):
        raise SystemOneError("System One content must be text, an object or an array")


def _question(value: Any) -> None:
    if not isinstance(value, dict) or set(value) - {"type", "instructions", "criteria"}:
        raise SystemOneError("System One question fields are invalid")
    _content(value.get("instructions"))
    kind, criteria = value.get("type"), value.get("criteria")
    if kind == "choice":
        if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
            raise SystemOneError("System One choice needs 2 to 255 options")
        for key, content in criteria.items():
            _name(key)
            if content is not None:
                _content(content)
    elif kind == "score":
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
            raise SystemOneError("System One score needs 2 to 10 levels")
        if any(not isinstance(content, str) for content in criteria):
            raise SystemOneError("System One score levels must be strings")
    elif kind == "noul":
        if criteria is not None:
            if not isinstance(criteria, dict) or set(criteria) - {"true", "false"}:
                raise SystemOneError("System One noul criteria are invalid")
            for content in criteria.values():
                if content is not None:
                    _content(content)
    else:
        raise SystemOneError("System One question type is unsupported")


def _distribution(value: Any, keys: set[str]) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SystemOneError("System One probability keys differ from the offered options")
    probs = {key: _number(p, 0, 1) for key, p in value.items()}
    if not math.isclose(sum(probs.values()), 1, abs_tol=max(1e-5, len(probs) * 0.000051)):
        raise SystemOneError("System One probabilities do not sum to one")
    return probs


def _answer(answer: Any, question: dict[str, Any]) -> None:
    kind = question["type"]
    if not isinstance(answer, dict) or answer.get("type") != kind:
        raise SystemOneError("System One answer type differs from its question")
    allowed = {"type", "noul"} if kind == "noul" else {"type", "probabilities", "confidence"}
    allowed |= {"choice"} if kind == "choice" else {"score", "legend"} if kind == "score" else set()
    if set(answer) - allowed:
        raise SystemOneError("System One answer fields are unsupported")
    if kind == "noul":
        _number(answer.get("noul"), 0, 1)
        return
    criteria = question["criteria"]
    keys = set(criteria) if kind == "choice" else {str(i) for i in range(len(criteria))}
    probs = _distribution(answer.get("probabilities"), keys)
    confidence = _number(answer.get("confidence"), 0, 1)
    if kind == "choice":
        chosen = answer.get("choice")
        if not isinstance(chosen, str) or chosen not in probs or probs[chosen] != max(probs.values()):
            raise SystemOneError("System One choice is not a highest-probability option")
        expected = max(0, min(1, (len(probs) * max(probs.values()) - 1) / (len(probs) - 1)))
        if not math.isclose(confidence, expected, abs_tol=0.0002):
            raise SystemOneError("System One choice confidence differs from its distribution")
    else:
        score = _number(answer.get("score"), 0, len(criteria) - 1)
        expected = sum(int(key) * value for key, value in probs.items())
        if not math.isclose(score, expected, abs_tol=len(criteria) ** 2 * 0.000051):
            raise SystemOneError("System One score differs from its distribution")
        if "legend" in answer and answer["legend"] != {str(i): v for i, v in enumerate(criteria)}:
            raise SystemOneError("System One score legend differs from its criteria")


def _endpoint(url: str) -> str:
    try:
        parsed = urlsplit(url)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise SystemOneError("System One needs a literal loopback server URL") from exc
    if (parsed.scheme not in {"http", "https"} or not address.is_loopback
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or port is None):
        raise SystemOneError("System One needs a literal loopback base URL with an explicit port")
    return url.rstrip("/") + "/v1/systemone"


@dataclass(frozen=True, slots=True)
class SystemOne:
    """A passive client; the caller owns the external server and coordinates its compute."""

    url: str
    model: str
    timeout: float = 10.0

    def __post_init__(self) -> None:
        _endpoint(self.url)
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 512:
            raise SystemOneError("System One needs an explicit expected model identity")
        _number(self.timeout, 0.001, 60)

    def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        """Send one state and all named questions; validate the complete answer batch."""
        _content(state)
        if not isinstance(questions, dict) or not 1 <= len(questions) <= MAX_QUESTIONS:
            raise SystemOneError("System One needs 1 to 256 named questions")
        for name, question in questions.items():
            _name(name)
            _question(question)
        try:
            raw = json.dumps({"state": state, "questions": questions, "model": self.model},
                             allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError) as exc:
            raise SystemOneError("System One request must contain finite JSON values") from exc
        body = loads(raw)
        endpoint, visits = _endpoint(self.url), 0

        def guard(target: str) -> str:
            nonlocal visits
            visits += 1
            if visits != 1 or target != endpoint:
                raise SystemOneError("System One redirects are refused")
            return target

        try:
            with open_stream(endpoint, data=raw, method="POST", options=Options(
                    headers={"Content-Type": "application/json"}, timeout=self.timeout,
                    retry=ONCE, error_body_limit=MAX_BYTES, guard=guard)) as response:
                got = loads(response.read(MAX_BYTES + 1))
        except SystemOneError:
            raise
        except (ServerError, OSError, ValueError) as exc:
            raise SystemOneError("System One server request failed") from exc
        if set(got) - {"model", "answers", "usage", "latency_ms"}:
            raise SystemOneError("System One response fields are unsupported")
        if got.get("model") != self.model:
            raise SystemOneError("System One server returned a different model identity")
        answers = got.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(body["questions"]):
            raise SystemOneError("System One answer IDs differ from the requested IDs")
        for name, question in body["questions"].items():
            _answer(answers[name], question)
        if "latency_ms" in got:
            _number(got["latency_ms"], 0, 3_600_000)
        return got
