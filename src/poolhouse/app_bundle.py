"""What Poolhouse's macOS bundles have in common: where they live, the Info.plist macOS reads and the icon.

`node_app` (the node) and `runtime_identity` (every Python process of the runtime) each make a Poolhouse.app with its own bundle id
and executable; both are named Poolhouse, so a prompt or the firewall list says Poolhouse whichever of them it is about.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from poolhouse.home import home

NAME = "Poolhouse"
USAGE = "Poolhouse finds and joins your other devices on this network."
BONJOUR = ["_poolhouse._tcp"]
MINIMUM_SYSTEM = "13.0"
ICON_SVG = Path(__file__).parent / "fleet" / "web" / "poolhouse.svg"
ICON_SIZES = (16, 32, 128, 256, 512)
TOOL_TIMEOUT = 60.0


class AppError(OSError):
    """A bundle could not be built or signed."""


def apps_directory() -> Path:
    """Where the bundles live: outside every checkout."""
    return home() / "apps"


def info_plist(bundle_id: str, executable: str, version: str = "0", *, icon: bool = True) -> dict:
    """A bundle's Info.plist as a dict: its fixed id, no dock icon, and the keys macOS reads to show the Local Network prompt."""
    plist = {
        "CFBundleIdentifier": bundle_id, "CFBundleName": NAME, "CFBundleDisplayName": NAME, "CFBundleExecutable": executable,
        "CFBundlePackageType": "APPL", "CFBundleVersion": version, "CFBundleShortVersionString": version,
        "LSUIElement": True, "LSMinimumSystemVersion": MINIMUM_SYSTEM, "NSLocalNetworkUsageDescription": USAGE,
        "NSBonjourServices": list(BONJOUR),
    }
    if icon:
        plist["CFBundleIconFile"] = NAME
    return plist


def tool(argv: list[str]) -> None:
    """Run a system tool, raising `AppError` with the end of what it said when it fails."""
    done = subprocess.run(argv, capture_output=True, text=True, timeout=TOOL_TIMEOUT, check=False)
    if done.returncode:
        raise AppError(f"{argv[0]} failed: {(done.stderr or done.stdout).strip()[-400:]}")


def icns(svg: Path, into: Path) -> bool:
    """Write Poolhouse.icns from the SVG with macOS's own renderer; False when it cannot (the bundle then has no icon)."""
    with tempfile.TemporaryDirectory(prefix="poolhouse-icon") as scratch:
        work = Path(scratch)
        try:
            tool(["qlmanage", "-t", "-s", "1024", "-o", str(work), str(svg)])
            iconset = work / "Poolhouse.iconset"
            iconset.mkdir()
            for size in ICON_SIZES:
                for scale in (1, 2):
                    tool(["sips", "-z", str(size * scale), str(size * scale), str(work / f"{svg.name}.png"), "--out",
                          str(iconset / f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png")])
            tool(["iconutil", "-c", "icns", str(iconset), "-o", str(into)])
        except (AppError, OSError, subprocess.SubprocessError):
            return False
    return True
