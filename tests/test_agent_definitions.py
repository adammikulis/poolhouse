"""No agent definition fixes a turn ceiling: the lead states each task's budget in the brief."""

from pathlib import Path

AGENTS = Path(__file__).resolve().parents[1] / ".claude" / "agents"


def test_no_agent_definition_sets_a_turn_ceiling():
    definitions = sorted(AGENTS.glob("*.md"))
    assert definitions
    for path in definitions:
        header = path.read_text().split("---")[1]
        assert "maxTurns" not in header, f"{path.name} sets a turn ceiling"
