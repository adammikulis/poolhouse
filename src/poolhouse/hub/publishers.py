"""The publishers Poolhouse knows, the rule that matches a repository to one, and the name markers that
ask a person even about a known publisher."""

from __future__ import annotations

import re
import unicodedata

__all__ = ["HOSTS", "MARKERS", "ORDER", "TRUSTED", "marker", "namespace", "official", "trusted", "valid_name"]

ORDER = ("Qwen", "unsloth", "ggml-org", "google", "bartowski", "lmstudio-community", "mlx-community")
"""The known publishers, best first. A name is matched to one exactly, character for character."""

TRUSTED = frozenset(ORDER)

HOSTS = frozenset({"huggingface.co", "hf.co"})
"""The hosts of the official Hub: a known publisher is known only on one of them."""

_NAME = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9_])?")

MARKERS = ("abliterat", "ablit", "obliterat", "uncensor", "decensor", "unfilter", "norefusal", "refusalfree",
           "withoutrefusal", "jailbr", "unaligned", "unrestricted", "unleashed", "unchained", "nsfw", "unsafe",
           "lorablated", "dolphin", "orthogonaliz", "heretic")
"""Name fragments, with case, separators, lookalikes and leet spelling removed, of models changed to answer what
the original refuses."""


def valid_name(name: str) -> bool:
    """Whether ``name`` can be a Hub owner or repository name: ASCII letters, digits, ``.``, ``-``
    and ``_``, not starting or ending with a separator, with no ``..`` or ``--``."""
    return bool(_NAME.fullmatch(name)) and ".." not in name and "--" not in name


def namespace(repo: str) -> str:
    """The owner of ``owner/name``; ``ValueError`` unless both halves are valid Hub names."""
    owner, slash, name = repo.partition("/")
    if not (slash and valid_name(owner) and valid_name(name)):
        raise ValueError(f"{repo!r} is not a Hugging Face repository name (owner/name)")
    return owner


def trusted(repo: str) -> bool:
    """Whether the owner of ``repo`` is a known publisher: an exact match of the whole owner."""
    try:
        return namespace(repo) in TRUSTED
    except ValueError:
        return False


_LOOKALIKES = str.maketrans("\u0430\u0435\u043e\u0440\u0441\u0456\u0443\u0445\u0458\u0455", "aeopciuxjs")
_LEET = str.maketrans("1l|!034@57$", "iiiioeaasts")


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).casefold().translate(_LOOKALIKES).translate(_LEET)
    return "".join(c for c in folded if not unicodedata.combining(c) and c.isalnum())


_FOLDED = tuple((word, _fold(word)) for word in MARKERS)


def official(host: str) -> bool:
    """Whether ``host`` (a host name, with or without a port) is the official Hub."""
    name = host.lower().rsplit("@", 1)[-1]
    name = name.split(":", 1)[0] if name.count(":") == 1 else name
    return name.rstrip(".") in HOSTS


def marker(*names: str) -> str:
    """The first name marker found in ``names`` (a repository or file name), or ''."""
    flat = "|".join(_fold(name) for name in names)
    return next((word for word, folded in _FOLDED if folded in flat), "")
