"""A flag that is set inside a block, for the context that entered it."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

__all__ = ["ScopedFlag"]


class ScopedFlag:
    """Unset until a `held` block is entered; set inside it, and as it was before once the block ends."""

    def __init__(self, name: str) -> None:
        self._set: ContextVar[bool] = ContextVar(name, default=False)

    @contextlib.contextmanager
    def held(self) -> Iterator[None]:
        """Set the flag for the code inside."""
        token = self._set.set(True)
        try:
            yield
        finally:
            self._set.reset(token)

    def is_set(self) -> bool:
        """Whether the caller is inside a `held` block."""
        return self._set.get()
