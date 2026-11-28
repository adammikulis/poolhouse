"""What each signed request of a linked phone does (docs/phone-api.md, section 4). Every operation is the one the computer's own page or
command runs: the pool page's members and actions, the serving lifecycle, the daemon's job runner, the delegate and scheduled-job records.
Nothing here is a second implementation. Every change is audited with the phone that asked."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from poolhouse import delegate_record, errors, lazy
from poolhouse.phone_mailbox import Phone, Refused, Signed

from ..pausing import minutes_of
from ..serving import NoRoom
from .errors import ApiError
from .model import PREFIX
from .server import Window

__all__ = ["MOST_SAID_A_MINUTE", "Api"]

MOST_SAID_A_MINUTE = 30
MOST_LOG = 64 * 1024
MOST_TASK = 8000
CANCELLABLE = ("queued", "preparing", "running")
ROUTES: list[tuple[str, re.Pattern[str], str]] = []


def _route(method: str, pattern: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def take(handler: Callable[..., Any]) -> Callable[..., Any]:
        ROUTES.append((method, re.compile(rf"/phone/v1{pattern}\Z"), handler.__name__))
        return handler

    return take


@dataclass(frozen=True)
class Asked:
    """One admitted request."""

    phone: Phone
    signed: Signed
    names: tuple[str, ...]
    query: Mapping[str, str]
    body: Mapping[str, Any]


def _text(asked: Asked, key: str, most: int, *, required: bool = True) -> str:
    value = asked.body.get(key, "")
    if not isinstance(value, str) or len(value) > most or (required and not value.strip()):
        raise ApiError(400, "invalid", f"{key} is text of at most {most} characters.")
    return value.strip()


def _missing(what: str) -> ApiError:
    return ApiError(404, "missing", f"There is no {what} with that id.")


def project_id(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:12]


class Api:
    """The operations, over ``computer``: the daemon's page (``ui``), the phone service's sessions and models, and the actions the
    command that started the daemon handed in."""

    def __init__(self, computer: Any) -> None:
        self.computer = computer
        self.said = Window(MOST_SAID_A_MINUTE)

    @property
    def ui(self) -> Any:
        return self.computer.ui

    def answer(self, phone: Phone, signed: Signed) -> tuple[int, Any]:
        parsed = urllib.parse.urlsplit(signed.path)
        found = [(match, name) for method, pattern, name in ROUTES if method == signed.method and (match := pattern.match(parsed.path))]
        if not found:
            raise _missing("request")
        match, name = found[0]
        try:
            body = json.loads(signed.body) if signed.body else {}
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            raise ApiError(400, "invalid", "The body is a JSON object.") from None
        asked = Asked(phone, signed, tuple(urllib.parse.unquote(each) for each in match.groups()),
                      dict(urllib.parse.parse_qsl(parsed.query)), body)
        try:
            result = getattr(self, name)(asked)
        except ApiError:
            raise
        except Refused as why:
            raise ApiError(403, "refused", str(why)) from why
        except LookupError as why:
            raise ApiError(404, "missing", str(why.args[0]) if why.args else "There is no such thing.") from why
        except PermissionError as why:
            raise ApiError(403, "refused", delegate_record.cleaned(why, 600)) from why
        except NoRoom as why:
            raise ApiError(409, "conflict", delegate_record.cleaned(why, 600)) from why
        except (errors.Error, OSError, RuntimeError, ValueError) as why:
            raise ApiError(400, "invalid", delegate_record.cleaned(why, 600)) from why
        return result if isinstance(result, tuple) else (200, result)

    def _audit(self, asked: Asked, action: str, target: str = "") -> None:
        self.computer.store.audit(asked.phone.id, action, target)

    # -- the phone and the pool --------------------------------------------------
    @_route("GET", "/me")
    def me(self, asked: Asked) -> dict[str, Any]:
        phone = asked.phone
        return {"phone": phone.id, "name": phone.name, "words": phone.words, "computer": self.ui.name, "linked_at": phone.linked_at,
                "pool_use": phone.pool_use}

    @_route("DELETE", "/me")
    def unlink(self, asked: Asked) -> dict[str, Any]:
        self.computer.remove(asked.phone.id, by=asked.phone.id)
        return {"removed": True}

    @_route("GET", "/devices")
    def devices(self, asked: Asked) -> dict[str, Any]:
        pool, note, members = None, "", []
        if self.ui.pool_actions is not None and self.ui.pool_status(force=True) is not None:
            try:
                held = asdict(self.ui.pool_actions.status())
                pool = {"name": held.get("name", ""), "governed": held.get("governance") == "governed", "policy": held.get("policy", "")}
                members = [m for m in held.get("members", ()) if m.get("active")]
            except (errors.Error, OSError, ValueError) as why:
                note = delegate_record.cleaned(why, 300)
        else:
            note = "No node answers on this computer, so only the devices its daemon reaches are listed."
        return {"pool": pool, "devices": [*self._computers(members), *self._phones()], "note": note}

    def _computers(self, members: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        reached = list(self.ui.members(force=False))
        out = []
        for member in members:
            row = next((r for r in reached if (member.get("this_device") and r.get("is_self"))
                        or r.get("machine") == member.get("fingerprint")), None)
            if row is not None:
                reached.remove(row)
            out.append(self._computer(row, member))
        return [*out, *(self._computer(row, None) for row in reached)]

    @staticmethod
    def _computer(row: Mapping[str, Any] | None, member: Mapping[str, Any] | None) -> dict[str, Any]:
        row, member = row or {}, member or {}
        mine = bool(row.get("is_self") or member.get("this_device"))
        out = {"id": member.get("fingerprint") or row.get("machine") or row.get("name"), "kind": "computer",
               "name": member.get("name") or row.get("name") or "", "this_computer": mine,
               "online": bool(row) or bool(member.get("connected")) or mine, "address": member.get("address") or row.get("host") or "",
               "last_seen": float(member.get("last_seen_ms") or 0) / 1000,
               **{key: row.get(key, default) for key, default in
                  (("busy", False), ("paused", False), ("paused_because", ""), ("slots", 0), ("free", 0), ("queued", 0), ("room", ""),
                   ("gpu", ""), ("serving", []), ("models", []), ("version", ""))}}
        report = row.get("device") or {}
        if mine:
            memory = psutil.virtual_memory()
            out["memory"] = {"used_bytes": memory.total - memory.available, "total_bytes": memory.total}
            out["cpu_load"] = round(psutil.cpu_percent(interval=None) / 100, 3)
        total, free = report.get("vram_total_gb"), report.get("vram_free_gb")
        if isinstance(total, (int, float)) and total:
            card = {"name": str(report.get("gpu") or "GPU"), "memory_total_bytes": int(total * 1024 ** 3)}
            if isinstance(free, (int, float)):
                card["memory_used_bytes"] = int(max(total - free, 0) * 1024 ** 3)
            out["gpus"] = [card]
        return out

    def _phones(self) -> list[dict[str, Any]]:
        out = []
        for phone in self.computer.store.phones().values():
            state = self.computer.models.state(phone.id)
            online = self.computer.online(phone.id)
            out.append({"id": PREFIX + phone.id, "name": phone.name, "kind": "phone", "this_computer": False, "online": online,
                        "pool_use": phone.pool_use, "models": [state["model"]["name"]] if state["offering"] else [],
                        "paused_because": state["blocked"], "last_seen": phone.seen_at})
        return out

    @_route("POST", "/pool/(pause|resume)")
    def pause(self, asked: Asked) -> Any:
        resume = asked.names[0] == "resume"
        span = _text(asked, "for", 20, required=False)
        minutes = minutes_of(span)
        if span and minutes is None:
            raise ApiError(400, "invalid", "Give a length of time such as 2h or 45m.")
        self._audit(asked, "pool-" + asked.names[0], span)
        return self.ui.pause_fleet(resume=resume, minutes=minutes, reason=_text(asked, "reason", 200, required=False))

    # -- models ------------------------------------------------------------------
    def _running(self) -> list[dict[str, Any]]:
        return [each.public() for each in self.ui.serving.live(force=True)] if self.ui.serving is not None else []

    @_route("GET", "/models")
    def models(self, asked: Asked) -> dict[str, Any]:
        library = self.ui.models.library() if self.ui.models is not None else []
        installed = [{"path": row["path"], "name": Path(row["path"]).name, "size_bytes": row.get("size_bytes", 0)}
                     for row in library if row.get("servable")]
        return {"can_serve": self.ui.serving is not None, "installed": installed, "running": self._running()}

    @_route("POST", "/models/start")
    def start_model(self, asked: Asked) -> Any:
        if self.ui.serving is None or self.ui.models is None:
            raise ApiError(501, "unavailable", "This computer cannot run a model itself.")
        found = self.ui.models.library_model(_text(asked, "path", 2000))
        if found is None:
            raise _missing("installed model")
        context = asked.body.get("context")
        if context is not None and (type(context) is not int or context <= 0):
            raise ApiError(400, "invalid", "context is a positive whole number.")
        self._audit(asked, "model-start", found.name)
        served = self.ui.start_serving(found, context=context, current=lambda: asked.phone.id in self.computer.store.phones())
        return 201, served.public()

    @_route("POST", "/models/stop")
    def stop_model(self, asked: Asked) -> dict[str, Any]:
        port = asked.body.get("port")
        if type(port) is not int or port not in {row.get("port") for row in self._running()}:
            raise _missing("running model")
        self._audit(asked, "model-stop", str(port))
        self.ui.stop_serving(port)
        return {"running": self._running()}

    # -- jobs ----------------------------------------------------------------------
    @staticmethod
    def _job(row: Mapping[str, Any]) -> dict[str, Any]:
        """A job row for the phone: its environment and command line stay on the computer."""
        kept = {k: v for k, v in row.items() if k not in ("env", "argv", "cwd", "log", "pid", "process_started")}
        return {**kept, "cancellable": row.get("state") in CANCELLABLE}

    def _runner(self, ident: str = "") -> Any:
        runner = self.ui.runner
        if runner is None:
            raise ApiError(501, "unavailable", "This computer runs no jobs.")
        if ident and ident not in runner.jobs:
            raise _missing("job")
        return runner

    @_route("GET", "/jobs")
    def jobs(self, asked: Asked) -> dict[str, Any]:
        rows = sorted(self._runner().snapshot(), key=lambda r: -float(r.get("submitted_at") or 0))
        return {"jobs": [self._job(row) for row in rows[:200]]}

    @_route("GET", "/jobs/([A-Za-z0-9._-]{1,120})")
    def job(self, asked: Asked) -> dict[str, Any]:
        return self._job(self._runner(asked.names[0]).jobs[asked.names[0]].public())

    @_route("GET", "/jobs/([A-Za-z0-9._-]{1,120})/log")
    def job_log(self, asked: Asked) -> dict[str, Any]:
        path = self._runner(asked.names[0]).log_path(asked.names[0])
        tail = asked.query.get("tail", "200")
        lines = min(int(tail), 5000) if tail.isdigit() and len(tail) < 6 else 200
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, handle.seek(0, os.SEEK_END) - MOST_LOG))
                text = handle.read().decode("utf-8", "replace")
        except OSError:
            text = ""
        return {"log": delegate_record.cleaned("\n".join(text.splitlines()[-lines:]), MOST_LOG, tail=False)}

    @_route("POST", "/jobs/([A-Za-z0-9._-]{1,120})/stop")
    def stop_job(self, asked: Asked) -> dict[str, Any]:
        runner = self._runner(asked.names[0])
        self._audit(asked, "job-stop", asked.names[0])
        return self._job(runner.stop(asked.names[0]).public())

    # -- scheduled jobs ------------------------------------------------------------
    def _scheduled(self) -> Any:
        """The scheduled-job records (`poolhouse.schedule_record`) of a build that has them, else None."""
        try:
            return self.computer.schedule or lazy.load("poolhouse.schedule_record")
        except ImportError:
            return None

    def _schedule(self, ident: str) -> tuple[Any, Any]:
        record = self._scheduled()
        if record is None:
            raise ApiError(501, "unavailable", "This computer has no scheduled jobs.")
        held = record.load(ident) if record.ID.fullmatch(ident) else None
        if held is None:
            raise _missing("scheduled job")
        return record, held

    @_route("GET", "/scheduled")
    def scheduled(self, asked: Asked) -> dict[str, Any]:
        record = self._scheduled()
        if record is None:
            return {"available": False, "jobs": []}
        return {"available": True, "jobs": [record.view(row) for row in record.jobs() if not row.get("refused")]}

    @_route("GET", "/scheduled/([A-Za-z0-9._-]{1,120})")
    def scheduled_one(self, asked: Asked) -> dict[str, Any]:
        record, held = self._schedule(asked.names[0])
        return {"job": record.view(held), "log": delegate_record.cleaned(record.log_tail(asked.names[0]), MOST_LOG)}

    @_route("POST", "/scheduled/([A-Za-z0-9._-]{1,120})/cancel")
    def cancel_scheduled(self, asked: Asked) -> dict[str, Any]:
        record, _held = self._schedule(asked.names[0])
        self._audit(asked, "scheduled-cancel", asked.names[0])
        record.cancel(asked.names[0], f"the person, from the linked phone {asked.phone.id}")
        return {"cancelled": True}

    # -- projects and delegates ----------------------------------------------------
    def _projects(self) -> list[dict[str, Any]]:
        return [{"id": project_id(path), "name": Path(path).name, "path": path, "connected": on}
                for path, on in sorted(self.computer.store.projects().items()) if Path(path).is_dir()]

    @_route("GET", "/projects")
    def projects(self, asked: Asked) -> dict[str, Any]:
        return {"projects": self._projects()}

    def _actions(self) -> Any:
        if self.computer.actions is None:
            raise ApiError(501, "unavailable", "This computer was started without delegates.")
        return self.computer.actions

    @_route("POST", "/delegates")
    def start_delegate(self, asked: Asked) -> Any:
        actions = self._actions()
        project = next((p for p in self._projects() if p["id"] == asked.body.get("project")), None)
        if project is None:
            raise _missing("project")
        task, model = _text(asked, "task", MOST_TASK), _text(asked, "model", 200, required=False)
        edit = asked.body.get("edit", False)
        if not isinstance(edit, bool):
            raise ApiError(400, "invalid", "edit is true or false.")
        self._limit(asked)
        self._audit(asked, "delegate-start", project["path"])
        before = set(delegate_record.names())
        said = delegate_record.cleaned(actions.start_delegate(project["path"], task, edit, model), 1000)
        new = [name for name in delegate_record.names() if name not in before]
        return 201, {"name": new[-1] if new else "", "said": said}

    @_route("GET", "/delegates")
    def delegates(self, asked: Asked) -> dict[str, Any]:
        return {"runs": delegate_record.runs(50)}

    def _delegate(self, name: str) -> dict[str, Any]:
        seen = delegate_record.view(name) if delegate_record.NAME.fullmatch(name) else {}
        if not seen:
            raise _missing("delegate")
        return seen

    @_route("GET", "/delegates/([A-Za-z0-9._-]{1,81})")
    def delegate(self, asked: Asked) -> dict[str, Any]:
        run = self._delegate(asked.names[0])
        after = asked.query.get("after", "0")
        events, following, more = delegate_record.read(asked.names[0], int(after) if after.isdigit() and len(after) < 16 else 0)
        return {"run": run, "events": events, "next": following, "more": more}

    def _stop(self, asked: Asked, name: str) -> dict[str, Any]:
        self._delegate(name)
        actions = self._actions()
        self._audit(asked, "delegate-stop", name)
        return {"stopped": True, "said": delegate_record.cleaned(actions.stop_delegate(name), 1000)}

    @_route("POST", "/delegates/([A-Za-z0-9._-]{1,81})/stop")
    def stop_delegate(self, asked: Asked) -> dict[str, Any]:
        return self._stop(asked, asked.names[0])

    # -- sessions ------------------------------------------------------------------
    def _limit(self, asked: Asked) -> None:
        if not self.said.admit(asked.phone.id):
            raise ApiError(429, "slow_down", "Too many messages from this phone; wait a minute.")

    def _session(self, ident: str) -> dict[str, Any]:
        row = self.computer.sessions.get(ident)
        if row is None:
            raise _missing("session")
        return row

    @_route("GET", "/sessions")
    def sessions(self, asked: Asked) -> dict[str, Any]:
        return {"sessions": self.computer.sessions.list(), "note": ""}

    @_route("GET", "/sessions/([^/]{1,200})")
    def session(self, asked: Asked) -> dict[str, Any]:
        return {"session": self._session(asked.names[0])}

    @_route("GET", "/sessions/([^/]{1,200})/events")
    def events(self, asked: Asked) -> dict[str, Any]:
        self._session(asked.names[0])
        return self.computer.sessions.events(asked.names[0], asked.query.get("after", ""))

    @_route("POST", "/sessions/([^/]{1,200})/messages")
    def message(self, asked: Asked) -> dict[str, Any]:
        self._limit(asked)
        row = self._session(asked.names[0])
        if row["mode"] == "none":
            raise ApiError(409, "conflict", row["note"])
        return self.computer.sessions.send(asked.names[0], asked.signed)

    @_route("POST", "/sessions/([^/]{1,200})/questions/(q-[0-9a-f]{16})/answer")
    def answer_question(self, asked: Asked) -> dict[str, Any]:
        self._limit(asked)
        self.computer.store.answer(asked.names[0], asked.names[1], asked.signed)
        return {"answered": True}

    @_route("POST", "/sessions/([^/]{1,200})/stop")
    def stop_session(self, asked: Asked) -> dict[str, Any]:
        row = self._session(asked.names[0])
        if row["kind"] != "delegate":
            raise ApiError(409, "conflict", "This session has no stop; ask it to stop in a message.")
        return self._stop(asked, row["agent"])

    @_route("GET", "/inbox")
    def inbox(self, asked: Asked) -> dict[str, Any]:
        store = self.computer.store
        notices = [{**row, "session": session} for session in store.eventful() for row in store.events(session) if row["type"] == "notice"]
        questions = [{k: q.get(k) for k in ("session", "question", "questions", "asked_at", "expires_at")} for q in store.open_questions()]
        return {"questions": questions, "notices": sorted(notices, key=lambda n: -n.get("at", 0))[:50]}

    # -- the phone in the pool -----------------------------------------------------
    @_route("GET", "/pool-use")
    def pool_use(self, asked: Asked) -> dict[str, Any]:
        return self.computer.models.state(asked.phone.id)

    @_route("POST", "/pool-use")
    def switch_pool_use(self, asked: Asked) -> dict[str, Any]:
        enabled = asked.body.get("enabled")
        if not isinstance(enabled, bool):
            raise ApiError(400, "invalid", "enabled is true or false.")
        return self.computer.models.switch(asked.phone.id, enabled)

    @_route("POST", "/model/next")
    def model_next(self, asked: Asked) -> dict[str, Any]:
        return self.computer.models.next(asked.phone.id, asked.body)

    @_route("POST", "/model/requests/(r-[0-9a-f]{16})")
    def model_post(self, asked: Asked) -> dict[str, Any]:
        return self.computer.models.post(asked.phone.id, asked.names[0], asked.body)
