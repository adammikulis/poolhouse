"""Literal managed selection checks; no repository code is collected or run."""
from __future__ import annotations

from dataclasses import replace

import pytest

from poolhouse.api import node_contract as contract
from poolhouse.api.managed_testing import resolve_unit
from poolhouse.testfarm.model.config import ConfigError
from poolhouse.testfarm.model.units import Unit

PROFILE = contract.TestingProfile.from_wire({"kind": "maintained_pytest_explicit_unit_v1", "value": {}})


def test_literal_parameter_selector_preserves_group_order_and_metadata():
    selected = Unit("tests/test_items.py::test_item[a*b]", ("test_a", "test_b"), deps=("data.txt",))
    catalogue = (Unit("other", ("test_other",)), selected)
    assert resolve_unit(PROFILE, contract.TestingExactSelector(selected.name), catalogue) is selected
    assert selected.ids == ("test_a", "test_b")
    for text in ("tests/test_items.py", "tests/*", "all", "test_a", "other --extra"):
        with pytest.raises(ConfigError, match="not in the catalogue"):
            resolve_unit(PROFILE, contract.TestingExactSelector(text), catalogue)


@pytest.mark.parametrize("catalogue", [
    (), (Unit("same", ("a",)), Unit("same", ("b",))),
    (Unit("one", ("a",)), Unit("two", ("a",))),
    (Unit("one", ("a", "a")),), (Unit("one", ()),),
    (Unit("one", ("-flag",)),), (Unit("bad\nname", ("a",)),),
])
def test_invalid_or_ambiguous_catalogue_refuses_even_if_selected_early(catalogue):
    with pytest.raises(ConfigError):
        resolve_unit(PROFILE, contract.TestingExactSelector("one"), catalogue)


def test_plain_data_cannot_supply_profile_or_selector_authority():
    unit = Unit("one", ("a",))
    for profile in ("maintained_pytest_explicit_unit_v1", {"approved": True}, None):
        with pytest.raises(ConfigError, match="unsupported"):
            resolve_unit(profile, contract.TestingExactSelector("one"), (unit,))
    for selector in ("one", {"argv": ["one"]}, None, contract.TestingExactSelector("-flag")):
        with pytest.raises(ConfigError, match="exact selector"):
            resolve_unit(PROFILE, selector, (unit,))
    assert resolve_unit(PROFILE, contract.TestingExactSelector("one"), (replace(unit, ids=("a", "b")),)).ids == ("a", "b")
