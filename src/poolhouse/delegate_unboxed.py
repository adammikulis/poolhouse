"""A delegate on a computer that cannot sandbox it: OpenCode on the folder itself, under the tier the lead chose.

Every tier has OpenCode's own permission rules (`read-only` denies the shell and editing) and the plugin that asks Poolhouse's hook before each tool
call (`poolhouse.delegate_guard`): the deterministic blocks and, below `yolo`, the classifier of the tier. Its home and configuration are a folder
of its own, so nothing of the user's is read as its settings or written. The model is reached through the same loopback bridge, behind a token of
this run, as in a sandbox, so the server's key stays in this process. None of this is a boundary: a program the model writes or runs reaches
whatever the account reaches, and only a sandbox stops that.

A self-check (`poolhouse.delegate_review`) runs each review and fix the same way, in a home of its own. A review only reads, and for a delegate that
edits it reads a copy of the folder with each instruction and settings file as it was before the work. What the work changed is in the folder at
once and nothing here can hold it back: a change the self-check does not accept stays there, and a patch of it is kept in the delegate's folder.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from poolhouse import (
    coding_policy,
    delegate_candidate,
    delegate_confined,
    delegate_guard,
    delegate_recorder,
    delegate_refusals,
    delegate_resume,
    delegate_review,
    delegate_store as store,
    harness,
    harnessing,
    home,
    inplace,
    isolation,
    lead_mcp,
    opencode,
    opencode_install,
    runtime,
    serverkeys,
)
from poolhouse.client import families
from poolhouse.errors import Denied
from poolhouse.isolation.environment import OPENCODE_OFFLINE, SHELL, SHELL_DEFAULT_S, SHELL_MAX_S
from poolhouse.log import say as log
from poolhouse.workspace import nudge_fence

__all__ = ["KEPT", "converse", "environment", "warning"]

KEPT = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT")
"""The only variables of the starting process a delegate without a sandbox is given: none that carries a key or a login."""
NOT_BOXED = ("A program it writes or runs reaches everything your account can, including your files, your keys and the network; Poolhouse's blocks "
             "refuse common harmful calls and are not a boundary. Only a sandbox stops that.")


def warning(tier: str, why: str) -> str:
    """What the lead is told when a delegate of ``tier`` starts on a computer that cannot sandbox it (``why``)."""
    if tier == coding_policy.READ_ONLY:
        return f"this computer cannot sandbox the delegate ({why}); it reads the folder itself, with no shell and no editing"
    does = ("edits files and runs the shell commands the guarded profile allows" if tier == coding_policy.GUARDED
            else "edits files and runs any shell command with nothing judging it")
    return f"warning: this computer cannot sandbox the delegate ({why}), so it works on the folder itself and {does}. {NOT_BOXED}"


def environment(own: Path, binary: str) -> dict[str, str]:
    """The delegate's whole environment: the system's own names, a home, temporary folder and configuration of its own under ``own``, and the
    folder of ``binary`` first on PATH (the ripgrep kept beside it is the one the search tools run)."""
    env = {name: os.environ[name] for name in KEPT if os.environ.get(name)}
    config, scratch = own / "config", str(own / "tmp")
    env["PATH"] = os.pathsep.join(each for each in (str(Path(binary).parent), env.get("PATH", "")) if each)
    return {**env, "HOME": str(own), "USERPROFILE": str(own), "APPDATA": str(config), "LOCALAPPDATA": str(own / "local"), "SHELL": SHELL,
            "TMPDIR": scratch, "TEMP": scratch, "TMP": scratch, "OPENCODE_CONFIG": str(config / opencode.CONFIG),
            "OPENCODE_PASSWORD": secrets.token_urlsafe(32), "XDG_CONFIG_HOME": str(config), "XDG_DATA_HOME": str(own / "data"),
            "XDG_CACHE_HOME": str(own / "cache"), "XDG_STATE_HOME": str(own / "state"), **OPENCODE_OFFLINE, "POOLHOUSE_AGENT": "1",
            "POOLHOUSE_NONINTERACTIVE": "1", "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def _written(rec: store.Delegate, own: Path, bridged: tuple[tuple[str, str, int], str], ask: delegate_review.Pass, folder: Path) -> None:
    """The config, the plugin and the shell bound of one pass on ``folder``, in its own home ``own``; ``bridged`` is the model as the pass reaches
    it (address, name and window) and its token."""
    (served, token), config = bridged, own / "config"
    for each in (config, own / "tmp"):
        each.mkdir(mode=0o700)
    hook = runtime.module_argv(delegate_guard.__name__, folder, "--tier", ask.tier, "--root", str(folder), "--home", str(home.user_home()),
                               "--state", str(home.home()), "--scratch", str(own / "tmp"), "--calls", str(own / "calls.jsonl"))
    lead = runtime.module_argv(lead_mcp.__name__, folder, "--state", str(home.home()), "--board", rec.board, "--lead", rec.lead, "--name", rec.name)
    settings: dict[str, Any] = {"hook": hook, "root": str(folder), "tools": list(ask.tools), "fixed": [[str(folder), "."], [str(own), "$HOME"]]}
    if "shell" in ask.tools and os.name != "nt":   # the program that ends a command with all it started ends a process group, which Windows has not
        (config / "bounded.py").write_text(delegate_confined.BOUNDED, encoding="utf-8")
        settings["shell"] = {"argv": [sys.executable, "-I", str(config / "bounded.py")], "program": SHELL, "default": SHELL_DEFAULT_S, "most": SHELL_MAX_S}
    agent = {"mode": "primary", "system": ask.brief, "permissions": delegate_confined.permissions(ask.tools)}
    setup = opencode.Setup(served, token, str(config / opencode.PLUGIN), lead, families.for_model_id(served[1]).think_kwargs(False), agent=agent)
    (config / opencode.CONFIG).write_text(opencode.config(setup), encoding="utf-8")
    entry = config / opencode.PLUGIN / opencode.ENTRY
    entry.parent.mkdir(parents=True)
    entry.write_text(opencode.plugin(settings), encoding="utf-8")


def _heard(path: Path, hooks: delegate_confined.Hooks) -> Callable[[], None]:
    """What hands ``hooks`` each event the hook recorded in ``path`` since the last look."""
    read = [0]

    def drain() -> None:
        try:
            held = path.read_bytes()[read[0]:]
        except FileNotFoundError:
            return
        for line in held.split(b"\n")[:-1]:
            read[0] += len(line) + 1
            row = json.loads(line)
            hooks.heard(row["event"], row["payload"], row["said"])

    return drain


def converse(rec: store.Delegate, bridge: Any, mine: Any, say: Callable[[str], None] = log) -> harness.Answer:
    """The task on the model, on the folder itself: the model leased, OpenCode started with a configuration of its own and read to its end, and the
    work self-checked when the record asks for it. A run that fails, reaches its limit or runs without the plugin that asks the hook is ended; what it
    changed before that stays in the folder."""
    binary = harnessing.binary_for("opencode", pinned=True) or opencode_install.ensure()
    store.set_status(rec.name, "loading", rec.model)
    taken = delegate_resume.Model(lambda: delegate_confined.leased(rec.model, say),
                                  lambda model: delegate_confined.told(rec, mine, model))
    with taken as model, tempfile.TemporaryDirectory(prefix="poolhouse-delegate-check-") as scratch:
        return _InPlace(rec, binary, model, say, Path(scratch).resolve()).run()


class _InPlace:
    """The passes of one delegate on its folder (`delegate_review.Bench`); ``scratch`` holds the copies a self-check compares and reads and the
    conversation of a pass broken off."""

    def __init__(self, rec: store.Delegate, binary: str, model: delegate_resume.Model, say: Callable[[str], None], scratch: Path) -> None:
        self.rec, self.binary, self.model, self.say, self.scratch = rec, binary, model, say, scratch
        self.window = model.window
        self.last: harness.Answer | None = None
        self.writes: list[delegate_refusals.Writes] = []
        self.used: list[harness.Usage] = []
        self.tried = False
        self.before: delegate_candidate.Folder | None = None
        self.uncompared = "no copy of the folder was taken before the work"

    def run(self) -> harness.Answer:
        """The work, and its self-check when the record asks for one."""
        rec, outcome = self.rec, None
        if rec.review_rounds and rec.edit:
            try:
                self.before = delegate_candidate.Folder(Path(rec.workdir), self.scratch / "before")
            except (OSError, ValueError) as why:
                self.uncompared = str(why)
                self.say(f"the change of this run cannot be shown to its reviewer: {why}")
        work = delegate_confined.work_pass(rec)
        try:
            latest = self.run_pass(work).answer
            if rec.review_rounds and not latest.is_error:
                latest, outcome = delegate_review.looped(rec, self, work, latest)
                if rec.edit and not outcome.accepted:
                    outcome = replace(outcome, where=self._kept_patch())
        except (SystemExit, KeyboardInterrupt) as stop:
            why = delegate_confined.STOPPED_WHY
            said = getattr(stop, "answer", None) if self.last is None else replace(self.last, is_error=True, why=why)
            said = said or harness.Answer("", harness.Usage(), is_error=True, why=why)
            raise delegate_confined.Stopped(self._came_to(said, None)) from stop
        return self._came_to(latest, outcome)

    def change(self) -> str:
        """What differs in the folder from before the work, as text; empty for a delegate that only reads."""
        if not self.rec.edit:
            return ""
        if self.before is None:
            return f"the change is not shown: {self.uncompared}"
        try:
            return delegate_candidate.diff(self.before.edits())
        except (OSError, ValueError) as why:
            return f"the change is not shown: {why}"

    def run_pass(self, ask: delegate_review.Pass) -> delegate_review.Ran:
        """A pass, continued when it is broken off."""
        return delegate_resume.carried_on(self.rec, ask, self._once(ask), self._once,
                                          lambda: delegate_resume.waited(self.model, self.say))

    def _once(self, ask: delegate_review.Pass) -> delegate_review.Ran:
        """One session of the model, in a home of its own that is removed with it, and behind a bridge to the server serving it now. A pass that
        cannot be started failed."""
        reviewing, first = ask.phase == "review", ask.phase == "work" and not ask.attempt
        own = Path(tempfile.mkdtemp(prefix="poolhouse-delegate-home-")).resolve()
        began, hooks = time.time(), delegate_confined.Hooks()
        url, alias, window = self.model.url, self.model.alias, self.model.window
        bridge = isolation.ModelBridge(url, isolation.Lease.of(alias, window, 1), key=serverkeys.for_url(url))
        broke = ""
        try:
            folder = Path(self.rec.workdir)
            if reviewing and self.before is not None:
                folder = self.scratch / f"review{ask.round}.{ask.attempt}"
                folder.mkdir()
                self.before.copy(folder)
            reached = (f"http://127.0.0.1:{bridge.start()}", alias, window)
            delegate_recorder.of(self.rec.name).hide(bridge.token, serverkeys.for_url(url) or "")
            _written(self.rec, own, (reached, bridge.token), ask, folder)
            if ask.session:
                delegate_resume.carry_conversation(self.scratch / "data", own / "data")
            answer, broke = self._followed(ask, own, folder, hooks, bridge)
            if broke:
                shutil.rmtree(self.scratch / "data", ignore_errors=True)
                delegate_resume.carry_conversation(own / "data", self.scratch / "data")
        except delegate_confined.Stopped as stop:
            self.used.append(stop.answer.spent)
            delegate_review.noted(self.rec.name, ask, delegate_review.Ran(stop.answer, hooks.calls), began)
            raise
        except (Denied, OSError, RuntimeError, ValueError) as error:
            if first:
                raise
            why = f"{type(error).__name__}: {error}"
            self.say(why)
            answer = harness.Answer("", harness.Usage(), is_error=True, why=why)
            delegate_recorder.of(self.rec.name).ended(ask, delegate_review.Ran(answer), time.time() - began)
        finally:
            bridge.stop()
            shutil.rmtree(own, ignore_errors=True)
        self.used.append(answer.spent)
        if not reviewing:
            self.tried, self.last = self.tried or answer.tried, answer
        ran = delegate_review.Ran(answer, hooks.calls, broke)
        delegate_review.noted(self.rec.name, ask, ran, began)
        return ran

    def _followed(self, ask: delegate_review.Pass, own: Path, folder: Path, hooks: delegate_confined.Hooks,
                  bridge: isolation.ModelBridge) -> tuple[harness.Answer, str]:
        """The answer of the pass, and what broke it off from outside."""
        rec, alias = self.rec, self.model.alias
        seen = delegate_refusals.Writes(str(folder))
        seen.within(str(folder), home=str(home.user_home()))
        if ask.phase != "review":
            self.writes.append(seen)

        def ran(part: Mapping[str, Any]) -> None:
            drain()   # the hook recorded the call before the harness could say how it ended
            seen.ran(part)

        record = delegate_recorder.of(rec.name).of_pass(ask, lambda: store.status(rec.name).get("state") == "asking")
        stream = delegate_confined.Stream(ran, record)

        def progress() -> None:
            store.set_progress(rec.name, stream.turns, nudge_fence.clean(hooks.tool or stream.tool))

        hooks.moved, hooks.answered = progress, record.after(seen.asked)
        drain = _heard(own / "calls.jsonl", hooks)

        def moved() -> None:
            drain()
            progress()

        def held() -> bool:
            drain()
            return store.status(rec.name).get("state") == "asking" or hooks.running()

        def over() -> str:
            drain()
            if stream.finished and not hooks.loaded:
                return opencode.UNGUARDED
            if ask.max_turns is not None and stream.reason == "tool-calls" and stream.finished >= ask.max_turns:
                return f"the run reached its limit of {ask.max_turns} turn(s) with more to do, so it was ended"
            return ""

        taken_up = ("--session", ask.session) if ask.session else ()
        argv = (self.binary, "run", "--standalone", "--format", "json", "--model", f"{opencode.PROVIDER}/{alias}", *taken_up, "--title", "delegate")
        running = inplace.Direct(argv, environment(own, self.binary), str(folder), bridge.busy, bridge.look)
        store.set_phase(rec.name, ask.phase, ask.round)
        store.set_status(rec.name, "working", alias)
        record.started()
        status, why = 1, ""
        try:
            with store.log_file(rec.name).open("ab") as errors:
                process = running.popen(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
                delegate_confined.follow(running, process, ask.message.encode(), stream, (moved, held, over))
            status = process.returncode
        except (Denied, OSError, RuntimeError, ValueError) as error:
            why = f"{type(error).__name__}: {error}"
            self.say(why)
        except (SystemExit, KeyboardInterrupt) as stop:
            raise delegate_confined.Stopped(stream.answer(status, delegate_confined.STOPPED_WHY)) from stop
        finally:
            running.close()
        drain()
        answer = stream.answer(status, why)
        answer.tried = hooks.tried
        outside = delegate_resume.broke(stream.error, status, why, "", self.model.url) if answer.is_error and rec.resumes else ""
        return answer, outside

    def _kept_patch(self) -> str:
        """Where what the work changed is, for one the self-check did not accept."""
        said = ("the changes were made in its own worktree and none of them is kept there" if self.rec.branch else
                "the changes are in the folder itself, since this computer has no sandbox to hold them back")
        if self.before is None:
            return f"{said}; no patch of them was kept: {self.uncompared}"
        patch = store.folder(self.rec.name) / "rejected.patch"
        patch.write_text(delegate_candidate.diff(self.before.edits()) + "\n", encoding="utf-8")
        return f"{said}; what changed is kept at {patch}"

    def _came_to(self, answer: harness.Answer, outcome: delegate_review.Outcome | None) -> harness.Answer:
        """``answer`` with what every pass spent, where the changes are and the writes it was refused."""
        answer = replace(answer, spent=delegate_review.spent(self.used) if self.used else answer.spent, review=outcome,
                         tried=answer.tried or self.tried)
        if self.rec.edit:
            answer.refused_writes = tuple({each.path: each for seen in self.writes for each in seen.refused()}.values())
            answer.wrote = any(seen.wrote for seen in self.writes)
            answer.text += f"\n\nThis run had no sandbox: whatever it changed is in {nudge_fence.clean(self.rec.workdir)} itself."
        return answer
