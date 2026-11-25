"""Find ``llama-server``, and give it an environment it can actually load in."""

from __future__ import annotations

import logging
import os
import platform
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from poolhouse import credentials, errors, home, lazy
from poolhouse.credentials import child_environment
from poolhouse.files import read_json
from poolhouse.serve import binary_trust, policy as trust

logger = logging.getLogger(__name__)

SERVER_NAMES = ("llama-server", "llama-server.exe")


def managed_root() -> Path:
    """Where `poolhouse-serve build` installs what it builds or downloads."""
    return home.state("llama.cpp")


def managed_current() -> Path:
    """The managed build that is trusted right now."""
    return managed_root() / "current"


def managed_named() -> Path:
    """Where builds kept beside `current` are pointed at, one link per name."""
    return managed_root() / "named"


def managed_builds() -> Path:
    """The directory holding one directory per build made."""
    return managed_root() / "builds"


# The repository `poolhouse-serve build` builds by default. A BUILD.json naming any other
# `repo` is a fork, and a fork is the only kind of build that can load a draft head its
# repository says "does not work on mainline".
MAINLINE = "ggml-org/llama.cpp"

# Directories that are on PATH only in a login shell, so a subprocess never sees them.
_LOGIN_SHELL_DIRS = (
    home.user_home() / "bin",
    home.user_home() / ".local" / "bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


class BinaryNotFound(RuntimeError, errors.Error):
    """No ``llama-server`` could be located, and none could be fetched."""


class BinaryTampered(BinaryNotFound):
    """A binary changed, nothing vouches for it, and neither an earlier build nor a fetched one could replace it."""


def is_windows() -> bool:
    return platform.system() == "Windows"


def find_binary(name: str = "llama-server", *, policy: trust.Policy | None = None, strict: bool = False,
                **how: Any) -> Path | None:
    """`locate_binary`; with ``strict``, whichever route found the file is vetted by ``policy`` (an explicit path,
    ``$LLAMA_CPP_SERVER``, ``$LLAMA_CPP_DIR``, a named build, a vendor directory, the cache and PATH are as
    trusted as ``current``) and the route is logged. Without it the policy vets ``current`` alone, as it always has."""
    found = locate_binary(name, policy=policy, **how)
    if strict and found is not None:
        logger.info("llama.cpp binary %s", found)
        return vetted(found, policy or trust.default())
    return found


def vetted(binary: Path, policy: trust.Policy) -> Path:
    """The binary to run for ``binary``: itself, or the build that replaces it (`binary_trust.vet`: an update something
    vouches for is re-pinned, a change nothing vouches for falls back to an earlier build or a fetched one).
    `BinaryTampered` only when all of that failed."""
    try:
        return binary_trust.vet(binary, policy, previous=lambda: _previous_builds(binary), recover=_fetch_good_build)
    except binary_trust.Untrusted as exc:
        raise BinaryTampered(str(exc)) from exc


def _previous_builds(bad: Path) -> Iterator[Path]:
    """The mainline builds kept under `managed_builds` other than ``bad``, newest first (a fork's build is no stand-in)."""
    kept = []
    for manifest in managed_builds().glob("*/BUILD.json"):
        info = read_json(manifest, {})
        info = info if isinstance(info, dict) else {}
        if str(info.get("repo") or MAINLINE).strip().lower() != MAINLINE:
            continue
        for candidate in _name_variants("llama-server"):
            real = Path(os.path.realpath(manifest.parent / candidate))
            if real.is_file() and real != bad:
                kept.append((str(info.get("built_at", "")), real))
    return (server for _, server in sorted(kept, reverse=True))


def _fetch_good_build() -> Path:
    """The binary of a good build fetched now, made `current` (`poolhouse.serve.build_recover`)."""
    return lazy.load("poolhouse.serve.build_recover", "fetch")()


def locate_binary(
    name: str = "llama-server",
    *,
    explicit: str | Path | None = None,
    vendor_dir: Path | None = None,
    build: str | None = None,
    policy: trust.Policy | None = None,
) -> Path | None:
    """Locate a llama.cpp binary. ``None`` if it is nowhere to be found; raises
    `BinaryNotFound` when the build it names is not installed.

    ``build`` (or, absent that, ``$POOLHOUSE_LLAMA_BUILD``) names a build ``poolhouse-serve
    build --name NAME`` made and kept beside ``current`` rather than replacing it -- a fork
    whose fixes have not reached master. It outranks ``current`` but never an explicit path
    or ``$LLAMA_CPP_SERVER``, so a caller that names a build gets it even while ``current``
    stays mainline, and never another binary in its place.

    ``policy`` holds the pin of the managed build (`poolhouse.serve.policy`); the pool's, which
    is the sentinel's, when none is given.
    """
    candidates = _name_variants(name)

    if explicit:
        path = home.expand(explicit)
        if path.is_file():
            return path.resolve()
        logger.debug("explicit binary %s does not exist; falling through", path)

    env_key = "LLAMA_CPP_SERVER" if name == "llama-server" else None
    if env_key and (value := os.environ.get(env_key)):
        path = home.expand(value)
        if path.is_file():
            return path.resolve()

    if env_dir := os.environ.get("LLAMA_CPP_DIR"):
        for candidate in candidates:
            path = home.expand(env_dir) / candidate
            if path.is_file():
                return path.resolve()

    if name == "llama-server":
        named = build or os.environ.get("POOLHOUSE_LLAMA_BUILD")
        if named:
            for candidate in candidates:
                path = managed_named() / named / candidate
                if path.is_file():
                    return path.resolve()
            raise BinaryNotFound(_absent_build(named, name, "build=" if build
                                               else "$POOLHOUSE_LLAMA_BUILD"))

        # A verified `poolhouse-serve build` outranks a login shell's PATH and the stale
        # bottle a release lags behind -- but never an explicit path or $LLAMA_CPP_SERVER,
        # both handled above.
        policy = policy or trust.default()
        for candidate in candidates:
            path = managed_current() / candidate
            real = Path(os.path.realpath(path))
            if path.is_file() or (policy.pinned(real) and not real.exists()):   # a pinned build that is gone is replaced too
                return vetted(real, policy)

    for directory in (vendor_dir, home.cache()):
        if directory is None:
            continue
        for candidate in candidates:
            path = home.expand(directory) / candidate
            if path.is_file():
                return path.resolve()

    return machine_binary(name, candidates)


def _absent_build(named: str, name: str, source: str) -> str:
    """What `find_binary` says of a named build with no ``name`` under `managed_named`."""
    return (f"llama.cpp build {named!r} (named by {source}) is not installed: "
            f"{managed_named() / named} holds no {name}.\n"
            f"poolhouse-serve build --repo OWNER/REPO --ref REF --name {named}   builds it; "
            f"poolhouse-serve build --check lists the builds this machine holds.")


def machine_binary(name: str, candidates: tuple[str, ...]) -> Path | None:
    """``name`` as this machine installs it: a login shell's directories, then PATH."""
    for directory in _LOGIN_SHELL_DIRS:
        for candidate in candidates:
            path = directory / candidate
            if path.is_file():
                return path.resolve()

    if found := shutil.which(name):
        return Path(found).resolve()

    return None


def require_binary(name: str = "llama-server", **kwargs: object) -> Path:
    """``find_binary`` or raise with the search path spelled out."""
    found = find_binary(name, **kwargs)  # type: ignore[arg-type]
    if found is not None:
        return found
    raise BinaryNotFound(
        f"{name} not found. Looked at: $LLAMA_CPP_SERVER, $LLAMA_CPP_DIR, a named build "
        f"($POOLHOUSE_LLAMA_BUILD or build=) under {managed_named()}, "
        f"{managed_current()}, "
        f"a vendor dir, PATH, {home.cache()}, and "
        f"{', '.join(str(d) for d in _LOGIN_SHELL_DIRS)}.\n"
        f"poolhouse-serve build   builds llama.cpp's own master (or downloads the newest "
        f"release, on a machine with no compiler) -- usually what you want, since a "
        f"release lags master by an architecture or two.\n"
        f"On macOS: brew install llama.cpp"
    )


def manifest_of(binary: str | Path | None) -> dict:
    """The ``BUILD.json`` `poolhouse-serve build` wrote beside ``binary``, or ``{}``.

    Read beside the path as given and beside where it resolves to: `find_binary` resolves
    the `named/<name>` link into `builds/<name>-<commit>/`, and the manifest lives in the
    build directory, not at the link. A brew bottle, a release unpacked by hand and a
    binary on PATH have no manifest, and ``{}`` is the honest answer for those.
    """
    if not binary:
        return {}
    import json

    path = home.expand(binary)
    for where in (path.parent, path.resolve().parent):
        manifest = where / "BUILD.json"
        if not manifest.is_file():
            continue
        try:
            info = json.loads(manifest.read_text())
        except (OSError, ValueError):
            return {}
        return info if isinstance(info, dict) else {}
    return {}


def borrows(binary: str | Path | None) -> bool:
    """Whether ``binary`` is a fork build (named, or from another repository), which alone loads a head that borrows the target's embeddings."""
    if not binary:
        return False
    path = home.expand(binary)
    try:
        path.relative_to(managed_named())
        return True
    except ValueError:
        pass
    info = manifest_of(path)
    repo = str(info.get("repo") or MAINLINE).strip().lower()
    return (bool(info.get("name")) and not info.get("track")) or repo != MAINLINE


def named_builds(name: str = "llama-server") -> list[tuple[str, Path]]:
    """Every named build on this machine as ``(name, binary)``, sorted by name.

    Read off ``managed_named()`` when asked, not at import, so a caller that points it
    elsewhere (a test, a machine with a different home) sees that. A link that no longer
    resolves -- its build directory removed by hand -- is skipped rather than reported as a
    build that is not there.
    """
    named = managed_named()
    if not named.is_dir():
        return []
    out = []
    for link in sorted(named.iterdir()):
        for candidate in _name_variants(name):
            if (link.is_symlink() or link.is_dir()) and (link / candidate).is_file():
                out.append((link.name, link / candidate))
                break
    return out


def hub_environment() -> dict[str, str]:
    """The environment for a server that downloads its own weights: this process's less its
    secrets, plus the Hugging Face token the credentials resolve to."""
    token = credentials.get("HF_TOKEN")
    return child_environment({"HF_TOKEN": str(token)} if token else None)


def child_env(binary: Path | str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment to launch ``binary`` with, with its own directory on PATH and none of
    this process's tokens or keys; ``extra`` is what the child is meant to have."""
    env = child_environment()
    bindir = str(Path(binary).resolve().parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    if extra:
        env.update(extra)
    return {key: value for key, value in env.items()
            if not key.startswith(("DYLD_", "LD_"))}


def _name_variants(name: str) -> tuple[str, ...]:
    if name.endswith(".exe"):
        return (name,)
    return (name, f"{name}.exe") if is_windows() else (name,)
