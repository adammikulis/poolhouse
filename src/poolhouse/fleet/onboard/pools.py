"""The pools a person can join: the ones this machine is in, the ones daemons on the network offer, and the question that picks one."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from poolhouse.log import say

from .. import discovery as disc
from .joining import Offer, find_pools

__all__ = ["Choice", "Pool", "format_pools", "known_pools", "pick_pool"]

THIS_MACHINE = "this machine"


@dataclass(frozen=True, slots=True)
class Pool:
    """A pool, and the machines that hold it."""

    name: str
    machines: tuple[str, ...]
    mine: bool = False
    method: str = "passphrase"

    def public(self) -> dict[str, object]:
        return {"name": self.name, "machines": list(self.machines), "mine": self.mine, "method": self.method}


@dataclass(frozen=True, slots=True)
class Choice:
    """The pool a person picked, and whether it already exists."""

    name: str
    existing: bool


def known_pools(pool_key_path: Path | str | None = None, *, timeout_s: float = 1.5,
                   port: int | None = None, self_names: tuple[str, ...] = (),
                   finder: Callable[..., list[Offer]] | None = None) -> list[Pool]:
    """Pools this machine is in, then those other daemons on the network offer to take a machine into."""
    held: dict[str, list[str]] = {m.group: [THIS_MACHINE] for m in disc.memberships(pool_key_path)}
    mine = set(held)
    methods: dict[str, str] = {}
    for offer in sorted((finder or find_pools)(timeout_s=timeout_s, port=port), key=lambda o: (o.group, o.machine)):
        names = held.setdefault(offer.group, [])
        methods.setdefault(offer.group, offer.method)
        if offer.method == "passphrase":
            methods[offer.group] = "passphrase"
        if offer.machine not in names and offer.machine not in self_names:
            names.append(offer.machine)
    return [Pool(name, tuple(machines), name in mine, methods.get(name, "passphrase"))
            for name, machines in held.items()]


def format_pools(pools: list[Pool]) -> list[str]:
    """One numbered line per pool: ``1) lab - studio, larch``."""
    return [f"{i}) {c.name} - {', '.join(c.machines) or 'no machine answered'}"
            for i, c in enumerate(pools, 1)]


def pick_pool(pools: list[Pool], ask: Callable[[str], str] | None = None,
                 tell: Callable[[str], None] = say, *, default: str = disc.DEFAULT_POOL) -> Choice:
    """Ask which pool to join: a number from the list, ``n`` or a typed name for a new one, or enter for ``default`` when none is listed."""
    ask = ask or input
    names = {c.name for c in pools}
    if pools:
        tell("Pools found:")
        for line in format_pools(pools):
            tell(f"  {line}")
        tell("  n) a new pool")
        prompt = "  Pick a number, or type a name: "
    else:
        prompt = f"  Pool name [{default}]: "
    while True:
        try:
            typed = ask(prompt).strip()
            if typed.lower() == "n" and pools:
                typed = ask("  Name of the new pool: ").strip()
        except EOFError:
            raise disc.DiscoveryError("No pool name was given.") from None
        if typed.isdigit():
            number = int(typed)
            if 1 <= number <= len(pools):
                return Choice(pools[number - 1].name, True)
            tell(f"  There is no pool {number}.")
            continue
        if not typed and not pools:
            typed = default
        try:
            name = disc.check_name(typed)
        except disc.DiscoveryError as why:
            tell(f"  {why}")
            continue
        return Choice(name, name in names)
