"""Where a test makes what it needs outside its own `tmp_path`, in a coding box and outside one.

A coding box writes its own temporary folder and not the shared ``/tmp``, so no test names ``/tmp`` itself.
"""

from __future__ import annotations

import os
import tempfile

__all__ = ["SHORT", "short_temp"]

SHORT = 40
"""The longest root that leaves the path of a Unix socket, 104 bytes on macOS, room for the folders a test makes in it."""


def short_temp() -> str:
    """The folder to make a folder with a short path in, as a Unix socket's must be: this process's temporary folder where its path is short
    (a coding box's, Linux's), else the shared ``/tmp``, since macOS gives an account a long one. Windows has only its own."""
    own = tempfile.gettempdir()
    if os.name != "posix" or len(os.path.realpath(own)) <= SHORT:
        return own
    return "/tmp"
