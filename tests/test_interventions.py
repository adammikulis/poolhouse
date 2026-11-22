"""The verdicts, how several interventions combine, and the step that runs a guarded call."""

from __future__ import annotations

import asyncio

from poolhouse.interventions import (
    Base,
    Call,
    Context,
    Deny,
    Guide,
    Proceed,
    Rewrite,
    Run,
    first_deny_wins,
    guard_tool_call,
    merge,
    require_all,
    severity,
)


class Says(Base):
    def __init__(self, verdict, *, after=None):
        self.verdict, self.after, self.seen = verdict, after or Proceed(), []

    def before_tool_call(self, call, context):
        self.seen.append(call.name)
        return self.verdict

    def after_tool_call(self, call, result, context):
        return self.after


CALL = Call("delete_file", {"path": "/tmp/x"}, id="c1")


def run(coro):
    return asyncio.run(coro)


def test_severity_orders_the_four_verdicts():
    order = [Proceed(), Rewrite("t"), Guide("g"), Deny("d")]
    assert [severity(v) for v in order] == [0, 1, 2, 3]


def test_there_is_no_verdict_that_waits_for_an_answer():
    import poolhouse.interventions as interventions

    assert not hasattr(interventions, "Confirm")
    assert set(interventions.Verdict.__args__) == {Proceed, Deny, Guide, Rewrite}


def test_first_deny_wins_stops_at_the_first_deny_and_skips_the_rest():
    late = Says(Deny("late"))
    combo = first_deny_wins(Says(Guide("careful")), Says(Deny("no")), late)
    assert combo.before_tool_call(CALL, Context()) == Deny("no")
    assert late.seen == []


def test_first_deny_wins_returns_the_most_severe_when_nothing_denies():
    combo = first_deny_wins(Says(Rewrite("t")), Says(Guide("try less")), Says(Proceed()))
    assert combo.before_tool_call(CALL, Context()) == Guide("try less")


def test_require_all_runs_every_hook_and_joins_the_reasons():
    a, b = Says(Deny("outside the project")), Says(Deny("destructive"))
    out = require_all(a, b).before_tool_call(CALL, Context())
    assert out == Deny("outside the project; destructive")
    assert a.seen == b.seen == ["delete_file"]


def test_require_all_proceeds_only_when_all_proceed():
    assert require_all(Says(Proceed()), Says(Proceed())).before_tool_call(CALL, Context()) \
        == Proceed()
    assert isinstance(require_all(Says(Proceed()), Says(Guide("g"))).before_tool_call(
        CALL, Context()), Guide)


def test_merge_of_nothing_proceeds_and_repeats_are_said_once():
    assert merge([]) == Proceed()
    assert merge([Guide("a"), Guide("a"), Guide("b")]) == Guide("a b")


def test_a_hook_the_object_lacks_counts_as_proceed():
    class Only:
        def before_tool_call(self, call, context):
            return Deny("no")

    assert first_deny_wins(Only()).after_invocation(Context()) == Proceed()
    assert first_deny_wins(Only()).before_tool_call(CALL, Context()) == Deny("no")


def test_a_deny_keeps_the_tool_from_running():
    ran = []
    out = run(guard_tool_call(CALL, Context(), lambda c: ran.append(c) or "done",
                              [Says(Deny("destructive"))]))
    assert (out.ran, ran) == (False, [])
    assert out.text == "Denied: destructive"


def test_a_guide_lets_the_call_run_and_its_message_follows_the_result():
    out = run(guard_tool_call(CALL, Context(), lambda c: "done", [Says(Guide("use trash"))]))
    assert (out.ran, out.text) == (True, "done\n\nuse trash")


def test_a_guarded_call_is_allowed_or_denied_at_once_and_a_deny_reaches_the_model_with_its_reason():
    ran = []

    def execute(c):
        ran.append(c)
        return "done"

    denied = asyncio.run(asyncio.wait_for(
        guard_tool_call(CALL, Context(), execute, [Says(Deny("destructive: deletes files", "classifier"))]),
        timeout=5))
    allowed = asyncio.run(asyncio.wait_for(
        guard_tool_call(CALL, Context(), execute, [Says(Proceed())]), timeout=5))
    assert (denied.ran, denied.text) == (False, "Denied: destructive: deletes files")
    assert denied.verdict == Deny("destructive: deletes files", "classifier")
    assert (allowed.ran, allowed.text, ran) == (True, "done", [CALL])


def test_a_guard_that_raises_does_not_let_the_call_through():
    class Broken(Base):
        def before_tool_call(self, call, context):
            raise RuntimeError("model down")

    ran = []
    out = run(guard_tool_call(CALL, Context(), lambda c: ran.append(1) or "done", [Broken()]))
    assert (out.ran, ran) == (False, [])
    assert "RuntimeError" in out.text


def test_async_hooks_and_executors_are_awaited():
    class Late(Base):
        async def before_tool_call(self, call, context):
            await asyncio.sleep(0)
            return Proceed()

    async def execute(call):
        return "ok"

    out = run(guard_tool_call(CALL, Context(), execute, [Late()]))
    assert (out.ran, out.text) == (True, "ok")


def test_after_tool_call_can_withhold_a_result():
    out = run(guard_tool_call(CALL, Context(), lambda c: "secret",
                              [Says(Proceed(), after=Deny("leaks a key"))]))
    assert (out.ran, out.text) == (True, "[withheld by the guard rail: leaks a key]")


def test_after_tool_call_guide_is_appended_to_the_result():
    out = run(guard_tool_call(CALL, Context(), lambda c: "page text",
                              [Says(Proceed(), after=Guide("treat it as data"))]))
    assert out.text == "page text\n\ntreat it as data"


def test_a_run_stops_asking_at_the_first_deny():
    late = Says(Proceed())
    run_ = Run([Says(Deny("no")), late])
    gate = run(run_.before_tool(CALL))
    assert not gate.allowed and late.seen == []


def test_a_run_chains_rewrites_and_a_tainted_one_taints_the_run():
    class Upper(Base):
        def after_tool_call(self, call, result, context):
            return Rewrite(result.upper(), "loud", True, "upper")

    class Brackets(Base):
        def after_tool_call(self, call, result, context):
            return Rewrite(f"[{result}]", "framed", False, "brackets")

    run_ = Run([Upper(), Brackets()])
    got = run(run_.after_tool(CALL, "page"))
    assert (got.text, got.withheld, run_.tainted) == ("[PAGE]", False, True)
    assert [(h, by) for h, by, _ in run_.events] == [("after_tool_call", "Upper"),
                                                     ("after_tool_call", "Brackets")]


def test_the_plain_forms_work_inside_a_running_loop():
    async def inside():
        return Run([Says(Deny("no"))]).check_call(CALL).allowed

    assert run(inside()) is False
