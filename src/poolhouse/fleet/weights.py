"""Which file in a hub repository is the model, and the error when one cannot be got."""

from __future__ import annotations

import re

from poolhouse import net
from poolhouse.http import ServerError

__all__ = [
    "BESIDE",
    "QUANTS",
    "ModelError",
    "is_a_piece",
    "is_beside",
    "quant_in",
    "reference",
    "repo_files",
]


class ModelError(RuntimeError):
    pass


QUANTS = ("q4_k_m", "q4_k_s", "q4_1", "q4_0", "q5_k_m", "q8_0")


LISTING = "https://huggingface.co/api/models"


def reference(source: str) -> str:
    """``hf:owner/repo/file.gguf``, or ``hf:owner/repo`` answered with its Q4 build, as `hub.pull` takes it.

    Only a Hugging Face reference is a source: it is fetched at its commit, from a publisher a person
    has approved or Poolhouse knows.
    """
    source = source.strip()
    if not source.startswith("hf:"):
        raise ModelError(f"{source!r} is not a Hugging Face reference (hf:owner/repo/file.gguf)")
    parts = source[3:].strip("/").split("/")
    if len(parts) == 2:
        parts = [parts[0], parts[1], quant_in(parts[0], parts[1])]
    if len(parts) < 3:
        raise ModelError(f"{source!r} should look like hf:owner/repo/file.gguf")
    return "hf:" + "/".join(parts)


def repo_files(owner: str, repo: str) -> list[str]:
    """Every file Hugging Face lists in a repository."""
    try:
        listed = net.default().json(f"{LISTING}/{owner}/{repo}",
                                    net.Ask(purpose="model hub", tries=3))
    except (ServerError, OSError, ValueError) as exc:
        raise ModelError(f"could not read hf:{owner}/{repo}: {exc}") from None
    return [str(f.get("rfilename", "")) for f in listed.get("siblings") or []]


def quant_in(owner: str, repo: str) -> str:
    """The file to take from a repository nobody named a file in."""
    files = repo_files(owner, repo)
    whole = [f for f in files
             if f.lower().endswith(".gguf") and not is_a_piece(f)
             and not is_beside(f)]
    if not whole:
        raise ModelError(f"hf:{owner}/{repo} holds no single-file gguf")
    for want in QUANTS:
        for name in sorted(whole):
            if want in name.lower():
                return name
    return sorted(whole)[0]


# Files that sit beside a model rather than being one: a vision projector, a
# multi-token-prediction head, a draft model, an importance matrix.
BESIDE = ("mmproj", "mtp", "draft", "imatrix", "vocab", "lora", "adapter")


def is_a_piece(name: str) -> bool:
    """A shard such as ``model-00001-of-00003.gguf``, which is no use on its own."""
    return bool(re.search(r"-\d{5}-of-\d{5}", name))


def is_beside(name: str) -> bool:
    """Whether a file is an accessory rather than the model itself."""
    stem = name.rsplit("/", 1)[-1].lower()
    if stem.endswith("-mtp.gguf") and not stem.startswith("mtp-"):
        return any(word in stem for word in BESIDE if word != "mtp")
    return any(word in stem for word in BESIDE)
