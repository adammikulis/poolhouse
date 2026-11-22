"""The roles an agent session runs under: which tools each may call and what lets a call that acts run.

``ROLES`` is the table. ``RoleRail`` enforces the current role on every call, at once: the call runs
or is refused with the reason, and nothing waits for an answer. ``PlanLedger`` holds the steps of the
plan the agent stated, and ``validate`` checks a table.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from poolhouse import activity
from poolhouse.chatpolicy import (
    _TOOL_NAME,
    ACTING,
    APPROVE_FIRST,
    PLAN_AND_GO,
    READ,
    READ_ONLY,
    _outside_state,
)
from poolhouse.interventions import Base, Call, Context, Deny, Proceed, Verdict
from poolhouse.rules import Rules, blocked_reason, describe
from poolhouse.taint.ledger import ledger_of

__all__ = ["OWN", "ROLES", "Extension", "PlanLedger", "Role", "RoleRail", "get", "validate"]

OWN = ("ask_user", "plan", "done")
"""The loop's own tools: they speak to the conversation and change nothing."""
GPU_CLASS = frozenset(ACTING) | {"jobs_wait"}
"""Tools whose wall time counts against a role's ``max_gpu_seconds``."""
NOTHING, RULE, PLAN = "nothing", "rule", "plan"
RUNS = (NOTHING, RULE, PLAN)
"""What lets a call that acts run: ``nothing`` (the role has no such call), ``rule`` (an always rule
the owner saved covers it) or ``plan`` (a saved rule, or a step of the plan the agent stated, covers it)."""


@dataclass(frozen=True)
class Role:
    """What a session may call (``tools``, besides the loop's own) and what lets a call that acts
    run (``runs``): ``rule`` only an always rule, ``plan`` also a step of the stated plan,
    ``nothing`` for a role with nothing that acts. Any other acting call is refused at once."""

    name: str
    summary: str
    tools: frozenset[str]
    runs: str
    max_calls: int | None = None
    max_gpu_seconds: float | None = None

    @property
    def acts(self) -> bool:
        return bool(self.tools & frozenset(ACTING))


@dataclass(frozen=True)
class Extension:
    """What a feature adds to a session: ``tools`` (``(schema, callable)`` pairs), text for the
    system message from ``context``, text put in front of a session's first message from
    ``start`` (given that message), slash ``commands`` by name that answer in text (given
    the words after the command and the messages so far), the names in ``reads`` that only look (every role may
    call them), the names in ``acts`` that change something, with what each does (roles
    that act run them under the role's rules) and the names in ``checks_itself`` that change something and
    check their own limits inside the tool (only roles that act may call them; the rail adds no
    rule of its own). A name that is a protected action is refused."""

    tools: Callable[[], Sequence[tuple[dict[str, Any], Callable[..., Any]]]] = lambda: ()
    context: Callable[[], str] = lambda: ""
    start: Callable[[str], str] = lambda task: ""
    commands: Mapping[str, Callable[[str, Sequence[dict[str, Any]]], str]] = field(default_factory=dict)
    reads: frozenset[str] = frozenset()
    acts: Mapping[str, str] = field(default_factory=dict)
    checks_itself: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        bad = sorted(n for n in (*self.reads, *self.acts, *self.checks_itself) if _TOOL_NAME.search(n) or n in OWN)
        if bad:
            raise ValueError(f"extension tools {bad} are protected actions or the loop's own")

    def allowed(self, role: Role) -> frozenset[str]:
        """The extension's tool names ``role`` may call."""
        return self.reads | (frozenset(self.acts) | self.checks_itself if role.acts else frozenset())


def _table(*roles: Role) -> Mapping[str, Role]:
    return MappingProxyType({r.name: r for r in roles})


ROLES: Mapping[str, Role] = _table(
    Role(READ_ONLY, "reads only: nothing is offered that starts, stops, downloads or writes",
         frozenset(READ), NOTHING, max_calls=None, max_gpu_seconds=0.0),
    Role(APPROVE_FIRST, "reads run; a call that acts runs only under an always rule the owner saved, "
                        "and is refused at once otherwise",
         frozenset(READ) | frozenset(ACTING), RULE, max_calls=None),
    Role(PLAN_AND_GO, "reads run; a call that acts runs when the plan you stated names it or a saved "
                      "always rule covers it, and is refused at once otherwise",
         frozenset(READ) | frozenset(ACTING), PLAN, max_calls=None,
         max_gpu_seconds=None),
)
DEFAULT = APPROVE_FIRST
TASK_DEFAULT = PLAN_AND_GO


def validate(table: Mapping[str, Role] = ROLES) -> None:
    """Raise ``ValueError`` unless every role in ``table`` is sound: its tools are known
    ones, none is a protected action, and what lets its acting calls run fits its tools."""
    known = READ | frozenset(ACTING)
    for key, role in table.items():
        problems = []
        if key != role.name:
            problems.append(f"filed under {key!r}")
        if role.runs not in RUNS:
            problems.append(f"runs {role.runs!r}, not one of {RUNS}")
        if role.tools - known:
            problems.append(f"unknown tools {sorted(role.tools - known)}")
        if any(_TOOL_NAME.search(name) for name in role.tools):
            problems.append("a tool that is a protected action")
        if role.acts == (role.runs == NOTHING):
            problems.append("what lets a call run does not fit what the tools do")
        if (role.max_calls is not None and role.max_calls < 1) or (role.max_gpu_seconds or 0.0) < 0:
            problems.append("limits must be positive")
        if problems:
            raise ValueError(f"role {key}: " + "; ".join(problems))


def get(name: str) -> Role:
    """The role called ``name``; ``ValueError`` listing the roles when there is none."""
    if name not in ROLES:
        raise ValueError(f"no role {name!r}: the roles are {', '.join(ROLES)}")
    return ROLES[name]


_STEP = re.compile(r"^\W*([A-Za-z_][A-Za-z0-9_]*)")


def _leaves(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _leaves(item)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)


def _names(step: str, leaf: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(leaf)}(?!\w)", step) is not None


class PlanLedger:
    """The steps of the plans the agent stated. A step names a tool first; a call is inside
    it when the tool matches and every value the call carries appears in the step. A step
    covers one call."""

    def __init__(self) -> None:
        self.steps: list[str] = []

    def approve(self, steps: Iterable[Any]) -> None:
        self.steps += [str(s) for s in steps]

    def clear(self) -> None:
        self.steps = []

    def take(self, call: Call) -> bool:
        """Whether a stated step covers ``call``; the step is used up when it does."""
        for at, step in enumerate(self.steps):
            named = _STEP.match(step)
            if named and named.group(1) == call.name and call.arguments is not None \
                    and all(_names(step, leaf) for leaf in _leaves(call.arguments)):
                del self.steps[at]
                return True
        return False


class RoleRail(Base):
    """Holds each call to the current ``role``, deciding at once: a tool outside it is denied; one
    that acts runs when a saved always rule covers it or (``plan-and-go``) a step of the stated
    plan names it, and is denied with the reason otherwise; a call that names a path outside
    poolhouse's state is denied whatever the plan says, and a plan stated after the run read
    outside text covers nothing (the chat never records one), so only a plan stated before it
    does. The role changes only through ``set``, which the model has no tool to reach."""

    name = "role"

    def __init__(self, role: Role, offered: Callable[[], set[str]],
                 plan: PlanLedger | None = None, *, extension: Extension | None = None,
                 rules: Rules | None = None) -> None:
        self.role, self.offered = role, offered
        self.plan = plan or PlanLedger()
        self.extension = extension or Extension()
        self.rules = rules
        self.gpu_seconds = 0.0

    def set(self, role: Role) -> None:
        self.role, self.gpu_seconds = role, 0.0
        self.plan.clear()

    def spent(self, name: str, seconds: float) -> None:
        """Count ``seconds`` of ``name`` against the GPU-time limit."""
        if name in GPU_CLASS:
            self.gpu_seconds += seconds

    def before_tool_call(self, call: Call, context: Context) -> Verdict:
        role = self.role
        if call.name in OWN:
            return Proceed() if call.name in self.offered() else Deny(
                f"{call.name} is not a tool the {role.name} role offers", self.name)
        ext = self.extension
        if call.name not in role.tools | ext.allowed(role) or call.name not in self.offered():
            return Deny(f"{call.name} is not a tool the {role.name} role offers", self.name)
        if call.name in READ or call.name in ext.reads or call.name in ext.checks_itself:
            return Proceed()
        does = ACTING.get(call.name) or ext.acts[call.name]
        if role.max_gpu_seconds is not None and self.gpu_seconds >= role.max_gpu_seconds \
                and call.name in GPU_CLASS:
            return Deny(f"the {role.name} role has used its {role.max_gpu_seconds:.0f} s "
                        f"of GPU time", self.name)
        args = call.arguments or {}
        outside = _outside_state(args)
        tainted = self.read_outside_text(context)
        rule = self.rules.covers(call.name, call.arguments, role.name, tainted) \
            if self.rules else None
        if rule is not None and (rule.verdict == "never" or not outside):
            activity.record("approval.rule_fired", subject=call.name, outcome=rule.verdict,
                            refs={"role": role.name}, meta={"rule": describe(rule), "outside": bool(outside)})
        if rule is not None and rule.verdict == "never":
            self.rules.fire(rule)
            return Deny(f"a rule you set says: {describe(rule)}", self.name)
        if rule is not None and not outside:
            self.rules.fire(rule)
            return Proceed()
        head = f"{call.name} would {does}, and is refused: "
        if outside:
            return Deny(f"{head}it names paths outside poolhouse's state: {', '.join(outside[:3])}",
                        self.name)
        if role.runs == PLAN:
            if self.plan.take(call):
                return Proceed()
            if tainted:
                return Deny(f"{head}the run has read text from outside, and no plan stated before it "
                            f"names this call (a plan made after outside text covers nothing)", self.name)
            return Deny(f"{head}it is not in the plan you stated (a step names the tool first and "
                        f"every value the call carries)", self.name)
        if call.name in ext.acts:
            why = " (an extension's tool: no rule can cover it, so only the plan-and-go role runs it)"
        elif blocked_reason(call.name, args):
            why = f" ({blocked_reason(call.name, args)}, so no rule can cover it)"
        elif tainted:
            why = (" (the run has read text from outside, which only a rule set to apply after "
                   "outside text covers)")
        else:
            why = ""
        return Deny(f"{head}the {role.name} role runs a call that acts only under an always rule "
                    f"the owner saved with /rules{why}", self.name)

    @staticmethod
    def read_outside_text(context: Context) -> bool:
        return context.tainted or ledger_of(context.notes).contaminated
