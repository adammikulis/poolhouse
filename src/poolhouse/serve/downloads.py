"""What llama-server downloads when asked: its flags and environment variables, which are never passed."""

from __future__ import annotations

__all__ = ["FLAGS", "clean_env", "download_flag"]

FLAGS = frozenset({
    "-hf", "-hfr", "--hf-repo", "-hff", "--hf-file", "-hfv", "-hfrv", "--hf-repo-v", "-hffv",
    "--hf-file-v", "-hfd", "-hfrd", "--hf-repo-draft", "-hft", "--hf-token", "-mu", "--model-url",
    "--mmproj-url", "-dr", "--docker-repo"})


def download_flag(argv: list[str]) -> str:
    """The first argument of ``argv`` that makes llama-server download something, or ''."""
    return next((a.split("=", 1)[0] for a in argv if a.split("=", 1)[0] in FLAGS), "")


def _downloads(name: str) -> bool:
    return name.startswith("LLAMA_ARG_") and any(word in name for word in ("HF", "URL", "DOCKER"))


def clean_env(env: dict[str, str]) -> dict[str, str]:
    """``env`` without the ``LLAMA_ARG_`` variables that name a repository, a URL or a token."""
    return {k: v for k, v in env.items() if not _downloads(k)}
