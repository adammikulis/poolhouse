"""A test never makes or downloads a llama.cpp build: the recovery for a binary nothing vouches for refuses at once.

`poolhouse.serve.build_recover` builds from source when a compiler is installed and downloads a release otherwise, and
both reach GitHub. A test that wants the recovery to succeed replaces `build_from_source` or `build_from_release` with a
copy of a release already on disk; one that wants it to fail gets this. An autouse fixture, so a broken implementation
cannot reach the network from a test either.
"""

from __future__ import annotations

import pytest

from poolhouse.serve import build_recover
from poolhouse.serve.build_paths import BuildFailed


@pytest.fixture(autouse=True)
def no_build_is_made_or_fetched(monkeypatch):
    def refused(args):
        raise BuildFailed("a test never builds or downloads llama.cpp")

    monkeypatch.setattr(build_recover, "can_build_from_source", lambda: False)
    monkeypatch.setattr(build_recover, "build_from_release", refused)
