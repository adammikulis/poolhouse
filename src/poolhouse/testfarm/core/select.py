"""Which units a command line names: sets, ids, globs over ids and unit names."""

from __future__ import annotations

from collections.abc import Sequence

from poolhouse.testfarm.model import Config, ConfigError, SetSpec, Unit, globs

__all__ = ["select"]


def _in_set(unit: Unit, chosen: SetSpec) -> bool:
    return ((not chosen.labels or any(word in unit.labels for word in chosen.labels))
            and not any(word in unit.labels for word in chosen.not_labels)
            and (not chosen.ids or any(globs.match(pattern, one) for pattern in chosen.ids for one in unit.ids)))


def _named(unit: Unit, selector: str) -> bool:
    return unit.name == selector or globs.match(selector, unit.name) or any(globs.match(selector, one) for one in unit.ids)


def select(units: Sequence[Unit], config: Config, selectors: Sequence[str] = ()) -> tuple[Unit, ...]:
    """The units the selectors name: the sets (``@NAME``) give the pool, every other selector (an id, a glob over ids, a unit name) keeps
    the units it matches, and no selector means every unit. A selection that matches nothing is an error."""
    sets = {chosen.name: chosen for chosen in config.sets}
    wanted = [selector[1:] for selector in selectors if selector.startswith("@")]
    for name in wanted:
        if name not in sets:
            raise ConfigError(f"no set named {name!r}; the sets are {', '.join(sorted(sets)) or 'none'}")
    pool = [unit for unit in units if not wanted or any(_in_set(unit, sets[name]) for name in wanted)]
    others = [selector for selector in selectors if not selector.startswith("@")]
    chosen = tuple(unit for unit in pool if not others or any(_named(unit, selector) for selector in others))
    if not chosen and selectors:
        raise ConfigError("the selection " + " ".join(selectors) + " matches no unit")
    return chosen
