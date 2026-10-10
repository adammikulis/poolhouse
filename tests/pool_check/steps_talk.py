"""Steps about members talking to one another: a direct message, a branch claim held pool-wide."""

from __future__ import annotations

from pool_check.world import World, eventually


def a_direct_message_reaches_its_session(world: World) -> None:
    """A direct message to a session on another device arrives in that session's inbox, and in no one else's."""
    world.fresh()
    world.pool_of(world.devices)
    sender, target = world.first, world.devices[-1]
    target.post("hello from the target")

    def address():
        """How the first device's board names the target's session: the sender of the entry the target wrote."""
        seen = [e["sender"] for e in sender.call("read", "demo", kind="message", limit=500)["entries"] if e["fields"].get("body") == "hello from the target"]
        return seen[0] if seen else None
    where = eventually(address, 30, f"the first device to hold the entry {target.name} wrote")
    eventually(lambda: sender.post("a private word", to=target.session), 30, f"the first device to accept a message to {target.session} (shown there as {where})")
    inbox = eventually(lambda: [e["fields"]["body"] for e in target.call("read", "demo", inbox=True, kind="message")["entries"]], 30, f"{target.name} to receive the direct message")
    assert "a private word" in inbox, inbox
    for other in world.devices[1:-1]:
        read = [e["fields"]["body"] for e in other.call("read", "demo", inbox=True, kind="message")["entries"]]
        assert "a private word" not in read, f"{other.name} read a direct message addressed to {target.session}"


def a_claim_is_held_pool_wide(world: World) -> None:
    """A branch claimed on one device is refused to every other device (naming the holder), and can be taken once it is released."""
    world.fresh()
    world.pool_of(world.devices)
    first, rest = world.devices[0], world.devices[1:]
    made = first.call("claim", "demo", kind="branch", key="pool-check/branch", ttl_s=600)
    assert made["changed"] is True, made
    for other in rest:
        def refused(other=other):
            out = other.try_call("claim", "demo", kind="branch", key="pool-check/branch", ttl_s=600)
            if out["ok"]:
                other.call("release", "demo", kind="branch", key="pool-check/branch")
                raise AssertionError(f"{other.name} took a branch {first.name} holds")
            return out
        out = eventually(refused, 30, f"{other.name} to be refused the branch {first.name} holds")
        assert "held by" in out["error"] and "of yours" not in out["error"], f"the refusal does not say who holds it: {out['error']}"
    first.call("release", "demo", kind="branch", key="pool-check/branch")
    taken = eventually(lambda: rest[0].call("claim", "demo", kind="branch", key="pool-check/branch", ttl_s=600), 30, f"{rest[0].name} to take the branch after it was released")
    assert taken["changed"] is True
