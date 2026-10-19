"""What briefs and docs tell an agent to write to the board: no join or done chatter."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENTS = sorted((ROOT / ".claude" / "agents").glob("*.md"))
CHATTER = re.compile(r"announce\s+(joined|done)\b")


@pytest.mark.parametrize("definition", AGENTS, ids=lambda path: path.name)
def test_an_agent_definition_never_tells_a_subagent_to_announce_that_it_joined_or_is_done(definition):
    assert CHATTER.search(definition.read_text(encoding="utf-8")) is None
