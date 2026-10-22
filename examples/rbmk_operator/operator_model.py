"""The operator: a poolhouse decision model asked named questions about the console.

There is one source of answers, the released checkpoint through `poolhouse.decide`, held under a GPU
hold at the Broker. A failed, missing, oversized or malformed answer raises `ModelStopped`; nothing
substitutes for it. The decision is hierarchical: an action class, then a target chosen from a named
list built from the live state, then a step size. Each question reads the sub-panel it concerns.
"""

from __future__ import annotations

import concurrent.futures
import math
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import controls, panel

ABSTAIN_BELOW = 0.40
CALL_TIMEOUT_S = 120.0
TOKEN_LIMIT = 4096
STEP_TEXT = ("How large a step: 1 is small (a tenth of the stroke), 2 is medium (a quarter), 3 is large (half)?")
ROD_CLASSES = ("insert_rod", "withdraw_rod")


class ModelStopped(RuntimeError):
    """The decision model could not answer; the run stops and says why."""


class Asker(Protocol):
    """Anything that answers one call of named questions about one text."""

    model: str

    def ask(self, index: int, key: str, text: str, qs: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]: ...


@dataclass
class Proposal:
    """What the operator asked for at one decision, before the interlocks see it."""

    cls: str
    target: str
    step: str
    source: str                       # model | safety-policy | naive-rule | crew-history
    model_choice: dict[str, str] | None
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    note: str = ""


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
        item = raw[name].public()
        item["question"] = q.text
        item["options"] = {o.name: o.description for o in q.options}
        check(name, item, [o.name for o in q.options])
        out[name] = item
    return out


class LiveAsker:
    """Asks the released decision checkpoint, one forward pass per question, with a timeout on every call."""

    def __init__(self, pointer: str = "", abstain_below: float = ABSTAIN_BELOW, verify: bool = True,
                 timeout: float = CALL_TIMEOUT_S) -> None:
        from poolhouse.decide import router

        self.abstain_below, self.timeout = abstain_below, timeout
        self.config = router.Config(backend="pointer", pointer=pointer)
        self.model = ""
        why = router.unavailable("pointer", self.config) if verify else ""
        if why:
            raise ModelStopped(f"the decision model is not available: {why}")

    def _tokens(self, text: str, qs: dict[str, Any]) -> dict[str, int]:
        """The decider's own token count of each question's prompt."""
        from poolhouse.decide import pointer_prompt, router

        tok = router.build("pointer", self.config)._loaded[0]
        shown = _questions().prepare(text)
        return {name: len(tok(pointer_prompt.render(q.text, shown, q.options).text,
                              add_special_tokens=False)["input_ids"]) for name, q in qs.items()}

    def ask(self, index: int, key: str, text: str, qs: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Answers for every question in ``qs`` about ``text``; raises `ModelStopped` on any failure or hang."""
        sent = time.time()
        got = call_with_timeout(lambda: _questions().decide(text, qs, config=self.config,
                                                            abstain_below=self.abstain_below), self.timeout, key)
        answered = time.time()
        answers = annotated(got, qs)
        ids = {a["model"] for a in answers.values()}
        if len(ids) != 1:
            raise ModelStopped(f"the answers came from different models: {sorted(ids)}")
        self.model = ids.pop()
        for name, n in self._tokens(text, qs).items():
            if n > TOKEN_LIMIT:
                raise ModelStopped(f"question {name!r} prompt is {n} tokens; the limit is {TOKEN_LIMIT}")
            answers[name]["tokens"] = n
        return answers, {"sent_at": round(sent, 3), "answered_at": round(answered, 3)}


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


def _questions() -> Any:
    from poolhouse.decide import questions

    return questions


def situational(plant: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """The sub-panel reading questions: each is asked about the panel it concerns."""
    q = _questions()
    return [
        ("neutron", panel.neutron(plant), {"falling": q.noul("Is reactor power falling dangerously?")}),
        ("coolant", panel.coolant(plant), {"void": q.noul("Is steam void feedback pushing reactor power up?")}),
        ("rods", panel.rods(plant), {"orm_low": q.noul("Is the operational reactivity margin below the 15 rod minimum?")}),
        ("steam", panel.steam(plant), {"steam": q.noul("Is a drum level or pressure reading close to its trip limit?")}),
        ("protection", panel.protections(plant), {"bypass": q.noul("Is any safety system currently defeated?")}),
        ("instruments", panel.full(plant), {"inconsistent": q.noul("Do any two instrument readings contradict each other?")}),
    ]


class ModelOperator:
    """The decision-model controller: class, then target, then step; an abstained answer holds."""

    kind = "model"

    def __init__(self, asker: Asker) -> None:
        self.asker = asker

    def _call(self, index: int, key: str, text: str, qs: dict[str, Any], prop: Proposal) -> dict[str, dict[str, Any]]:
        got, timing = self.asker.ask(index, key, text, qs)
        latency = sum(item["latency_ms"] for item in got.values())
        prop.calls.append({"key": key, "state_text": text, "answers": got, "latency_ms": round(latency, 3), **timing})
        for name, item in got.items():
            prop.answers[name] = {**item, "call": key}
        prop.latency_ms += latency
        return got

    def _hold(self, prop: Proposal, name: str, ans: dict[str, Any]) -> Proposal:
        top = ans["scores"][ans["choice"]]
        prop.cls, prop.target, prop.source = "hold", controls.NONE, "safety-policy"
        prop.note = (f"ABSTAINED on {name}: best guess {ans['choice']} had probability {top:.2f}, under the abstain "
                     f"floor. Fixed safety policy: hold and flag a person.")
        return prop

    def propose(self, index: int, plant: Any) -> Proposal:
        """The (class, target, step) the model's answers ask for, or the fixed safety policy on abstain."""
        q = _questions()
        prop = Proposal("hold", controls.NONE, "1", "model", None)
        for key, text, qs in situational(plant):
            self._call(index, key, text, qs, prop)
        full = panel.full(plant)
        got = self._call(index, "decision", full, {
            "risk": q.score("How risky is the current reactor state, from 1 (safe) to 5 (about to be damaged)?", 1, 5),
            "class": q.choice("Which kind of operator action should be taken now?", controls.CLASSES)}, prop)
        cls = got["class"]
        if cls["abstained"]:
            return self._hold(prop, "class", cls)
        prop.cls = cls["choice"]
        if prop.cls == "orm_print":
            plant.print_orm()
            again = self._call(index, "decision_after_printout", panel.full(plant), {
                "class_after_printout": q.choice("The margin printout is now fresh. Which kind of operator action "
                                                 "should be taken now?", controls.CLASSES)}, prop)["class_after_printout"]
            if again["abstained"]:
                return self._hold(prop, "class_after_printout", again)
            prop.cls = again["choice"]
            prop.note = "The model asked for a margin printout first, then chose again with the fresh printout."
        prop.model_choice = {"cls": prop.cls, "target": controls.NONE, "step": "1"}
        if prop.cls == "orm_print":
            prop.cls = "hold"
            prop.note += " It asked for a printout again, which it already had: the plant holds."
            return prop
        if prop.cls in ("hold", "scram"):
            return prop
        options = controls.candidates(prop.cls, plant)
        tgt = self._call(index, "target", full + f" The operator is considering: {controls.CLASSES[prop.cls]}.",
                         {"target": q.choice("Which target should that action be applied to?", options)}, prop)["target"]
        if tgt["abstained"]:
            return self._hold(prop, "target", tgt)
        prop.target = prop.model_choice["target"] = tgt["choice"]
        if prop.cls in ROD_CLASSES and prop.target != controls.NONE:
            step = self._call(index, "step", full + f" The operator will {controls.CLASSES[prop.cls]}: {options[prop.target]}.",
                              {"step": q.score(STEP_TEXT, 1, 3)}, prop)["step"]
            if step["abstained"]:
                prop.step, prop.source = "1", "model"
                prop.note = (f"The step size was uncertain (best guess {step['choice']} at "
                             f"{step['scores'][step['choice']]:.2f}); the smallest step was applied.")
            else:
                prop.step = step["choice"]
            prop.model_choice["step"] = step["choice"]
        return prop


class NaiveOperator:
    """A baseline rule, not a model: chase the demand with the best manual rod using the average reading."""

    kind = "naive"

    def propose(self, index: int, plant: Any) -> Proposal:
        """Withdraw a rod when the average reading is under demand, insert when over, else hold."""
        want, got = plant.setpoint, sum(plant.reading) / 2
        cls = "withdraw_rod" if got < want - 0.03 else "insert_rod" if got > want + 0.03 else "hold"
        target = next(iter(controls.candidates(cls, plant))) if cls != "hold" else controls.NONE
        return Proposal(cls, target, "2", "naive-rule", None, note=f"average reading {got:.2f} against demand {want:.2f}")


class CrewOperator:
    """The 1986 crew as a script: pull the best manual rod out until the margin is near 7, then AZ-5 36 s into the test."""

    kind = "crew"

    def __init__(self) -> None:
        self.test_tick: int | None = None

    def propose(self, index: int, plant: Any) -> Proposal:
        """Withdraw rods one by one until the margin is about 7, hold, then scram 36 s after the test starts."""
        if plant.test == "running":
            self.test_tick = plant.tick if self.test_tick is None else self.test_tick
            if plant.tick - self.test_tick >= 36:
                return Proposal("scram", controls.NONE, "1", "crew-history", None, note="AZ-5 pressed at 01:23:40")
        elif plant.tick < 40 and plant.core.orm() > 5.5:
            options = [k for k in controls.candidates("withdraw_rod", plant) if k.startswith("R-")]
            return Proposal("withdraw_rod", options[0], "2", "crew-history", None,
                            note="the crew pulls another manual rod; the automatic regulator holds the power")
        return Proposal("hold", controls.NONE, "1", "crew-history", None, note="continuing the programme")
