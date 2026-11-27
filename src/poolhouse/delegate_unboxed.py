"""A delegate on a computer that cannot sandbox it: OpenCode on the folder itself, under the tier the lead chose.

Every tier has OpenCode's own permission rules (`read-only` denies the shell and editing) and the plugin that asks Poolhouse's hook before each tool
call (`poolhouse.delegate_guard`): the deterministic blocks and, below `yolo`, the classifier of the tier. Its home and configuration are a folder
of its own, so nothing of the user's is read as its settings or written. The model is reached through the same loopback bridge, behind a token of
this run, as in a sandbox, so the server's key stays in this process. None of this is a boundary: a program the model writes or runs reaches
whatever the account reaches, and only a sandbox stops that.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from poolhouse import (
    claude,
    coding_policy,
    delegate_confined,
    delegate_guard,
    delegate_refusals,
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


def _written(rec: store.Delegate, own: Path, served: tuple[str, str, int], token: str) -> None:
    """The config, the plugin and the shell bound of one run, in its own folder."""
    config, folder = own / "config", Path(rec.workdir)
    for each in (config, own / "tmp"):
        each.mkdir(mode=0o700)
    tools = delegate_confined.tools_of(rec.edit)
    hook = runtime.module_argv(delegate_guard.__name__, folder, "--tier", rec.permissions, "--root", str(folder), "--home", str(home.user_home()),
                               "--state", str(home.home()), "--scratch", str(own / "tmp"), "--calls", str(own / "calls.jsonl"))
    lead = runtime.module_argv(lead_mcp.__name__, folder, "--state", str(home.home()), "--board", rec.board, "--lead", rec.lead, "--name", rec.name)
    settings: dict[str, Any] = {"hook": hook, "root": str(folder), "tools": list(tools), "fixed": [[str(folder), "."], [str(own), "$HOME"]]}
    if rec.edit and os.name != "nt":   # the program that ends a command with all it started ends a process group, which Windows has not
        (config / "bounded.py").write_text(delegate_confined.BOUNDED, encoding="utf-8")
        settings["shell"] = {"argv": [sys.executable, "-I", str(config / "bounded.py")], "program": SHELL, "default": SHELL_DEFAULT_S, "most": SHELL_MAX_S}
    agent = {"mode": "primary", "system": delegate_confined.brief_of(rec), "permissions": delegate_confined.permissions(tools)}
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
    """The task on the model, on the folder itself: the model leased, OpenCode started with this run's own configuration and read to its end. A run
    that fails, reaches its limit or runs without the plugin that asks the hook is ended; what it changed before that stays in the folder."""
    binary = harnessing.binary_for("opencode", pinned=True) or opencode_install.ensure()
    store.set_status(rec.name, "loading", rec.model)
    with delegate_confined.leased(rec.model, say) as (base_url, config, found):
        alias = claude.alias_of(base_url, found)
        mine.record_model(alias, "poolhouse-agent", base_url)
        window = config.serving.slot_context
        own = Path(tempfile.mkdtemp(prefix="poolhouse-delegate-home-")).resolve()
        served = isolation.ModelBridge(base_url, isolation.Lease.of(alias, window, 1), key=serverkeys.for_url(base_url))
        try:
            url = f"http://127.0.0.1:{served.start()}"
            _written(rec, own, (url, alias, window), served.token)
            return _followed(rec, binary, own, (served.busy, alias), say)
        finally:
            served.stop()
            shutil.rmtree(own, ignore_errors=True)


def _followed(rec: store.Delegate, binary: str, own: Path, model: tuple[Callable[[], int], str], say: Callable[[str], None]) -> harness.Answer:
    busy, alias = model
    seen = delegate_refusals.Writes(rec.workdir)

    def ran(part: Mapping[str, Any]) -> None:
        drain()   # the hook recorded the call before the harness could say how it ended
        seen.ran(part)

    stream = delegate_confined.Stream(ran)

    def progress() -> None:
        store.set_progress(rec.name, stream.turns, nudge_fence.clean(hooks.tool or stream.tool))

    hooks = delegate_confined.Hooks(progress, seen.asked)
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
        if rec.max_turns is not None and stream.reason == "tool-calls" and stream.finished >= rec.max_turns:
            return f"the run reached its limit of {rec.max_turns} turn(s) with more to do, so it was ended"
        return ""

    argv = (binary, "run", "--standalone", "--format", "json", "--model", f"{opencode.PROVIDER}/{alias}", "--title", "delegate")
    running = inplace.Direct(argv, environment(own, binary), rec.workdir, busy)
    store.set_status(rec.name, "working", alias)
    status, why = 1, ""
    try:
        with store.log_file(rec.name).open("ab") as errors:
            process = running.popen(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
            delegate_confined.follow(running, process, rec.message().encode(), stream, (moved, held, over))
        status = process.returncode
    except (Denied, OSError, RuntimeError, ValueError) as error:
        why = f"{type(error).__name__}: {error}"
        say(why)
    except (SystemExit, KeyboardInterrupt) as stop:
        raise delegate_confined.Stopped(_came_to(rec, stream.answer(status, delegate_confined.STOPPED_WHY), seen)) from stop
    finally:
        running.close()
    drain()
    answer = stream.answer(status, why)
    answer.tried = hooks.tried
    return _came_to(rec, answer, seen)


def _came_to(rec: store.Delegate, answer: harness.Answer, seen: delegate_refusals.Writes) -> harness.Answer:
    """``answer`` with where the changes of a run with no sandbox are, and the writes it was refused."""
    if rec.edit:
        answer.refused_writes, answer.wrote = seen.refused(), seen.wrote
        answer.text += f"\n\nThis run had no sandbox: whatever it changed is in {nudge_fence.clean(rec.workdir)} itself."
    return answer
