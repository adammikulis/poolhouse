"""``--apply`` and ``--persist`` take megabytes or a size in gigabytes."""

from __future__ import annotations

import pytest

from poolhouse.serve import wired_cli


@pytest.mark.parametrize(("given", "mb"), [("117G", 119808), ("117g", 119808), ("117GB", 119808), ("117gb", 119808), ("117Gb", 119808), ("117gB", 119808),
                                           ("117GiB", 119808), ("117GIB", 119808), ("117 GiB", 119808), ("96.5G", 98816),
                                           ("119808", 119808), (" 8192 ", 8192)])
def test_a_size_in_gigabytes_or_megabytes_is_read_as_megabytes(given, mb):
    assert wired_cli.megabytes(given) == mb


@pytest.mark.parametrize("given", ["", "G", "-5", "117T", "twelve", "1e3"])
def test_anything_else_is_not_a_size(given):
    assert wired_cli.megabytes(given) is None
