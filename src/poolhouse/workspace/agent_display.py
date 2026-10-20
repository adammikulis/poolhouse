"""Readable agent presentation, independent of capability and routing identity."""


def describe(agent) -> dict:
    """The presentation of an agent the node lists: its name, whether it is a main session or a subagent, and who spawned it."""
    return {'display_name': agent.name, 'session_kind': 'subagent' if agent.parent else 'main', 'spawned_by': agent.parent}


def spoken(shown: dict) -> str:
    """How a person reads an agent: its unique name, and who spawned it when a subagent."""
    return f"{shown['display_name']} (spawned by {shown['spawned_by']})" if shown.get('spawned_by') else shown['display_name']
