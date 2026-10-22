"""The suite-wide guard: no test may resolve the keyring to a backend that talks to the machine's own keystore.

`keyring.get_keyring` is wrapped for the whole session, so a test (or the code it runs) that ends up on the real macOS Keychain,
Secret Service, Windows Credential Manager or kwallet backend fails there, at the call, instead of raising a prompt on the
person's screen. A fake that only borrows a real backend's module name (as `tests/test_real_machine.py` does to test the
refusal) is not that class and passes.
"""
from __future__ import annotations

import sys

import pytest

NOT_REAL = ("keyring.backends.fail", "keyring.backends.null", "keyring.backends.chainer")


def is_the_real_thing(ring: object) -> bool:
    """Whether ``ring``, or a backend a chainer holds, is a class `keyring` ships that talks to the machine's own keystore."""
    for part in getattr(ring, "backends", None) or [ring]:
        kind = type(part)
        module = sys.modules.get(kind.__module__)
        if (kind.__module__.startswith("keyring.backends.") and not kind.__module__.startswith(NOT_REAL)
                and module is not None and getattr(module, kind.__name__, None) is kind):
            return True
    return False


@pytest.fixture(scope="session", autouse=True)
def _keyring_never_resolves_a_real_backend():
    import keyring

    from poolhouse import keystore_guard

    keystore_guard.forbid_prompts()  # whatever still reaches the Keychain from a test process fails there, it does not ask

    resolve = keyring.get_keyring

    def guarded():
        ring = resolve()
        if is_the_real_thing(ring):
            raise RuntimeError(f"a test resolved the real keyring backend {type(ring).__module__}.{type(ring).__name__}: "
                               "install a fake (tests/memory_keys.py or tests/onboard_support.py) first")
        return ring

    keyring.get_keyring = guarded
    yield
    keyring.get_keyring = resolve
