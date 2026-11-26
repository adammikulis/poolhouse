"""The OpenCode binary a launch needs, found or fetched: ``poolhouse-opencode`` and ``launch_coding_agent(harness="opencode")`` take no install step.

A launch finds ``opencode`` on PATH first (a person's own install), then the copy this module keeps under the state root
(``tools/opencode/<tag>/opencode``). With neither, the newest GitHub release of the project is fetched through the same net pipeline
`poolhouse-serve build` uses (`fleet.updates.download`: the asset is checked against the sha256 GitHub reports for it, format-checked and
scanned, and refused when it comes with no digest), unpacked with `safenames.unpack` and kept read-only to everything but this user. Nothing is
run to install it and nothing is asked of a person.
"""

from __future__ import annotations

import platform
import shutil
import stat
import tempfile
from pathlib import Path

from poolhouse import home, net
from poolhouse.fleet import updates
from poolhouse.http import ServerError
from poolhouse.lock import only_one
from poolhouse.log import say
from poolhouse.safenames import Unsafe, unpack

__all__ = ["REPO", "InstallFailed", "asset_name", "ensure", "kept"]

REPO = "anomalyco/opencode"
"""The project's releases (the former sst/opencode, which redirects here)."""
RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=5"
WAIT_S = 1800.0


class InstallFailed(RuntimeError):
    """The binary could not be fetched; the message names why."""


def asset_name() -> str:
    """The release asset for this machine: ``opencode-<os>-<arch>.<zip|tar.gz>``."""
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system(), "")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else ""
    if not system or not arch:
        raise InstallFailed(f"no OpenCode release is built for {platform.system()} {platform.machine()}")
    return f"opencode-{system}-{arch}." + ("tar.gz" if system == "linux" else "zip")


def _name() -> str:
    return "opencode.exe" if platform.system() == "Windows" else "opencode"


def root() -> Path:
    return home.state("tools", "opencode")


def kept() -> str:
    """The newest binary this module has fetched, or ''."""
    found = sorted((p for p in root().glob(f"*/{_name()}") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(found[0]) if found else ""


def ensure() -> str:
    """The kept binary, fetching the newest release first when none is kept. `InstallFailed` names why a fetch did not complete."""
    if binary := kept():
        return binary
    root().mkdir(parents=True, exist_ok=True)
    with only_one(root() / "fetch.lock", wait=True, timeout=WAIT_S, note="fetching OpenCode"):
        if binary := kept():
            return binary
        return _fetch()


def _fetch() -> str:
    want = asset_name()
    try:
        releases = net.default().json(RELEASES, net.Ask(purpose="OpenCode releases", tries=3, headers={"Accept": "application/vnd.github+json"}))
    except (ServerError, OSError, ValueError) as why:
        raise InstallFailed(f"could not reach the releases of {REPO}: {why}") from None
    for release in releases if isinstance(releases, list) else []:
        if release.get("draft") or release.get("prerelease"):
            continue
        asset = next((a for a in release.get("assets") or [] if a.get("name") == want), None)
        if asset is None:
            continue
        tag = str(release.get("tag_name", ""))
        say(f"fetching OpenCode {tag} ({want})")
        dest = root() / "".join(c for c in tag if c.isalnum() or c in ".-_")
        with tempfile.TemporaryDirectory(prefix="poolhouse-opencode-") as scratch:
            try:
                archive = updates.download(asset, scratch)
                staging = Path(scratch) / "unpacked"
                unpack(archive, staging)
            except (updates.UpdateError, Unsafe) as why:
                raise InstallFailed(f"OpenCode {tag} was not installed: {why}") from None
            found = next((p for p in staging.rglob(_name()) if p.is_file()), None)
            if found is None:
                raise InstallFailed(f"the {want} of {tag} holds no {_name()}")
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True)
            shutil.copy2(found, dest / _name())
        (dest / _name()).chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
        return str(dest / _name())
    raise InstallFailed(f"no recent release of {REPO} carries {want}")
