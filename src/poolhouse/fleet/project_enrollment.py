"""Admission of a local project registration."""


def local(opening, source: str) -> bool:
    """Whether a sealed, authenticated request came from this machine's loopback."""
    return source in {"127.0.0.1", "::1"} and opening is not None and bool(opening[2])
