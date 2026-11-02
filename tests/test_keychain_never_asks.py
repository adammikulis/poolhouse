"""A test run cannot make macOS show a Keychain dialog: whatever reaches the Keychain from this process fails instead of asking."""

from __future__ import annotations

import ctypes
import sys

import pytest


def interaction_allowed() -> bool:
    framework = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    allowed = ctypes.c_ubyte(1)
    framework.SecKeychainGetUserInteractionAllowed.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    assert framework.SecKeychainGetUserInteractionAllowed(ctypes.byref(allowed)) == 0
    return bool(allowed.value)


@pytest.mark.skipif(sys.platform != "darwin", reason="the Keychain is macOS's")
def test_the_test_process_may_not_show_a_keychain_dialog() -> None:
    assert not interaction_allowed()
