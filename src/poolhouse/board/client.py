"""The node's local API: length-prefixed JSON over a Unix socket or a Windows pipe (docs/node.md, "Local API")."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import errors, node_launch
from poolhouse.node_health import API_VERSION, MAX_FRAME, exchange

__all__ = ["Client", "Conflict", "Denied", "Invalid", "NodeError", "Quota"]



class NodeError(errors.Error):
    """The node refused a request; ``code`` is its word for why."""

    def __init__(self, code: str, message: str, grant: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.grant = grant
        """What a person's key would sign to allow the request (``action``, ``resource``, ``board``, ``name``, ``device``), when a missing grant is why it was refused."""


class Denied(NodeError, errors.Denied):
    """The caller may not do that."""


class Invalid(NodeError):
    """The request is malformed or names something that is not there."""


class Quota(NodeError):
    """Something past what the node keeps."""


class Conflict(Denied, errors.Conflict):
    """Another session holds what a claim asked for."""


def _error(reply: dict[str, Any]) -> NodeError:
    code, message = reply["error"]["code"], str(reply["error"]["message"])
    kind = {"denied": Denied, "invalid": Invalid, "quota": Quota}.get(code, NodeError)
    return kind(code, message, reply["error"].get("grant"))


class Client:
    """One node, reached on its socket; the node is started first when the socket is dead."""

    def __init__(self, state: Path | None = None, *, person: Callable[[str, dict[str, Any]], bool] | None = None) -> None:
        self.state = state or node_launch.default_state()
        self.requests = 0
        self.person = person
        """For a client a person is running: asked with the token and what a refusal named, it returns True once the person's key allowed it."""

    def call(self, method: str, board: str = "", token: str = "", **params: Any) -> Any:
        """Send one request and return its result; a refusal raises `NodeError`. A refusal for want of a grant is tried
        once more when `person` allowed it."""
        try:
            return self._send(method, board, token, params)
        except Denied as err:
            if self.person is None or not token or not err.grant or not self.person(token, err.grant):
                raise
            return self._send(method, board, token, params)

    def _send(self, method: str, board: str, token: str, params: dict[str, Any]) -> Any:
        self.requests += 1
        request: dict[str, Any] = {"v": API_VERSION, "id": self.requests, "method": method, "params": params}
        if board:
            request["board"] = board
        if token:
            request["token"] = token
        reply = self._exchange(request)
        if reply.get("ok") is not True:
            raise _error(reply)
        return reply["result"]

    def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        node_launch.ensure_node(self.state)
        body = json.dumps(request).encode()
        if len(body) > MAX_FRAME:
            raise Quota("quota", "the request is larger than a frame may be")
        reply = exchange(self.state, body, 660.0)
        return json.loads(reply)
