"""Compile one literal managed selection from catalogue data; never execute or admit code.

The fixed profile tag and returned Unit are untrusted descriptions. This module does
not recognize source command templates, measure source, or issue a launch permit.
"""
from __future__ import annotations

from collections.abc import Sequence

from poolhouse.api.node_contract import TestingExactSelector, TestingProfile
from poolhouse.testfarm.model.config import ConfigError
from poolhouse.testfarm.model.ids import refusal
from poolhouse.testfarm.model.units import Unit

__all__ = ["resolve_unit"]


def resolve_unit(profile: TestingProfile, selector: TestingExactSelector, catalogue: Sequence[Unit]) -> Unit:
    """Resolve exactly one unit by name, preserving its ordered grouped IDs.

    No wildcard, prefix, shell, argument or whole-catalogue interpretation exists.
    A parameter ID containing punctuation is matched literally. The caller must
    independently establish source custody, collection admission and execution
    permission before this data can be used by a managed execution consumer.
    """
    if type(profile) is not TestingProfile or profile.to_wire() != {
        "kind": "maintained_pytest_explicit_unit_v1", "value": {}
    }:
        raise ConfigError("unsupported managed testing profile")
    if type(selector) is not TestingExactSelector or refusal(selector.value) is not None:
        raise ConfigError("managed testing requires one valid exact selector")
    names: set[str] = set()
    seen_ids: set[str] = set()
    selected = None
    for unit in catalogue:
        if type(unit) is not Unit or refusal(unit.name) is not None or unit.name in names:
            raise ConfigError("managed testing catalogue has invalid or duplicate unit names")
        names.add(unit.name)
        if not isinstance(unit.ids, tuple) or not unit.ids:
            raise ConfigError("managed testing unit has no frozen ordered IDs")
        for item in unit.ids:
            if refusal(item) is not None or item in seen_ids:
                raise ConfigError("managed testing catalogue has invalid or duplicate IDs")
            seen_ids.add(item)
        if unit.name == selector.value:
            selected = unit
    if selected is None:
        raise ConfigError("exact managed testing unit is not in the catalogue")
    return selected
