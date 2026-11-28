"""The OpenCode binary a launch needs, at the one pinned release: ``poolhouse-opencode`` and ``poolhouse-delegate`` take no install step.

A launch runs the copy this module keeps under the state root for the pinned tag (``tools/opencode/<VERSION>/bin/opencode``) and no other: an
``opencode`` on PATH never stands in for it, and a later pin names another folder, so it takes effect at the next launch. ripgrep, which OpenCode's
search tools run and would otherwise download from inside the confinement (where there is no network), is kept beside it at its own pinned release,
so the one folder a session may read and run holds both. Both are fetched through the same net pipeline `poolhouse-serve build` uses
(`fleet.updates.download`: the asset is checked against the sha256 recorded in `PINNED`, format-checked and scanned, and refused when none is
recorded), unpacked with `safenames.unpack` and kept read-only to everything but this user. Nothing is run to install them and nothing is asked of
a person.
"""

from __future__ import annotations

import platform
import shutil
import stat
import tempfile
from pathlib import Path

from poolhouse import files, home, net, provision
from poolhouse.fleet import updates
from poolhouse.http import ServerError
from poolhouse.lock import only_one
from poolhouse.log import say
from poolhouse.safenames import Unsafe, unpack

__all__ = ["PINNED", "REPO", "RIPGREP", "VERSION", "InstallFailed", "asset_name", "ensure", "keep", "kept", "ripgrep_asset_name"]

REPO = "anomalyco/opencode"
"""The project's releases (the former sst/opencode, which redirects here)."""
VERSION = "v2.4.2"
"""The one release a launch runs: its config, plugin and event formats are what `poolhouse.opencode` and the delegate are written against."""
RIPGREP = "15.1.0"
"""The ripgrep release that OpenCode of `VERSION` pins for itself."""
PINNED = {
    (VERSION, "opencode-darwin-arm64.zip"):
        "d5a8c9fb4b121ebd531d9369d7ba6ce8af91c9d4d51d6685dbb4e0c7224fa5f3",
    (VERSION, "opencode-darwin-x64.zip"):
        "ea4204a75602ce4f1d2121c781a15fa3af6d1c4285cf8b6ae81f61a76deca2a2",
    (VERSION, "opencode-linux-arm64.tar.gz"):
        "bf8d18dde87224629341ff398e76c7c17216b9b406affc728b6c1a5c136abf93",
    (VERSION, "opencode-linux-x64.tar.gz"):
        "452912288f934d1771bd5bd0d8611ba3a6adc3db3ac3333c37facf373770ba66",
    (VERSION, "opencode-windows-arm64.zip"):
        "0081574c145d60fc6b0079db558ca1b071394b8ccc4549424dec0acb66706942",
    (VERSION, "opencode-windows-x64.zip"):
        "a8ab3034ca5b3ec8476e9ee73b1dc334b6d8e93edaaf224b3d2a5205e2cbfb99",
    (RIPGREP, "ripgrep-15.1.0-aarch64-apple-darwin.tar.gz"):
        "378e973289176ca0c6054054ee7f631a065874a352bf43f0fa60ef079b6ba715",
    (RIPGREP, "ripgrep-15.1.0-aarch64-pc-windows-msvc.zip"):
        "00d931fb5237c9696ca49308818edb76d8eb6fc132761cb2a1bd616b2df02f8e",
    (RIPGREP, "ripgrep-15.1.0-aarch64-unknown-linux-gnu.tar.gz"):
        "2b661c6ef508e902f388e9098d9c4c5aca72c87b55922d94abdba830b4dc885e",
    (RIPGREP, "ripgrep-15.1.0-x86_64-apple-darwin.tar.gz"):
        "64811cb24e77cac3057d6c40b63ac9becf9082eedd54ca411b475b755d334882",
    (RIPGREP, "ripgrep-15.1.0-x86_64-pc-windows-msvc.zip"):
        "124510b94b6baa3380d051fdf4650eaa80a302c876d611e9dba0b2e18d87493a",
    (RIPGREP, "ripgrep-15.1.0-x86_64-unknown-linux-musl.tar.gz"):
        "1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599",
}
"""The sha256 of each asset fetched, per release tag and asset name: a download that does not match, or has no row, is not installed."""
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
    if refused := provision.download_refusal():
        raise InstallFailed(f"OpenCode was not downloaded: {refused}")
    root().mkdir(parents=True, exist_ok=True)
    with only_one(root() / "fetch.lock", wait=True, timeout=WAIT_S, note="fetching OpenCode"):
        if binary := kept():
            return binary
        wanted = ((RELEASE, VERSION, asset_name(), "opencode"), (RIPGREP_RELEASE, RIPGREP, ripgrep_asset_name(), "rg"))
        for feed, tag, asset, program in wanted:
            keep(f"{feed}{tag}", asset, _name(program), _folder() / _name(program), sha256=_pinned(tag, asset))
        return kept()


def _pinned(tag: str, asset: str) -> str:
    if found := PINNED.get((tag, asset)):
        return found
    raise InstallFailed(f"no sha256 is pinned for {asset} of {tag}, so it is not downloaded")


def keep(url: str, want: str, member: str, target: Path, *, sha256: str) -> None:
    """Keep the file ``member`` of the asset ``want``, of the GitHub release at ``url``, as ``target``. The download is held to
    ``sha256``, the digest recorded here, never to the one the release reports. `InstallFailed` names why it was not."""
    try:
        release = net.default().json(url, net.Ask(purpose="tool releases", tries=3, headers={"Accept": "application/vnd.github+json"}))
    except (ServerError, OSError, ValueError) as why:
        raise InstallFailed(f"could not reach the release {url}: {why}") from None
    asset = next((a for a in (release.get("assets") or [] if isinstance(release, dict) else []) if a.get("name") == want), None)
    if asset is None:
        raise InstallFailed(f"the release {url} carries no {want}")
    asset = {**asset, "digest": f"sha256:{sha256}"}
    say(f"fetching {want}")
    with tempfile.TemporaryDirectory(prefix="poolhouse-tool-") as scratch:
        try:
            archive = updates.download(asset, scratch, pinned=True)
            staging = Path(scratch) / "unpacked"
            unpack(archive, staging)
        except (updates.UpdateError, Unsafe) as why:
            raise InstallFailed(f"{want} was not installed: {why}") from None
        found = next((p for p in staging.rglob(member) if p.is_file()), None)
        if found is None:
            raise InstallFailed(f"{want} holds no {member}")
        with files.writing(target, suffix=".part") as part:   # whole or absent: a kept tool is asked for without the lock
            shutil.copy2(found, part)
            part.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
