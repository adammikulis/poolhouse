"""Saved conversations with transactional messages and model/project relationships."""
from __future__ import annotations

import json
import secrets
import uuid
from contextlib import contextmanager
from pathlib import Path

from poolhouse import coding_actions, coding_workspace_state, requests
from poolhouse.client import completion
from poolhouse.fleet import conversation_graph as graph
from poolhouse.fleet.conversation_settings import checked
from poolhouse.fleet.conversation_types import (
    ROLES,
    TITLE_CHARS,
    CodingState,
    Conversation,
    Message,
    safe,
    title,
)
from poolhouse.graph.columns import column

__all__ = ["Conversation", "Conversations", "Message"]


def _coding_current(store, expected: CodingState) -> None:
    if (not isinstance(expected, CodingState) or not safe(expected.conversation)
            or type(expected.revision) is not int or expected.revision < 0):
        raise ValueError("invalid coding session policy")
    found = graph.record(store, expected.conversation)
    if found is None or CodingState.from_conversation(found) != expected:
        raise ValueError("The coding session changed; reload its current settings")


def _user_message(store, cid, sequence):
    if type(sequence) is not int or sequence < 0:
        raise ValueError("An existing user message is required for coding")
    identifier = f"message:{cid}:{sequence}"
    rows = store.query("MATCH (c:Node {id:$cid})-[:Edge {rel:'contains'}]->(m:Node {id:$id}) "
                       "RETURN m.label AS content, m.attrs AS attrs", {"cid": graph.node_id(cid), "id": identifier})
    if len(rows) != 1:
        raise ValueError("The coding user message is missing; this turn stays interrupted")
    attrs = column(rows[0]["attrs"], identifier)
    if (attrs.get("role") != "user" or attrs.get("status") != "complete"
            or type(attrs.get("sequence")) is not int or attrs["sequence"] != sequence):
        raise ValueError("Only a complete user message starts a coding execution")
    return {"id": identifier, "kind": graph.MESSAGE, "label": rows[0]["content"], "attrs": attrs}


def _execution(store, execution):
    if not isinstance(execution, coding_actions.Execution):
        raise ValueError("An original coding execution receipt is required")
    saved = store.get_doc(f"coding:{execution.policy.conversation}", {})
    run = saved.get("executions", {}).get(execution.id)
    message = _user_message(store, execution.policy.conversation, execution.message)
    if (not isinstance(run, dict) or run.get("id") != execution.id or saved.get("execution") != execution.id
            or coding_actions.digest(run.get("binding")) != execution.binding
            or run["binding"].get("policy") != coding_actions.policy(execution.policy)
            or type(run.get("action_count")) is not int or run["action_count"] != len(run.get("actions", {}))
            or type(run.get("semantic_count")) is not int or run["semantic_count"] != len(run.get("semantic", {}))
            or any(identifier not in run["actions"] for identifier in run["semantic"].values())
            or not isinstance(run.get("active_action"), str)
            or any((row.get("state") == "running") != (identifier == run["active_action"])
                   for identifier, row in run["actions"].items())
            or (run["active_action"] and run["active_action"] not in run["actions"])
            or message["attrs"].get("coding_execution") != execution.id
            or coding_actions.digest(message["label"]) != run["binding"].get("message_digest")):
        raise ValueError("The coding execution changed or its history is missing; it stays interrupted")
    return saved, run


def _action(run, execution, action):
    row = run["actions"].get(action.id)
    if not isinstance(row, dict) or row.get("binding") != coding_actions.digest(action.binding(execution)):
        raise ValueError("The coding action changed or its receipt is missing; it stays interrupted")
    return row


class Conversations:
    """One conversation graph under the daemon's chat root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()

    def all(self) -> list[Conversation]:
        with graph.opened(self.root) as store:
            found = [conversation for cid in graph.all_ids(store) if (conversation := graph.get(store, cid)) is not None]
        return sorted(found, key=lambda conversation: conversation.created, reverse=True)

    def get(self, cid: str) -> Conversation | None:
        if not safe(cid):
            return None
        with graph.opened(self.root) as store:
            return graph.get(store, cid)

    def start(self, model: str = "", title: str = "", settings: dict | None = None) -> Conversation:
        made = Conversation(id=uuid.uuid4().hex[:12], title=title, model=model, settings=checked(settings))
        with graph.opened(self.root) as store, store.transaction():
            graph.save(store, made)
        return made

    def coding_state(self, cid: str) -> CodingState:
        """Read one current coding policy without loading its conversation history."""
        if not safe(cid):
            raise ValueError("invalid conversation id")
        with graph.opened(self.root) as store:
            found = graph.record(store, cid)
            if found is None:
                raise ValueError("no such coding session")
            return CodingState.from_conversation(found)

    @contextmanager
    def policy_guard(self, expected: CodingState, *, current):
        """Hold current policy only around a bounded parent-owned publication.

        Resource resolution, captures and tool execution precede this guard. The
        trusted current callback checks retained resource and execution identity
        locally, without subprocesses, network calls or acquiring registry locks.
        """
        with graph.opened(self.root) as store, store.transaction():
            _coding_current(store, expected)
            if current() is not True:
                raise ValueError("The coding session changed; reload its current settings")
            yield store
            _coding_current(store, expected)
            if current() is not True:
                raise ValueError("The coding session changed before publishing its result")

    def workspace_state(self, expected: CodingState) -> dict:
        """Read existing coding snapshots only for the exact current session policy."""
        with graph.opened(self.root) as store:
            _coding_current(store, expected)
            saved = store.get_doc(f"coding:{expected.conversation}", {})
            if not isinstance(saved.get("workspace"), dict):
                raise ValueError("The coding workspace receipt is missing; this session stays interrupted")
            return saved["workspace"]

    @contextmanager
    def coding_guard(self, expected: CodingState, previous: dict, replacement: dict, *, current):
        """Publish exactly one workspace epoch while retaining current authority."""
        if current() is not True:
            raise ValueError("The coding session changed before publishing its result")
        with graph.opened(self.root) as store:
            _coding_current(store, expected)
        coding_workspace_state.transition(previous, replacement)
        key = f"coding:{expected.conversation}"
        with self.policy_guard(expected, current=current) as store:
            saved = store.get_doc(key, {})
            if saved.get("workspace") != previous:
                raise ValueError("The coding workspace changed before publishing its result")
            yield store
            saved = store.get_doc(key, {})
            if saved.get("workspace") != previous:
                raise ValueError("The coding workspace changed during publication")
            store.put_doc(key, {**saved, "workspace": replacement})
            if current() is not True:
                raise ValueError("The coding session changed before committing its result")

    def begin_execution(self, expected: CodingState, intent: coding_actions.Intent, *, current):
        """Attach a coding execution to its original complete user message."""
        if current() is not True:
            raise ValueError("The original coding identity is no longer current")
        sequence = intent.message
        identity = coding_actions.identity(intent.identity)
        implementation = coding_actions.fingerprint(intent.implementation)
        workspace = json.loads(coding_actions.canonical(intent.workspace))
        coding_workspace_state.checked(Path(workspace["source"]), workspace)
        key = f"coding:{expected.conversation}"
        with self.policy_guard(expected, current=current) as store:
            saved = store.get_doc(key, {})
            if saved.get("workspace") != workspace:
                raise ValueError("The coding workspace changed before the turn started")
            message = _user_message(store, expected.conversation, sequence)
            binding = {"policy": coding_actions.policy(expected), "identity": identity, "message": sequence,
                       "message_digest": coding_actions.digest(message["label"]), "implementation": implementation,
                       "roots": {field: workspace[field] for field in coding_workspace_state.BOUND_FIELDS}}
            executions = saved.get("executions", {})
            identifier = message["attrs"].get("coding_execution")
            if identifier is not None:
                previous = executions.get(identifier)
                if not isinstance(previous, dict) or previous.get("binding") != binding:
                    raise ValueError("The coding execution history changed; this turn stays interrupted")
                execution = coding_actions.Execution(expected, identifier, coding_actions.digest(binding), sequence)
                _saved, previous = _execution(store, execution)
                if previous["state"] != "active":
                    raise ValueError("This coding turn is interrupted or blocked; a new user message is required")
                return execution
            if any(run.get("binding", {}).get("message") == sequence for run in executions.values()):
                raise ValueError("The original coding message binding is missing")
            identifier = uuid.uuid4().hex
            if saved.get("execution") in executions:
                executions[saved["execution"]]["state"] = "interrupted"
            executions[identifier] = {"id": identifier, "binding": binding, "seed": secrets.token_hex(32),
                                      "state": "active", "actions": {}, "semantic": {}, "action_count": 0, "semantic_count": 0, "active_action": ""}
            message["attrs"]["coding_execution"] = identifier
            store.upsert_node(message)
            store.put_doc(key, {**saved, "execution": identifier, "executions": executions})
            return coding_actions.Execution(expected, identifier, coding_actions.digest(binding), sequence)

    def reserve_action(self, execution: coding_actions.Execution, action: coding_actions.Action, *, current):
        """Persist intent before any approval or tool effect, attaching exact retries."""
        with self.policy_guard(execution.policy, current=current) as store:
            saved, run = _execution(store, execution)
            binding, semantic = coding_actions.digest(action.binding(execution)), action.semantic(execution)
            existing = run["actions"].get(action.id)
            if existing is not None:
                if existing.get("binding") != binding:
                    raise ValueError("This coding action ID already belongs to different arguments")
                return coding_actions.receipt(existing)
            if coding_actions.digest(saved.get("workspace")) != coding_actions.digest(json.loads(action.workspace)):
                raise ValueError("The coding workspace changed before this action")
            original = run["semantic"].get(semantic)
            if original is not None:
                if original not in run["actions"]:
                    raise ValueError("The original coding action history is missing")
                row = {"binding": binding, "state": "attached", "original": original}
            else:
                if run["state"] != "active":
                    raise ValueError("This coding turn is blocked; a new user message is required")
                decision = json.loads(action.decision)
                row = {"binding": binding, "semantic": semantic, "state": "intent", "decision": decision,
                       "input": coding_actions.digest(json.loads(action.workspace))}
                run["semantic"][semantic] = action.id
                run["semantic_count"] += 1
                if decision["action"] == "deny":
                    row["state"], run["state"] = "denied", "blocked"
            row.update(tool=action.tool, arguments_digest=coding_actions.digest(json.loads(action.arguments)))
            run["actions"][action.id] = row
            run["action_count"] += 1
            store.put_doc(f"coding:{execution.policy.conversation}", saved)
            return coding_actions.receipt(row)

    def action_request(self, execution: coding_actions.Execution, action: coding_actions.Action, *, current):
        """Attach approval initiation once and return fixed parent-only request inputs."""
        with self.policy_guard(execution.policy, current=current) as store:
            saved, run = _execution(store, execution)
            row = _action(run, execution, action)
            if row["state"] != "intent" or row["decision"]["action"] != "ask" or run["state"] != "active":
                raise ValueError("This coding action cannot request another approval")
            create = not row.get("request_started", False)
            row["request_started"] = True
            store.put_doc(f"coding:{execution.policy.conversation}", saved)
            ask, cancellation = coding_actions.approval_request(run, execution, action)
            return ask, cancellation, create

    def start_action(self, execution: coding_actions.Execution, action: coding_actions.Action, *, current,
                     approval: requests.Handle | None = None) -> bool:
        """Consume exact approval and intent once before invoking a tool process."""
        outcome = approval.outcome() if isinstance(approval, requests.Handle) else None
        with self.policy_guard(execution.policy, current=current) as store:
            saved, run = _execution(store, execution)
            row = _action(run, execution, action)
            if row["state"] != "intent" or run["state"] != "active":
                return False
            if run["active_action"]:
                raise ValueError("Another coding action is running or interrupted; it must not be repeated")
            if coding_actions.digest(saved.get("workspace")) != row["input"]:
                raise ValueError("The coding workspace changed while this action waited")
            if row["decision"]["action"] == "ask":
                request = approval.request if isinstance(approval, requests.Handle) else None
                ask, _cancel = coding_actions.approval_request(run, execution, action)
                if not row.get("request_started") or request is None or not coding_actions.approval_matches(request, ask):
                    raise ValueError("The approval does not belong to this coding action")
                if outcome.state == "pending":
                    return False
                if not outcome.approved:
                    known = outcome.state in ("expired", "cancelled") or (outcome.state == "denied" and outcome.choice == "deny")
                    row["state"] = outcome.state if known else "interrupted"
                    run["state"] = "blocked"
                    store.put_doc(f"coding:{execution.policy.conversation}", saved)
                    return False
            elif row["decision"]["action"] != "allow" or approval is not None:
                raise ValueError("This coding action has no valid execution decision")
            row["state"], run["active_action"] = "running", action.id
            store.put_doc(f"coding:{execution.policy.conversation}", saved)
            return True

    @contextmanager
    def action_guard(self, execution: coding_actions.Execution, action: coding_actions.Action, replacement: dict,
                     result: dict, *, current):
        """Commit a completed result and replacement workspace in one transaction."""
        if action.tool not in coding_actions.TOOLS:
            raise ValueError("Only a stock coding tool can replace its workspace")
        if type(result) is not dict:
            raise ValueError("The coding tool result must be an object")
        result = json.loads(coding_actions.canonical(result, limit=coding_actions.MAX_FRAME))
        previous = json.loads(action.workspace)
        with self.coding_guard(execution.policy, previous, replacement, current=current) as store:
            saved, run = _execution(store, execution)
            row = _action(run, execution, action)
            if row["state"] != "running" or run["state"] != "active":
                raise ValueError("This coding action is not the original running intent")
            before = coding_actions.canonical(run)
            yield store
            saved, run = _execution(store, execution)
            if coding_actions.canonical(run) != before:
                raise ValueError("The running coding action changed during publication")
            row = _action(run, execution, action)
            row.update(state="completed", result=result, result_digest=coding_actions.digest(result), workspace=replacement)
            run["active_action"] = ""
            store.put_doc(f"coding:{execution.policy.conversation}", saved)

    def recorded_action(self, execution: coding_actions.Execution, identifier: str, tool: str,
                        inputs: dict | completion.PreparedCompletion, *, current):
        """Look up an exact original action receipt before inspecting a new workspace."""
        arguments = coding_actions.call_arguments(identifier, tool, inputs)
        with self.policy_guard(execution.policy, current=current) as store:
            _saved, run = _execution(store, execution)
            row = run["actions"].get(identifier)
            if row is None:
                return None
            if row.get("tool") != tool or row.get("arguments_digest") != coding_actions.digest(json.loads(arguments)):
                raise ValueError("This coding action ID already belongs to different arguments")
            return coding_actions.receipt(row)

    def action_state(self, execution: coding_actions.Execution, action: coding_actions.Action, *, current):
        """Read one original action receipt without manufacturing a retry."""
        with self.policy_guard(execution.policy, current=current) as store:
            _saved, run = _execution(store, execution)
            return coding_actions.receipt(_action(run, execution, action))

    def append(self, cid: str, role: str, content: str, *,
               reasoning: str = "", status: str = "complete") -> Conversation:
        if role not in ROLES:
            raise ValueError(f"a message is from {' or '.join(ROLES)}, not {role!r}")
        if not safe(cid):
            raise ValueError("invalid conversation id")
        with graph.opened(self.root) as store, store.transaction():
            found = graph.get(store, cid)
            if found is None:
                raise ValueError("no such chat")
            message = Message(role=role, content=content, reasoning=reasoning, status=status)
            graph.append(store, found, message, len(found.messages))
            found.messages.append(message)
            if not found.title and role == "user":
                found.title = title(content)
                graph.save(store, found)
        return found

    def update(self, cid: str, *, expected_revision: int | None = None, **changes) -> Conversation | None:
        if set(changes) - {"model", "settings", "title"}:
            raise ValueError("unknown conversation field")
        model, settings, title = (changes.get(key) for key in ("model", "settings", "title"))
        if not safe(cid):
            return None
        with graph.opened(self.root) as store, store.transaction():
            found = graph.get(store, cid)
            if found is None:
                return None
            original = found.model, found.settings
            coding = bool(found.settings["harness"] or (isinstance(settings, dict) and settings.get("harness")))
            if (coding and (model is not None or settings is not None)
                    and (type(expected_revision) is not int or expected_revision != found.policy_revision)):
                raise ValueError("The coding session changed; reload its current settings")
            if settings is not None:
                if not isinstance(settings, dict):
                    raise ValueError("conversation settings must be an object")
                found.settings = checked({**found.settings, **settings})
            if model is not None:
                if not isinstance(model, str):
                    raise ValueError("model must be text")
                found.model = model
            if title is not None:
                if not isinstance(title, str):
                    raise ValueError("title must be text")
                found.title = title.strip()[:TITLE_CHARS]
            if (found.model, found.settings) != original:
                found.policy_revision += 1
            graph.save(store, found)
        return found

    def remove(self, cid: str) -> bool:
        if not safe(cid):
            return False
        with graph.opened(self.root) as store, store.transaction():
            return graph.remove(store, cid)

    def search(self, needle: str) -> list[Conversation]:
        want = needle.strip().casefold()
        return [conversation for conversation in self.all()
                if not want or want in conversation.title.casefold()
                or any(want in message.content.casefold() for message in conversation.messages)]
