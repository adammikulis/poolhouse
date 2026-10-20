"""Steps about the level of the person's key: a pool a computer's key governs, what only a computer's key signs waits, and the floors.

The key is planted in each node's person keys at the level a step names, the way `wire.plant_key` writes it: the level is recorded, and
nothing behind it is a phone, a protected helper or the computer's own prompt. These steps prove what the pool does with a key at that
level, not that the level was earned.
"""

from __future__ import annotations

from pool_check.device import Refused
from pool_check.world import World, eventually

PLANTED = "the person's key was planted at level {level}, not custody"
SEVEN_DAYS_S = 7 * 24 * 3600
ONE_DAY_S = 24 * 3600


def founds_computer_only(world: World) -> str:
    """With the person's key planted at level computer the first device founds a pool, every other device joins it, `pool_status` reports every floor at computer; a key planted at weak founds nothing."""
    world.fresh(level="computer")
    world.pool_of(world.devices)
    gov = world.first.pool()["governance"]
    computer = {"routine": "computer", "sensitive": "computer", "governance": "computer"}
    assert (gov["floors"], gov["levels"], gov["approved_from"]) == (computer, ["computer"], "this computer"), f"{world.first.name}: {gov}"
    for device in world.devices:
        assert len(device.others()) == world.count - 1, f"{device.name} lists {len(device.others())} other members in a pool a computer key governs"
    world.fresh(level="weak")
    try:
        world.found(world.first)
    except Refused as refused:
        assert "approved from a computer or a phone" in str(refused), f"a key planted at weak was refused for another reason: {refused}"
    else:
        raise AssertionError("a key planted at weak founded a pool")
    assert world.first.pool()["governance"]["mode"] == "unfounded", "a refused founding left a pool behind"
    return f"{world.count} devices in one pool whose floors are all computer; a weak key founds nothing; " + PLANTED.format(level="computer")


def removes_computer_only(world: World) -> str:
    """In a pool a computer's key governs the first device takes the key's signed removal of one member, and every other member records it."""
    world.fresh(level="computer")
    world.pool_of(world.devices)
    victim, rest = world.others[-1], [d for d in world.devices if d is not world.others[-1]]
    world.remove(world.first, victim)
    for device in rest:
        eventually(lambda d=device: (d.member(victim.fingerprint) or {}).get("status") == "revoked", 30, f"{device.name} to record the removal of {victim.name}")
    return f"{victim.name} was removed by a computer-level key and {len(rest)} members record it; " + PLANTED.format(level="computer")


def a_hold_waits_what_a_computer_alone_signed(world: World) -> str:
    """A change to the pool's holds that only a computer-level key signed waits seven days and says so on its card; the same change signed by a phone-level key takes effect at once."""
    world.fresh(level="computer")
    first = world.first
    world.found(first)
    draft = first.admin("pool_draft", kind="set_holds", body={"admin_add_h": 48})
    assert "LEVEL: " in draft["summary"] and "waits 7 days" in draft["summary"], f"the card does not say it waits: {draft['summary']}"
    first.admin("pool_submit", act=world.person.signed(draft))
    gov = first.pool()["governance"]
    wait = (gov["pending"] or {}).get("takes_effect_in_s", 0)
    assert SEVEN_DAYS_S - 600 < wait <= SEVEN_DAYS_S and gov["holds"]["admin_add_h"] == 24, f"a change signed by a computer alone is not waiting seven days: {gov}"
    world.fresh(level="phone")
    first = world.first
    world.found(first)
    draft = first.admin("pool_draft", kind="set_holds", body={"admin_add_h": 48})
    assert "waits 7 days" not in draft["summary"], f"a phone-level pool's card says it waits: {draft['summary']}"
    first.admin("pool_submit", act=world.person.signed(draft))
    gov = first.pool()["governance"]
    assert (gov["epoch"], gov["pending"], gov["holds"]["admin_add_h"]) == (2, None, 48), f"a change signed by a phone-level key was held: {gov}"
    return f"seven days ({wait} s left) for a computer alone, none for a phone; " + PLANTED.format(level="computer and phone")


def a_floor_is_held_when_lowered_and_never_a_typed_yes(world: World) -> str:
    """In a pool a phone-level key governs, a floor of a typed yes is refused, lowering the governance floor waits a day and the same key cancels it, and the floor stays."""
    world.fresh(level="phone")
    first = world.first
    world.found(first)
    phone = {"routine": "phone", "sensitive": "phone", "governance": "phone"}
    assert first.pool()["governance"]["floors"] == phone, f"a pool a phone-level key founded has other floors: {first.pool()['governance']}"
    try:
        first.admin("pool_submit", act=world.person.signed(first.admin("pool_draft", kind="set_floors", body={"routine": "weak"})))
    except Refused as refused:
        assert "never a typed yes" in str(refused), f"a floor of a typed yes was refused for another reason: {refused}"
    else:
        raise AssertionError("a floor of a typed yes was taken")
    draft = first.admin("pool_draft", kind="set_floors", body={"governance": "computer"})
    assert "LEVEL: " in draft["summary"], f"the card does not say who approves it: {draft['summary']}"
    first.admin("pool_submit", act=world.person.signed(draft))
    gov = first.pool()["governance"]
    pending = gov["pending"] or {}
    wait = pending.get("takes_effect_in_s", 0)
    assert ONE_DAY_S - 600 < wait <= ONE_DAY_S and gov["floors"] == phone, f"lowering a floor is not waiting a day: {gov}"
    cancel = first.admin("pool_draft", kind="cancel", body={"act": pending["acts"][0]})
    first.admin("pool_submit", act=world.person.signed(cancel))
    gov = first.pool()["governance"]
    assert gov["pending"] is None and gov["floors"] == phone, f"a cancelled lowering left a change behind: {gov}"
    return f"a floor of a typed yes is refused; lowering a floor waits a day ({wait} s left) and its cancel leaves every floor at phone; " + PLANTED.format(level="phone")
