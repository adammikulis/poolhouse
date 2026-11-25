"""What a test needs to run a launcher for real on this computer: the real stage, the real bridges and the real proof under the real Seatbelt.

The launchers prove a confinement before they hand the runner anything, so a test of a launcher's plumbing (its lease, its arguments, its settings,
its lines) runs the whole of `isolation.prepare`. Two differences of the test rig from a launch: the stage is built in a private directory of the test, and the proof does not listen on the
machine's LAN address, which the suite's network guard forbids (`lan=False`: the probe then tries a documentation-range address, as it does on a
machine with none). Nothing here stands in for the sandbox, the proof or a bridge.

    pytest_plugins = ["tests.confined_kit"]
    pytestmark = pytest.mark.usefixtures("confinable")
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from poolhouse.isolation import confinement
from poolhouse.isolation.proof import run as run_proof


@pytest.fixture
def confinable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Launches in this test prove a real confinement. The stage of a launch is built in a private directory of the test while it is begun (a
    snapshot refuses storage that holds the state root, and a test's state root is under the temporary directory); the returned directory is it."""
    storage = tmp_path / "snapshot-storage"
    storage.mkdir(mode=0o700)
    begun = confinement.Session.begin

    def begin(launch):
        kept, tempfile.tempdir = tempfile.tempdir, str(storage)
        try:
            return begun(launch)
        finally:
            tempfile.tempdir = kept

    monkeypatch.setattr(confinement.Session, "begin", staticmethod(begin))
    monkeypatch.setattr(confinement, "run_proof", lambda profile, services: run_proof(profile, services, lan=False))
    return storage


def repository(path: Path) -> Path:
    """A real git project with one file in it: what a launch stages (a launch refuses a folder that is not one)."""
    from tests.isolation_world import git

    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("# project\n")
    git(path, "init", "-q")
    git(path, "add", "-A")
    return path
