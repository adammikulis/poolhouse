"""Every agent the computer can see as one conversation (docs/phone-api.md, "Sessions"), found without anybody registering it:
Claude Code transcripts and their subagents, the states the phone hooks report, delegate records and the workspace board's agents. Each
says whether a message reaches it now, at its next step, through its lead or not at all, and why."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from poolhouse import delegate_record
from poolhouse.lock import pid_alive
from poolhouse.phone_mailbox import SESSION, Refused, Signed, Store

from . import transcripts

__all__ = ["KEPT_S", "NOTES", "Sessions"]

KEPT_S = 7 * 24 * 3600
"""Ended sessions older than this are not listed."""
WORKING_S = 120
"""A transcript with no hooks that changed this recently belongs to a working session."""
OPEN_S = 30 * 60
"""One that changed this recently may still be open; older ones are taken as closed."""
QUIET_S = 24 * 3600
"""A connected session without a listener that said nothing for this long is taken as closed."""
NOTES = {
    "wake": "Delivered now; an idle session starts working.",
    "next_step": "Delivered at the agent's next step.",
    "turn_end": "Delivered when the agent's turn ends.",
    "parent": "This agent has finished or cannot be reached directly: its lead session is given your message to pass on.",
    "next_turn": "Delivered when the session next does something.",
    "unconnected": "Connect this project to phones on the computer to talk to this session.",
    "closed": "This session was closed at the computer.",
    "lead_closed": "This agent has finished and its lead session cannot be reached.",
    "unboxed": "A delegate without a sandbox has no way to take a message yet.",
    "ended": "This delegate has ended.",
    "other": "This kind of agent has no supported way to take your messages yet.",
    "remote": "This agent runs on another computer: link your phone to that computer to talk to it.",
}
EVENTS_MOST = 200
CURSOR = re.compile(r"t(\d{1,15})\.m(\d{1,25})\Z")
NOBODY: Callable[[], list[dict[str, Any]]] = list


def _row(ident: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {"id": ident, "kind": kind, "title": "", "agent": "", "model": "", "project": "", "device": "", "this_computer": True,
            "parent": None, "state": "idle", "mode": "none", "note": "", "active_at": 0.0, "last_text": "", "waiting": 0, **fields}


def _under(folder: str, projects: list[str]) -> bool:
    if not folder:
        return False
    real = os.path.realpath(folder)
    return any(real == each or real.startswith(each.rstrip(os.sep) + os.sep) for each in projects)


class Sessions:
    """The sessions of this computer. ``agents`` lists the workspace board's agents; ``computer`` is this device's name."""

    def __init__(self, store: Store, computer: Callable[[], str], agents: Callable[[], list[dict[str, Any]]] = NOBODY,
                 transcript_root: Path | None = None) -> None:
        self.store, self.computer, self.agents = store, computer, agents
        self.root = transcript_root
        self._described: dict[Path, tuple[float, dict[str, str]]] = {}
        self._paths: dict[str, Path] = {}

    def _describe(self, entry: transcripts.Found) -> dict[str, str]:
        held = self._described.get(entry.path)
        if held is None or held[0] != entry.changed:
            held = self._described[entry.path] = (entry.changed, transcripts.described(entry))
        return held[1]

    # -- Claude Code -------------------------------------------------------------
    def _claude(self, entry: transcripts.Found, asking: set[str], connected: list[str], now: float) -> dict[str, Any]:
        about, held = self._describe(entry), self.store.session(entry.id)
        cwd = about["cwd"] or str(held.get("cwd") or "")
        updated = float(held.get("updated") or 0)
        quiet = now - max(entry.changed, updated)
        listener = held.get("listener") or {}
        if held.get("state") == "ended" and entry.changed <= updated + 5:
            state, mode, note = "ended", "none", NOTES["closed"]
        else:
            if held.get("connected"):
                state = str(held.get("state") or "idle")
                if state in ("idle", "ended") and entry.changed > updated + 30:
                    state = "working" if now - entry.changed < WORKING_S else "idle"
                if pid_alive(int(listener.get("pid") or 0)):
                    mode, note = "wake", NOTES["wake"]
                elif state == "working" and quiet < OPEN_S:
                    mode, note = "next_step", NOTES["next_step"]
                elif quiet < QUIET_S:
                    mode, note = "next_turn", NOTES["next_turn"]
                else:
                    state, mode, note = "ended", "none", NOTES["closed"]
            else:
                state = "working" if quiet < WORKING_S else "idle" if quiet < OPEN_S else "ended"
                if state == "ended":
                    mode, note = "none", NOTES["closed"]
                elif _under(cwd, connected):
                    mode, note = "next_turn", NOTES["next_turn"]
                else:
                    mode, note = "none", NOTES["unconnected"]
        return _row(entry.id, "claude", title=about["title"] or Path(cwd).name or entry.session[:8], agent=about["agent"],
                    model=about["model"], project=Path(cwd).name, device=self.computer(),
                    state="asking" if entry.id in asking else state, mode=mode, note=note, active_at=max(entry.changed, updated),
                    last_text=about["last_text"] or str(held.get("last_text") or "")[:300])

    def _subagent(self, entry: transcripts.Found, leads: Mapping[str, dict[str, Any]], now: float) -> dict[str, Any]:
        about, held = self._describe(entry), self.store.session(entry.id)
        lead = leads.get(f"claude-{entry.session}")
        reachable = lead is not None and lead["mode"] != "none"
        running = held.get("state") == "working" and lead is not None and lead["state"] != "ended" and now - entry.changed < OPEN_S
        if running:
            state, mode, note = "working", "next_step", NOTES["turn_end"]
        else:
            state = "working" if not held and now - entry.changed < WORKING_S else "ended"
            mode, note = ("via_parent", NOTES["parent"]) if reachable else ("none", NOTES["lead_closed"])
        return _row(entry.id, "subagent", title=about["title"] or entry.agent, agent=about["agent"], model=about["model"],
                    project=Path(about["cwd"]).name, device=self.computer(), parent=f"claude-{entry.session}", state=state, mode=mode,
                    note=note, active_at=entry.changed, last_text=about["last_text"] or str(held.get("last_text") or "")[:300])

    def _reported(self, found: set[str], now: float) -> list[transcripts.Found]:
        """The sessions the hooks report that no transcript under the usual folder shows: one kept elsewhere is read from where its
        hooks said, and one with none yet is still listed."""
        out = []
        for ident, held in self.store.sessions().items():
            kind, _, rest = ident.partition("-")
            updated = float(held.get("updated") or 0)
            if ident in found or kind not in ("claude", "subagent") or not held.get("connected") or now - updated > KEPT_S:
                continue
            session, agent = (rest[:36], rest[37:]) if kind == "subagent" else (rest, "")
            path = Path(str(held.get("transcript") or ""))
            if path.suffix != ".jsonl" or not path.is_file():
                path = self.store.root / "no-transcript" / f"{ident}.jsonl"
            out.append(transcripts.Found(session, agent, path, path.stat().st_mtime if path.is_file() else updated))
        return out

    def _transcripts(self, now: float) -> list[dict[str, Any]]:
        entries = transcripts.found(now - KEPT_S, self.root)
        entries += self._reported({entry.id for entry in entries}, now)
        self._paths = {entry.id: entry.path for entry in entries}
        asking = {q["session"] for q in self.store.open_questions(now)}
        connected = [os.path.realpath(folder) for folder, on in self.store.projects().items() if on]
        leads = {entry.id: self._claude(entry, asking, connected, now) for entry in entries if not entry.agent}
        return [*leads.values(), *(self._subagent(entry, leads, now) for entry in entries if entry.agent)]

    # -- delegates and the board -------------------------------------------------
    def _delegates(self, now: float) -> list[dict[str, Any]]:
        out = []
        for run in delegate_record.runs(50):
            if not run or (not run["live"] and now - float(run.get("ended") or run.get("started") or 0) > KEPT_S):
                continue
            record = delegate_record.held(run["name"], "record.json")
            state = "asking" if run["state"] == "asking" else "working" if run["live"] else "ended"
            if not run["live"]:
                mode, note = "none", NOTES["ended"]
            elif record.get("sandboxed"):
                mode, note = "next_step", NOTES["next_step"]
            else:
                mode, note = "none", NOTES["unboxed"]
            out.append(_row(f"delegate-{run['name']}", "delegate", title=run["task"][:120], agent=run["name"], model=run["model"],
                            project=Path(str(record.get("source") or "")).name, device=self.computer(), state=state, mode=mode,
                            note=note, active_at=float(run.get("heard") or run.get("ended") or run.get("started") or 0),
                            last_text=str(run.get("doing") or "")))
        return out

    def _board(self, listed: set[str], now: float) -> list[dict[str, Any]]:
        out = []
        for agent in self.agents():
            name = str(agent.get("name") or "")
            ident = f"agent-{name}"
            here = bool(agent.get("this_computer", True))
            seen = float(agent.get("seen") or 0)
            covered = name in listed or (here and agent.get("harness") == "claude-code")
            if covered or not SESSION.match(ident) or (agent.get("retired") and now - seen > KEPT_S):
                continue
            out.append(_row(ident, "agent", title=f"{agent.get('harness') or 'agent'} {name}", agent=name,
                            model=str(agent.get("model") or ""), device=str(agent.get("device") or self.computer()), this_computer=here,
                            parent=f"agent-{agent['parent']}" if agent.get("parent") else None,
                            state="ended" if agent.get("retired") else "idle", mode="none",
                            note=NOTES["other"] if here else NOTES["remote"], active_at=seen))
        return out

    def list(self, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        found = [*self._transcripts(now), *self._delegates(now)]
        found += self._board({row["agent"] for row in found if row["agent"]}, now)
        for row in found:
            if row["mode"] != "none":
                row["waiting"] = self.store.waiting(row["id"])
        return sorted(found, key=lambda r: -r["active_at"])

    def get(self, ident: str) -> dict[str, Any] | None:
        return next((row for row in self.list() if row["id"] == ident), None)

    def recent(self, since: float) -> list[str]:
        """The folders the Claude Code sessions changed after ``since`` ran in, the most recently active first."""
        return [self._describe(entry)["cwd"] for entry in transcripts.found(since, self.root) if not entry.agent]

    # -- history and sending -----------------------------------------------------
    def events(self, ident: str, after: str = "") -> dict[str, Any]:
        """The events of session ``ident`` after cursor ``after`` (its newest without one), oldest first, and the next cursor."""
        at = CURSOR.match(after) if after else None
        if after and at is None:
            raise Refused("That cursor was not given by this computer.")
        offset, since = (int(at[1]), int(at[2])) if at else (None, 0)
        recorded, following = self._recorded(ident, offset)
        own = [row for row in self.store.events(ident) if int(row["id"][2:]) > since]
        since = max([since, *(int(row["id"][2:]) for row in own)])
        found = sorted([*recorded, *own], key=lambda row: row.get("at") or 0)
        if offset is None:
            found = found[-EVENTS_MOST:]
        return {"events": found, "next": f"t{following}.m{since}", "more": False}

    def _recorded(self, ident: str, offset: int | None) -> tuple[list[dict[str, Any]], int]:
        if ident.startswith("delegate-"):
            return transcripts.delegate_events(ident[len("delegate-"):], offset)
        if ident.startswith(("claude-", "subagent-")):
            if ident not in self._paths:
                self._transcripts(time.time())
            if path := self._paths.get(ident):
                return transcripts.claude_events(path, offset, sidechain=ident.startswith("subagent-"))
        return [], offset or 0

    def send(self, ident: str, signed: Signed) -> dict[str, Any]:
        """Queue a phone's signed message for session ``ident``; `LookupError` for none, `Refused` when it can take no message."""
        row = self.get(ident)
        if row is None:
            raise LookupError("There is no session with that id.")
        if row["mode"] == "none":
            raise Refused(row["note"])
        if row["mode"] == "via_parent":   # its lead takes the mail of a subagent that has ended
            self.store.report(ident, state="ended", parent=row["parent"], description=row["title"], connected=True)
        message = self.store.send(ident, signed)
        return {"message": message, "state": "queued", "mode": row["mode"], "note": row["note"]}
