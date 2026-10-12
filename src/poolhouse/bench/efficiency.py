"""Accuracy per generated token: the rates of one run and how models compare on them.

Generated tokens are the completion tokens a server reports, the thinking a model does
before it answers included (llama-server counts every token it decodes, and an OpenAI-style
server counts reasoning inside its completion total). A run whose rows carry no completion
count has no generated figures at all: absent, never 0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["HALF_THE_TOKENS", "HEAD", "RULE", "SAME_AND_HALF", "Relative", "carried",
           "counted", "note", "relative", "row_cells", "with_rates"]

HALF_THE_TOKENS = 0.5
SAME_AND_HALF = "same accuracy, half the tokens"
HEAD = "| gen/q | acc vs best | tok vs best | verdict "
RULE = "| --- | --- | --- | --- "
_EDGE = 1e-9


def counted(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """``generated_tokens``: the rows' completion tokens summed, or nothing when no row
    reported any."""
    total = sum(float(r.get("completion_tokens") or 0) for r in rows)
    return {"generated_tokens": total} if total > 0 else {}


def carried(one: Mapping[str, float]) -> dict[str, float]:
    """The generated-token total of ``one`` when it has one, for a point built from it."""
    return {"generated_tokens": one["generated_tokens"]} if "generated_tokens" in one else {}


def with_rates(out: dict[str, float]) -> dict[str, float]:
    """The rates, from the totals. Guarded: a run that took no time or paid nothing has
    nothing to divide by, and a zero score is a real answer rather than a missing one."""
    got, seconds, paid, memory = out["right"], out["seconds"], out["paid_tokens"], out["kv_bytes"]
    questions = out.get("questions") or 0.0
    generated = out.get("generated_tokens")
    if questions > 0:
        out.setdefault("seconds_per_question", seconds / questions)
        out["tokens_per_question"] = paid / questions
        out["calls_per_question"] = out.get("calls", 0.0) / questions
        if generated:
            out["generated_per_question"] = generated / questions
    if seconds > 0:
        out["right_per_minute"] = got * 60.0 / seconds
    if paid > 0:
        out["right_per_1k"] = got * 1000.0 / paid
    if generated:
        out["right_per_1k_generated"] = got * 1000.0 / generated
    if memory > 0:
        out["right_per_gb"] = got / (memory / 2**30)
    if got > 0:
        out["seconds_per_right"] = seconds / (got * out["questions"])
        out["tokens_per_right"] = paid / (got * out["questions"])
        if generated:
            out["generated_per_right"] = generated / (got * out["questions"])
    return out


@dataclass(frozen=True)
class Relative:
    """One model's accuracy and generated tokens as shares of the most accurate model's."""

    model: str
    generated_per_question: float
    accuracy_share: float
    tokens_share: float
    reference: bool
    efficient: bool
    verdict: str

    def cells(self) -> list[str]:
        """The four table cells: generated per question, accuracy share, tokens share, and
        the plain words (reference, the verdict, efficient)."""
        words = [w for w, on in (("reference", self.reference), (self.verdict, self.verdict),
                                 ("efficient", self.efficient)) if on]
        return [f"{self.generated_per_question:.0f}", f"{100 * self.accuracy_share:.0f}%",
                f"{100 * self.tokens_share:.0f}%", ", ".join(words) or "-"]


def relative(points: Mapping[str, Mapping[str, float]], *, noise: float) -> dict[str, Relative]:
    """Each model with a generated count against the most accurate one (a tie goes to the
    fewer tokens).

    The verdict `SAME_AND_HALF` is a model other than the reference whose F1 is within
    ``noise`` of it and whose generated tokens per question are half the reference's or
    fewer. ``efficient`` is a model no other beats on both accuracy and generated tokens.
    """
    have = {m: (float(d["right"]), float(d["generated_per_question"]))
            for m, d in points.items()
            if d.get("right") is not None and d.get("generated_per_question")}
    if not have:
        return {}
    best = min(have, key=lambda m: (-have[m][0], have[m][1]))
    top, base = have[best]
    out = {}
    for model, (right, tokens) in have.items():
        share = tokens / base
        beaten = any(r >= right and t <= tokens and (r > right or t < tokens)
                     for r, t in have.values())
        said = (SAME_AND_HALF if model != best and top - right <= noise + _EDGE
                and share <= HALF_THE_TOKENS + _EDGE else "")
        out[model] = Relative(model, tokens, right / top if top else 0.0, share,
                              model == best, not beaten, said)
    return out


def row_cells(table: Mapping[str, Relative], model: str) -> str:
    """``model``'s four table cells as a markdown row segment, dashes when it has none."""
    one = table.get(model)
    return "| " + " | ".join(one.cells() if one else ["-"] * 4) + " "


def note(table: Mapping[str, Relative], noise: float) -> list[str]:
    """The sentence under the ranking that says what the generated-token columns mean."""
    if not table:
        return []
    return ["", "Generated tokens are the completion tokens each model's most accurate run "
                "wrote, thinking included, per question. The shares are against the most "
                f"accurate model; `{SAME_AND_HALF}` is F1 within {noise * 100:g} points of it "
                "at half its generated tokens or fewer; `efficient` is on the frontier of "
                "F1 against generated tokens. `-` is a run that recorded no completion "
                "count."]
