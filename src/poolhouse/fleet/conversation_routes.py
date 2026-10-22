"""Saved conversation listing, lookup and updates behind the daemon UI guard."""
from __future__ import annotations

from . import conversation_creation, setup_context
from .conversation_settings import effective
from .request_fields import exact_body
from .room_routes import _origin_ok


class ConversationRoutes:
    def route(self) -> bool:
        if self.path.startswith("/ui/conversations"):
            if not _origin_ok(self.header("Origin"), self.host_header):
                self.send(403, {"error": "conversation requests require the same origin"})
                return True
            try:
                return self._conversations()
            except setup_context.SessionLost as error:
                self.send(401, {"error": str(error)})
                return True
            except PermissionError:
                self.send(403, {"error": "Reconnect this Poolhouse window to continue."})
                return True
            except conversation_creation.Conflict:
                self.send(409, {"error": conversation_creation.ERROR})
                return True
            except (TypeError, ValueError) as error:
                self.send(400, {"error": str(error)})
                return True
        return super().route()

    def _body(self) -> dict:
        request = self.body()
        if not isinstance(request, dict):
            raise ValueError("conversation request must be an object")
        if set(request) - {"model", "title", "settings", "expected_revision"}:
            raise ValueError("unknown conversation field")
        for field in ("model", "title"):
            if field in request and not isinstance(request[field], str):
                raise ValueError(f"{field} must be text")
        if "expected_revision" in request and (type(request["expected_revision"]) is not int
                or request["expected_revision"] < 0):
            raise ValueError("expected revision must be a nonnegative integer")
        if "settings" in request and not isinstance(request["settings"], dict):
            raise ValueError("conversation settings must be an object")
        return request

    def _conversations(self) -> bool:
        if self.path == "/ui/conversations/defaults" and self.method == "GET":
            self.send(200, {"settings": effective(self.ui.settings)})
            return True
        store = self.ui.conversations
        if store is None:
            self.send(501, {"error": "no chat store on this daemon"})
            return True
        if self.path.startswith("/ui/conversations/operations/"):
            if self.method != "GET":
                self.send(405, {"error": "Use GET to check a conversation request."})
                return True
            context = setup_context.bind(self)
            found = store.creation(self.path.removeprefix("/ui/conversations/operations/"), context.binding,
                                   current=context.current)
            self.send(200 if found is not None else 404, found if found is not None else {"error": "No such conversation request."},
                      {"Cache-Control": "no-store"})
            return True
        cid = self.path[len("/ui/conversations"):].strip("/")
        if cid:
            return self._one_conversation(cid)
        if self.method == "GET":
            self.send(200, {"conversations": [conversation.public(full=False)
                                             for conversation in store.search(self.asked("q"))]})
            return True
        if self.method == "POST":
            context = setup_context.bind(self)
            request = exact_body(self, ("operation_id",), optional=("model", "title", "settings"))
            operation = request.pop("operation_id")
            made = store.create(operation, request, context.binding, defaults=effective(self.ui.settings), current=context.current)
            self.send(201, made, {"Cache-Control": "no-store"})
            return True
        return super().route()

    def _one_conversation(self, cid: str) -> bool:
        store = self.ui.conversations
        found = store.get(cid)
        if found is None:
            self.send(404, {"error": "no such chat"})
            return True
        if self.method == "GET":
            self.send(200, found.public())
            return True
        if self.method == "DELETE":
            store.remove(cid)
            self.send(200, {"removed": cid})
            return True
        if self.method == "POST":
            request = self._body()
            changed = store.update(cid, model=request.get("model"), title=request.get("title"),
                                   settings=request.get("settings"), expected_revision=request.get("expected_revision"))
            if changed is None:
                self.send(404, {"error": "no such chat"})
            else:
                self.send(200, changed.public())
            return True
        return super().route()
