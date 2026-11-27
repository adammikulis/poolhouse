"""Finding llama.cpp's converter and quantiser."""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
from pathlib import Path

from poolhouse import home, lazy, provision
from poolhouse.files import promote
from poolhouse.httpguard import Refused
from poolhouse.lock import only_one
from poolhouse.log import warn
from poolhouse.net import git as netgit

CONVERTER_NAME = "convert_hf_to_gguf.py"
WAIT_S = 1800.0
"""How long a second process waits for the one already fetching."""


def llama_cpp_src() -> Path:
    """Where `ensure_converter` clones llama.cpp."""
    return home.cache("llama.cpp-src")


def source_dirs() -> tuple[Path, ...]:
    """Every directory a llama.cpp checkout is looked for in, in order."""
    account = home.user_home()
    return (
        llama_cpp_src(),
        account / ".local" / "opt" / "llama.cpp-src",
        account / "llama.cpp",
        account / ".unsloth" / "llama.cpp",
        Path("/opt/homebrew/share/llama.cpp"),
        Path("/usr/local/share/llama.cpp"),
    )

QUANTIZE_NAMES = ("llama-quantize", "llama-quantize.exe")


class ToolNotFound(RuntimeError):
    """A llama.cpp tool could not be located."""


def find_converter(explicit: str | Path | None = None) -> Path | None:
    """Locate ``convert_hf_to_gguf.py``. ``None`` if it is nowhere."""
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()

    for key in ("LLAMA_CPP_ROOT", "LLAMA_CPP_DIR"):
        if root := os.environ.get(key):
            candidate = Path(root).expanduser() / CONVERTER_NAME
            if candidate.is_file():
                return candidate.resolve()

    for directory in source_dirs():
        candidate = directory / CONVERTER_NAME
        if candidate.is_file():
            return candidate.resolve()

    return None


def _not_found(why: str) -> ToolNotFound:
    return ToolNotFound(
        f"{CONVERTER_NAME} not found. Looked at $LLAMA_CPP_ROOT, $LLAMA_CPP_DIR and:\n"
        + "\n".join(f"  {d}" for d in source_dirs()) + f"\n{why}")


def require_converter(explicit: str | Path | None = None) -> Path:
    """``find_converter``, fetching llama.cpp's source first when no path is named and none is here. `ToolNotFound`
    names every directory tried and why it could not be fetched."""
    found = find_converter(explicit)
    if found is not None:
        return found
    if explicit:
        raise _not_found(f"{explicit} was named, so nothing was fetched in its place.")
    return ensure_converter()


def ensure_converter(*, ref: str = "master") -> Path:
    """Locate the converter, shallow-cloning llama.cpp into the cache if it is absent. One process clones, beside the
    place it ends up in, so an interrupted clone is never mistaken for a checkout."""
    if found := find_converter():
        return found
    source = llama_cpp_src()
    source.parent.mkdir(parents=True, exist_ok=True)
    with only_one(source.with_name(source.name + ".lock"), wait=True, timeout=WAIT_S, announce=warn, note="fetching llama.cpp"):
        if found := find_converter():
            return found
        if refused := provision.download_refusal():
            raise _not_found(f"llama.cpp's source was not fetched: {refused}")
        partial = source.with_name(source.name + ".part")
        shutil.rmtree(partial, ignore_errors=True)
        warn(f"fetching llama.cpp's source into {source}, needed for the first time")
        try:
            netgit.clone("https://github.com/ggml-org/llama.cpp", partial, branch=ref)
        except (netgit.GitFailed, Refused) as exc:
            raise ToolNotFound(f"llama.cpp could not be fetched: {exc}") from exc
        if not (partial / CONVERTER_NAME).is_file():
            raise _not_found("the llama.cpp source fetched holds no converter.")
        shutil.rmtree(source, ignore_errors=True)
        promote(partial, source)
    if found := find_converter():
        return found
    raise _not_found("the fetched source was not found afterwards.")


def find_quantize(explicit: str | Path | None = None) -> Path | None:
    """Locate ``llama-quantize``. Unlike the converter, this one ships as a binary."""
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()

    for key in ("LLAMA_CPP_ROOT", "LLAMA_CPP_DIR"):
        if root := os.environ.get(key):
            base = Path(root).expanduser()
            for sub in ("", "build/bin", "bin"):
                for name in QUANTIZE_NAMES:
                    candidate = base / sub / name if sub else base / name
                    if candidate.is_file():
                        return candidate.resolve()

    for name in QUANTIZE_NAMES:
        if found := shutil.which(name):
            return Path(found).resolve()

    for directory in (*source_dirs(), *_release_dirs()):
        for sub in ("build/bin", "bin", ""):
            for name in QUANTIZE_NAMES:
                candidate = directory / sub / name if sub else directory / name
                if candidate.is_file():
                    return candidate.resolve()

    return None


def _release_dirs() -> tuple[Path, ...]:
    """The managed llama.cpp builds, newest first: a downloaded release carries ``llama-quantize`` beside the server."""
    builds = home.state("llama.cpp", "builds")
    return tuple(sorted(builds.iterdir(), key=lambda one: one.stat().st_mtime, reverse=True)) if builds.is_dir() else ()


def require_quantize(explicit: str | Path | None = None) -> Path:
    """``find_quantize``, downloading the newest llama.cpp release first when no path is named and none is here; the
    release is kept in ``builds/`` and the build serving uses is left as it is. `ToolNotFound` says why it could not be."""
    found = find_quantize(explicit)
    why = f"{explicit} was named, so nothing was fetched in its place."
    if found is None and not explicit:
        try:
            _fetch_release()
        except (OSError, RuntimeError, ValueError) as exc:
            why = f"No llama.cpp release could be downloaded: {exc}"
        else:
            found = find_quantize()
            why = "The release downloaded here holds none."
    if found is not None:
        return found
    raise ToolNotFound(
        "llama-quantize not found on PATH, in $LLAMA_CPP_ROOT/$LLAMA_CPP_DIR, or in "
        f"{', '.join(str(d) for d in source_dirs())}.\n{why}"
    )


def _fetch_release() -> None:
    root = home.state("llama.cpp")
    with only_one(root / "quantize.lock", wait=True, timeout=WAIT_S, announce=warn, note="fetching llama-quantize"):
        if find_quantize() is not None:
            return
        if refused := provision.download_refusal():
            raise ToolNotFound(refused)
        warn("llama-quantize is needed for the first time: downloading a llama.cpp release once")
        with contextlib.redirect_stdout(sys.stderr):
            lazy.load("poolhouse.serve.build_release", "build_from_release")(argparse.Namespace(force=False))
