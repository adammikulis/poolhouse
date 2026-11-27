"""A plumbing-only keychain the processes of one test share, with the access rule macOS was measured to apply to Poolhouse's
executables: an item is read only by the code identity that added it, whatever signs it, and another gets -25293 with no dialog;
any identity may delete an item. No keychain of the computer is read or written.

The file named by ``POOLHOUSE_TEST_KEYCHAIN`` holds the items and the faults a test asks for: ``refuse_add``, ``locked``
(-25308 for every read) and ``refuse_check`` (a read by the `check` step is refused).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import keyring.backend
import keyring.errors

from poolhouse import keystore_guard

FOREIGN = "Can't get password from keychain: (-25293, 'plumbing-only: this program is not trusted with the item')"
LOCKED = "Can't get password from keychain: (-25308, 'plumbing-only: interaction is not allowed')"


def path() -> Path:
    return Path(os.environ["POOLHOUSE_TEST_KEYCHAIN"])


def held() -> dict:
    return json.loads(path().read_text()) if path().is_file() else {"items": {}}


def keep(state: dict) -> None:
    path().write_text(json.dumps(state))


class Keyring(keyring.backend.KeyringBackend):
    priority = 5

    def get_password(self, service, username):
        state = held()
        row = state["items"].get(f"{service}/{username}")
        if state.get("locked"):
            raise keyring.errors.KeyringError(LOCKED)
        if row is None:
            return None
        if row["owner"] != keystore_guard.code_identity() or (state.get("refuse_check") and "check" in sys.argv):
            raise keyring.errors.KeyringError(FOREIGN)
        return row["value"]

    def set_password(self, service, username, password):
        state = held()
        if state.get("refuse_add"):
            raise keyring.errors.PasswordSetError("plumbing-only: the keychain takes no new item")
        state["items"].pop(f"{service}/{username}", None)  # as keyring's macOS backend does: delete, then add
        state["items"][f"{service}/{username}"] = {"owner": keystore_guard.code_identity(), "value": password}
        keep(state)

    def delete_password(self, service, username):
        state = held()
        if state["items"].pop(f"{service}/{username}", None) is None:
            raise keyring.errors.PasswordDeleteError("plumbing-only: no such item")
        keep(state)
