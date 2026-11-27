"""The OpenCode binary a launch needs, at the one pinned release: ``poolhouse-opencode`` and ``poolhouse-delegate`` take no install step.

A launch runs the copy this module keeps under the state root for the pinned tag (``tools/opencode/<VERSION>/bin/opencode``) and no other: an
``opencode`` on PATH never stands in for it, and a later pin names another folder, so it takes effect at the next launch. ripgrep, which OpenCode's
search tools run and would otherwise download from inside the confinement (where there is no network), is kept beside it at its own pinned release,
so the one folder a session may read and run holds both. Both are fetched through the same net pipeline `poolhouse-serve build` uses
(`fleet.updates.download`: the asset is checked against the sha256 GitHub reports for it, format-checked and scanned, and refused when it comes with
no digest), unpacked with `safenames.unpack` and kept read-only to everything but this user. Nothing is run to install them and nothing is asked of
a person.
"""

from __future__ import annotations

import platform
import shutil
import stat
import tempfile
from pathlib import Path

from poolhouse import files, home, net
from poolhouse.fleet import updates
from poolhouse.http import ServerError
from poolhouse.lock import only_one
from poolhouse.log import say
from poolhouse.safenames import Unsafe, unpack

__all__ = ["REPO", "RIPGREP", "VERSION", "InstallFailed", "asset_name", "ensure", "kept", "ripgrep_asset_name"]

REPO = "anomalyco/opencode"
"""The project's releases (the former sst/opencode, which redirects here)."""
VERSION = "v2.4.2"
"""The one release a launch runs: its config, plugin and event formats are what `poolhouse.opencode` and the delegate are written against."""
RIPGREP = "15.1.0"
"""The ripgrep release that OpenCode of `VERSION` pins for itself."""
RELEASE = f"https://api.github.com/repos/{REPO}/releases/tags/"
RIPGREP_RELEASE = "https://api.github.com/repos/BurntSushi/ripgrep/releases/tags/"
WAIT_S = 1800.0
RIPGREP_TARGETS = {("darwin", "arm64"): "aarch64-apple-darwin.tar.gz", ("darwin", "x64"): "x86_64-apple-darwin.tar.gz",
                   ("linux", "arm64"): "aarch64-unknown-linux-gnu.tar.gz", ("linux", "x64"): "x86_64-unknown-linux-musl.tar.gz",
                   ("windows", "arm64"): "aarch64-pc-windows-msvc.zip", ("windows", "x64"): "x86_64-pc-windows-msvc.zip"}


class InstallFailed(RuntimeError):
    """The binary could not be fetched; the message names why."""


def _machine() -> tuple[str, str]:
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system(), "")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else ""
    if not system or not arch:
        raise InstallFailed(f"no OpenCode release is built for {platform.system()} {platform.machine()}")
    return system, arch


def asset_name() -> str:
    """The release asset for this machine: ``opencode-<os>-<arch>.<zip|tar.gz>``."""
    system, arch = _machine()
    return f"opencode-{system}-{arch}." + ("tar.gz" if system == "linux" else "zip")


def ripgrep_asset_name() -> str:
    """The ripgrep release asset for this machine."""
    return f"ripgrep-{RIPGREP}-{RIPGREP_TARGETS[_machine()]}"


def _name(program: str) -> str:
    return program + ".exe" if platform.system() == "Windows" else program


def root() -> Path:
    return home.state("tools", "opencode")


def _folder() -> Path:
    return root() / VERSION / "bin"


def kept() -> str:
    """The binary of the pinned release, with its ripgrep beside it, or ''. A copy of any other release is not one."""
    found = _folder() / _name("opencode")
    return str(found) if found.is_file() and (_folder() / _name("rg")).is_file() else ""


def ensure() -> str:
    """The pinned binary, fetched first when it is not kept. `InstallFailed` names why a fetch did not complete."""
    if binary := kept():
        return binary
    root().mkdir(parents=True, exist_ok=True)
    with only_one(root() / "fetch.lock", wait=True, timeout=WAIT_S, note="fetching OpenCode"):
        if binary := kept():
            return binary
        _fetch(f"{RELEASE}{VERSION}", asset_name(), "opencode")
        _fetch(f"{RIPGREP_RELEASE}{RIPGREP}", ripgrep_asset_name(), "rg")
        return kept()


def _fetch(url: str, want: str, program: str) -> None:
    """Keep ``program`` from the asset ``want`` of the release at ``url`` in the pinned folder."""
    try:
        release = net.default().json(url, net.Ask(purpose="OpenCode releases", tries=3, headers={"Accept": "application/vnd.github+json"}))
    except (ServerError, OSError, ValueError) as why:
        raise InstallFailed(f"could not reach the release {url}: {why}") from None
    asset = next((a for a in (release.get("assets") or [] if isinstance(release, dict) else []) if a.get("name") == want), None)
    if asset is None:
        raise InstallFailed(f"the release {url} carries no {want}")
    say(f"fetching {want}")
    with tempfile.TemporaryDirectory(prefix="poolhouse-opencode-") as scratch:
        try:
            archive = updates.download(asset, scratch)
            staging = Path(scratch) / "unpacked"
            unpack(archive, staging)
        except (updates.UpdateError, Unsafe) as why:
            raise InstallFailed(f"{want} was not installed: {why}") from None
        found = next((p for p in staging.rglob(_name(program)) if p.is_file()), None)
        if found is None:
            raise InstallFailed(f"{want} holds no {_name(program)}")
        with files.writing(_folder() / _name(program), suffix=".part") as part:   # whole or absent: `kept` is asked without the lock
            shutil.copy2(found, part)
            part.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
