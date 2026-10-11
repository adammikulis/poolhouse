"""Readable agent presentation, independent of capability and routing identity."""
from poolhouse.workspace.model_tiers import LOWEST, tier_of

SPAWN_NOTICE = ("no main session is eligible to coordinate and only lowest-tier sessions are present: "
                "spawn a subagent at a suitable level (Sonnet 5.5 is acceptable) to coordinate")


def describe(agent) -> dict:
    """The presentation of an agent the node lists: its name, whether it is a main session or a subagent, and
    whether it may coordinate (a subagent never; a main session by its model's tier)."""
    reason = 'not a main session' if agent.parent else tier_of(agent.model, agent.model_state).reason
    return {'display_name': agent.name, 'session_kind': 'subagent' if agent.parent else 'main',
            'coordinator_eligible': not reason, 'coordinator_reason': reason, 'spawned_by': agent.parent}


def spoken(shown: dict) -> str:
    """How a person reads an agent: its unique name, and who spawned it when a subagent."""
    return f"{shown['display_name']} (spawned by {shown['spawned_by']})" if shown.get('spawned_by') else shown['display_name']


def vacancy_notice(rows) -> str:
    """What to do when main sessions exist and none may coordinate because all are lowest tier; empty otherwise."""
    mains = [row for row in rows if row['session_kind'] == 'main' and not row['parent']]
    if mains and not any(row['coordinator_eligible'] for row in mains) \
            and any(row['coordinator_reason'] == LOWEST for row in mains):
        return SPAWN_NOTICE
    return ''
