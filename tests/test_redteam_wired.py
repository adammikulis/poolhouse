"""The GPU wiring limit, attacked: no model tool, chat tool or slash command reaches it. Nothing changes it at all
(tests/test_wired.py holds that: no flag, no route, no administrator prompt)."""

from __future__ import annotations

import io


def test_no_model_tool_chat_tool_or_slash_command_touches_the_wiring_limit():
    from poolhouse import chat, do, mcp

    words = ("wired", "sysctl", "iogpu", "wiring")
    for tool in mcp.TOOLS:
        assert not any(w in f"{tool.name} {tool.description}".lower() for w in words), tool.name
    person = do.Person(io.StringIO(""), io.StringIO(""))
    session = chat.Chat(None, person, role="approve-first", extension=chat.extensions(person))
    for spec, _ in session.offered:
        text = f"{spec['function']['name']} {spec['function'].get('description', '')}".lower()
        assert not any(w in text for w in words), spec["function"]["name"]
