"""The native worker receives one complete bounded task rather than an inbox preview."""
import threading

from local_kit import team  # noqa: F401
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse.workspace import localagent as la, localloop


def test_worker_receives_constraints_after_a_long_task(team):  # noqa: F811
    agent = team.worker("worker", profile="coding", orders_from=(team.lead.name,))
    payload = "Read the source. " * 300 + "REQUIRED: no unbrokered tests"
    team.session(team.lead).post(agent.identity, "task", payload)
    stopped = threading.Event()
    seen = []

    def execute(_, row, why, stop):
        seen.append(row["text"])
        stopped.set()
        return "answer", "done", 1

    settings = localloop.Settings(cancel=stopped, serve=lambda _: localloop.Held(None, {}), execute=execute)
    assert localloop.run(team.base, agent.name, settings) == 0
    assert len(seen) == 1 and "REQUIRED: no unbrokered tests" in seen[0]
    assert la.status_of(team.base, agent.name)["state"] == "stopped"
