"""The Codex binary a launch uses when this device has none: one pinned release, fetched the first time and kept.

It is kept under the state root (``tools/codex/<VERSION>/``) through the same installer as OpenCode (`opencode_install.keep`):
the asset is held to the sha256 recorded here, format-checked and scanned, unpacked and written whole. A ``codex`` on PATH is
the person's own and is used as it is. One process fetches at a time and the others use what it fetched. Nothing is run to
install it.
"""

from __future__ import annotations

import platform
from pathlib import Path

from poolhouse import home, opencode_install, provision
from poolhouse.lock import only_one
from poolhouse.log import warn

__all__ = ["ASSETS", "REPO", "VERSION", "ensure", "kept"]

REPO = "openai/codex"
VERSION = "rust-v0.167.0"
"""The release tag fetched: the config format `poolhouse.codex` writes was used with it."""
RELEASE = f"https://api.github.com/repos/{REPO}/releases/tags/"
WAIT_S = 1800.0
ASSETS = {
    ("Darwin", "arm64"): ("codex-aarch64-apple-darwin", ".tar.gz",
                          "4b22a2a971be2791c3e31f912f3e77a88ebd64a65812a7289ccba65dd6425fe9"),
    ("Darwin", "x64"): ("codex-x86_64-apple-darwin", ".tar.gz",
                        "d8eb4a865a0b17319cde10347523e5bab0978d01725b39f4a639f40905e01221"),
    ("Windows", "arm64"): ("codex-aarch64-pc-windows-msvc.exe", ".zip",
                           "c437e561e756477c5cbef0e8f99d17761cc59ef87ca5567f2a00a8964ec131cf"),
    ("Windows", "x64"): ("codex-x86_64-pc-windows-msvc.exe", ".zip",
                         "94b51ca84648a6923784ef71f06e2feb1e10796ddb3f5d033ccb0fbe8daa2f16"),
}
"""Per system and processor: the binary's name inside the asset, the asset's extension and the asset's sha256."""


def _asset() -> tuple[str, str, str]:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else ""
    found = ASSETS.get((platform.system(), arch))
    if found is None:
        raise opencode_install.InstallFailed(f"no Codex release is pinned for {platform.system()} {platform.machine()}")
    return found


def _target() -> Path:
    return home.state("tools", "codex", VERSION, "codex.exe" if platform.system() == "Windows" else "codex")


def kept() -> str:
    """The pinned binary when it is kept here, else ''."""
    return str(_target()) if _target().is_file() else ""


def ensure() -> str:
    """The pinned binary, fetched first when it is not kept. `opencode_install.InstallFailed` names why it was not."""
    if binary := kept():
        return binary
    member, extension, digest = _asset()
    if refused := provision.download_refusal():
        raise opencode_install.InstallFailed(f"Codex was not downloaded: {refused}")
    folder = _target().parent
    folder.mkdir(parents=True, exist_ok=True)
    with only_one(folder.parent / "fetch.lock", wait=True, timeout=WAIT_S, announce=warn, note="fetching Codex"):
        if binary := kept():
            return binary
        warn(f"Codex is needed for the first time: downloading {VERSION} once")
        opencode_install.keep(f"{RELEASE}{VERSION}", member + extension, member, _target(), sha256=digest)
        return kept()
