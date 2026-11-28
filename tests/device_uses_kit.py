"""Plumbing stand-ins for the list of what a device can do, each one marked canned.

No model runs and nothing is installed: these answer the reads the route makes, so the words it picks can be checked.
"""

from __future__ import annotations

from types import SimpleNamespace

CANNED_MODEL = "CANNED-NOT-A-MODEL.gguf"
OWN, OTHER = "ab" * 32, "cd" * 32


class CannedEnvironment:
    """[CANNED INSTALL, NOTHING IS INSTALLED] the job environment's library reads and installs."""

    def __init__(self, *have: str) -> None:
        self.have = set(have)
        self.reads = 0
        self.asked: list[list[str]] = []

    def installed(self) -> dict[str, str]:
        self.reads += 1
        return {}

    def library_state(self, library, have) -> dict[str, bool]:
        return {"installed": library.name in self.have}

    def install(self, names, *, on_progress=None, locked=False) -> dict[str, dict]:
        self.asked.append(list(names))
        self.have.update(names)
        return {name: {"ok": True} for name in names}

    def uninstall(self, names) -> dict[str, dict]:
        return {}

    def state(self, vendor: str = "") -> dict:
        return {"libraries": []}


def canned_serving() -> SimpleNamespace:
    """A registry that names one running model, which is plainly no model."""
    return SimpleNamespace(public=lambda: [{"port": 1, "models": [CANNED_MODEL]}], live=lambda **_: [])


def canned_pool(name: str = "canned-laptop", *, connected: bool = True) -> dict:
    """What a node would report for an unfounded pool holding this device and one other."""
    return {"fingerprint": OWN, "governance": {"mode": "unfounded", "say": "[CANNED] no node answers in this test"},
            "members": [{"fingerprint": OWN, "name": "canned-desk", "status": "active", "self": True},
                        {"fingerprint": OTHER, "name": name, "status": "active", "connected": connected}]}


def add_model(folder, name: str = "thornfield-8B-Q4_K_M.gguf") -> None:
    """A file the model store lists; its bytes are no model."""
    (folder / name).write_bytes(b"x" * (2 << 20))
