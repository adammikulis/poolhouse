"""The model an agent runs: its id string, the harness, and how sure the record is."""

from __future__ import annotations

import re

__all__ = ["CLAIMED", "HISTORY_MAX", "INHERITED", "UNKNOWN", "VERIFIED", "clean_harness",
           "clean_model", "describe", "safe"]

VERIFIED, CLAIMED, INHERITED = "verified", "claimed", "inherited"
UNKNOWN = "model unknown"
MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,79}$")
HARNESS = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
HISTORY_MAX = 50


def clean_model(text: str) -> str:
    """``text`` as a model id; ValueError for anything outside A-Z a-z 0-9 . _ - : / + @ or over 80."""
    if not MODEL.fullmatch(text):
        raise ValueError("a model id is 1-80 characters from A-Z a-z 0-9 . _ - : / + @, "
                         "starting with a letter or digit")
    return text


def clean_harness(text: str) -> str:
    """``text`` as a harness name (a-z 0-9 . _ -, up to 40); empty stays empty."""
    if text and not HARNESS.fullmatch(text):
        raise ValueError("a harness name is up to 40 characters from a-z 0-9 . _ -")
    return text


def safe(model: str) -> str:
    """``model`` when it is a model id, else empty: the node keeps any clean line a session claims, a reader shows only ids."""
    return model if MODEL.fullmatch(model) else ""


def describe(model: str, state: str) -> str:
    """``model, state`` for a header, or ``model unknown``."""
    shown = safe(model)
    return f"{shown}, {state}" if shown else UNKNOWN
