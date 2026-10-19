"""Declarative untrusted log steps; this module performs no effects."""
from dataclasses import dataclass

from transaction import Plan, canonical, decode


@dataclass(frozen=True)
class Effect:
    kind: str
    name: str
    data: bytes


@dataclass(frozen=True)
class Step:
    effects: tuple[Effect, ...]
    result: bytes | None = None


def _record(plan, phase, cursor):
    return canonical({"version": 1, "plan": plan.digest(), "nonce": plan.value()["nonce"],
                      "phase": phase, "cursor": cursor,
                      "result": plan.result().decode("ascii") if phase == "COMMITTED" else None})


def _persist(data):
    return (Effect("write-exclusive-temp", "log.next", data),
            Effect("fsync-file", "log.next", b""),
            Effect("rename-log", "log", b""),
            Effect("fsync-directory", "transaction", b""))


def next_step(plan: Plan, durable_log: bytes | None, observed_files: dict[str, str], *, existing_authority=False) -> Step:
    """Return model effects from untrusted observations, without conferring authority."""
    artifacts = plan.artifacts()
    if type(existing_authority) is not bool or existing_authority or type(observed_files) is not dict:
        raise ValueError("Existing or invalid authority state.")
    expected = {item.path: item.digest for item in artifacts}
    if not set(observed_files) <= expected.keys() or any(expected[k] != v for k, v in observed_files.items()):
        raise ValueError("Unexpected installed content.")
    if durable_log is None:
        if observed_files:
            raise ValueError("Missing log.")
        return Step(_persist(_record(plan, "PREPARED", 0)))
    value = decode(durable_log)
    if type(value) is not dict or set(value) != {"version", "plan", "nonce", "phase", "cursor", "result"}:
        raise ValueError("Invalid log.")
    cursor = value["cursor"]
    phase = value["phase"]
    if type(cursor) is not int or not 0 <= cursor <= len(artifacts) or phase not in ("PREPARED", "INSTALLING", "COMMITTED"):
        raise ValueError("Invalid log.")
    if (phase == "PREPARED" and cursor != 0) or (phase == "INSTALLING" and cursor == 0) or (phase == "COMMITTED" and cursor != len(artifacts)):
        raise ValueError("Invalid log prefix.")
    if _record(plan, phase, cursor) != durable_log:
        raise ValueError("Wrong transaction log.")
    prefix = {item.path for item in artifacts[:cursor]}
    allowed = prefix | ({artifacts[cursor].path} if cursor < len(artifacts) else set())
    if not prefix <= observed_files.keys() or not observed_files.keys() <= allowed:
        raise ValueError("Invalid installed prefix.")
    if phase == "COMMITTED":
        return Step((), plan.result())
    if cursor == len(artifacts):
        return Step(_persist(_record(plan, "COMMITTED", cursor)))
    item = artifacts[cursor]
    effects = (Effect("stage-exact-artifact", item.path, item.digest.encode("ascii")),
               Effect("fsync-file", item.path, b""),
               Effect("fsync-directory", "payload", b""),
               Effect("readback-exact-artifact", item.path, item.digest.encode("ascii")))
    return Step(effects + _persist(_record(plan, "INSTALLING", cursor + 1)))
