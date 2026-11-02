"""The client the bench runner's own tests hand it: one that takes exactly what `Client` takes.

It is test scaffolding and ships nowhere. It answers nothing a measurement could use, and no
shipped command reaches it: a person's run asks a real model through the lease. Bound against the
real ``Client.__init__`` signature, so a keyword the client does not take fails here, naming
itself, rather than on a load that costs minutes.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from poolhouse.client.chat import Client as _RealClient
from poolhouse.client.settings import Request, Transport

__all__ = ["Reply", "ScriptedModel"]


@dataclass
class Reply:
    content: str
    raw: dict
    tool_calls: list | None = None
    thinking: str | None = None
    finish_reason: str | None = None


class ScriptedModel:
    """Takes exactly what `Client` takes, calls look_up once, then answers.

    Keeps every message it was shown in ``seen``; ``told`` is what look_up answered, as the
    caller saw it. Has the ``card`` a ``--also card`` way reads and the ``sampling`` the run
    writes down.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8080", **settings: Any) -> None:
        inspect.signature(_RealClient.__init__).bind(None, base_url, **settings)
        self.base_url = base_url
        self.model = settings.get("model")
        self.pinned_family = settings.get("family")
        self.request = settings.get("request") or Request()
        self.transport = settings.get("transport") or Transport()
        self.sampling = self.request.sampling()
        self.card: dict[str, Any] = {"temperature": 1.0, "top_k": 64}
        self.text = "compilers"
        self.seen: list[list[dict[str, Any]]] = []

    def chat(self, messages: Sequence[Mapping[str, Any]], tools: Any = None, **_: Any) -> Any:
        self.seen.append([dict(m) for m in messages])
        offered = {str((t.get("function") or {}).get("name")) for t in (tools or [])}
        asked_before = sum(1 for m in messages if m.get("role") == "tool")
        if "look_up" in offered and not asked_before:
            return Reply(content="", raw={}, tool_calls=[{"id": "c1", "function": {
                "name": "look_up", "arguments": json.dumps({"texts": [self.text]})}}])
        return Reply(content="a compiler person", raw={})

    def told(self) -> str:
        """What look_up answered, as the model saw it."""
        return " ".join(str(m["content"]) for turn in self.seen for m in turn
                        if m.get("role") == "tool")
