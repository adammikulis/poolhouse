"""Staging the sandbox files and their manifest in Poolhouse's own state folder. No harness's own settings are written or installed."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import agent_sandbox_profile as profile

LIFETIME = 7 * 24 * 3600
MANIFEST = "manifest.json"


def staging_dir(state: Path) -> Path:
    """The directory prepared files are written to."""
    return state / "agent-sandbox" / "staging"


def digest(data: bytes) -> str:
    """The sha256 of ``data`` as hex."""
    return hashlib.sha256(data).hexdigest()


def rendered(layout: profile.Layout) -> dict[str, bytes]:
    """Every staged file's content, by name."""
    return {
        "claude-managed-settings.json": (json.dumps(profile.claude_settings(layout), indent=2) + "\n").encode(),
        "srt-settings.json": (json.dumps(profile.srt_settings(layout), indent=2) + "\n").encode(),
        "codex-config.toml": profile.codex_toml(layout).encode(),
    }


def prepare(layout: profile.Layout, device: str, now: float | None = None) -> Path:
    """Write the staged files and manifest; return the staging directory."""
    stamp = time.time() if now is None else now
    target = staging_dir(layout.state)
    target.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, data in rendered(layout).items():
        (target / name).write_bytes(data)
        entries.append({"name": name, "sha256": digest(data)})
    manifest = {"version": 1, "platform": layout.platform, "device_id": device, "created": stamp,
                "expires": stamp + LIFETIME, "files": entries}
    (target / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return target


def load(state: Path) -> dict:
    """The staged manifest, or an empty dict when nothing is staged."""
    path = staging_dir(state) / MANIFEST
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def expired(state: Path, now: float | None = None) -> bool:
    """Whether the staged files are past their expiry."""
    manifest = load(state)
    return bool(manifest) and (time.time() if now is None else now) > manifest["expires"]
