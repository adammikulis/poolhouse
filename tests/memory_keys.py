"""A keystore held in memory for the memory tests, installed through keyring's own interface so
the real key code runs against it."""

from __future__ import annotations

import sys

import keyring
import pytest
from keyring.backend import KeyringBackend

from poolhouse import authority, caller


class MemoryRing(KeyringBackend):
    """The keyring interface over a dict."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.held: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.held.get((service, username))

    def set_password(self, service, username, password):
        self.held[(service, username)] = password

    def delete_password(self, service, username):
        self.held.pop((service, username), None)


@pytest.fixture(autouse=True)
def ring():
    before = keyring.get_keyring()
    fake = MemoryRing()
    keyring.set_keyring(fake)
    yield fake
    keyring.set_keyring(before)


@pytest.fixture
def person():
    """A process no agent started (the suite clears the markers); pytest captures both streams, so neither is a terminal."""
    assert not caller.marked()
    assert not caller.is_terminal(sys.stdin) and not caller.is_terminal(sys.stdout)


@pytest.fixture
def held_from_agents(monkeypatch):
    """Every gate, `memory.admin` among them, held back from agents (the suite's own default, set here so the test says so)."""
    monkeypatch.setenv(authority.FLOOR_ENV, authority.PERSON)
