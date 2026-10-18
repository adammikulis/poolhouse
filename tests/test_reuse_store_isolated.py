"""A test finds the reuse store and the job table in a temporary directory, never the account's own."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

from poolhouse.activity import reuse


def test_the_reuse_store_a_test_reaches_is_not_the_accounts():
    account_cache = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve() / ".cache"
    assert not reuse.reuse_base().resolve().is_relative_to(account_cache)
