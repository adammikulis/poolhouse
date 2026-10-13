"""Red team: listing the standard extras never runs the keystore library or any other extra.

`poolhouse.installed` names `keyring` (the OS keystore) and `jwt` (the GitHub App key signer) as
strings in STANDARD. A hostile copy of one of those modules earlier on the path, a hostile
module name, or a listing that imports instead of looking would run code beside the real
keystore. Real child processes with a poisoned path, no mocks. Run with --redteam.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse import installed

pytestmark = pytest.mark.redteam

SRC = Path(installed.__file__).resolve().parents[1]
LIST_ALL = (
    "import json, sys\n"
    "from poolhouse import installed\n"
    "installed.standard(); installed.missing(); installed.report(); installed.behind()\n"
    "names = {one.module for one in installed.STANDARD}\n"
    "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in names)))\n"
)


def poison(folder: Path, marker: Path) -> None:
    """One stand-in per standard extra's module, each recording that it was run."""
    for one in installed.STANDARD:
        (folder / f"{one.module}.py").write_text(
            f"open({str(marker)!r}, 'a').write({one.module!r} + '\\n')\n")


def list_with_poison(tmp_path: Path) -> tuple[list[str], list[str]]:
    """The modules the stand-ins ran as, and the extras' modules the listing left imported."""
    folder = tmp_path / "shadow"
    folder.mkdir()
    marker = tmp_path / "ran"
    poison(folder, marker)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "home"),
           "PYTHONPATH": os.pathsep.join([str(folder), str(SRC)])}
    out = subprocess.run([sys.executable, "-c", LIST_ALL], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    ran = marker.read_text().split() if marker.exists() else []
    return ran, json.loads(out.stdout.strip().splitlines()[-1])


def test_listing_the_standard_extras_runs_no_module_a_shadow_copy_stands_in_for(tmp_path):
    ran, loaded = list_with_poison(tmp_path)
    assert ran == []
    assert loaded == []


def test_the_shadow_modules_are_the_ones_the_listing_would_find(tmp_path):
    folder = tmp_path / "shadow"
    folder.mkdir()
    poison(folder, tmp_path / "ran")
    sys.path.insert(0, str(folder))
    try:
        assert all(one.present() for one in installed.STANDARD)
    finally:
        sys.path.remove(str(folder))
    assert not (tmp_path / "ran").exists()


def test_every_standard_module_is_a_top_level_name():
    assert all(one.module.isidentifier() for one in installed.STANDARD)


@pytest.mark.parametrize("name", ["", ".", "..", "a\x00b", "keyring/../x", "-m os"])
def test_a_hostile_module_name_is_absent_and_never_raises(name):
    assert installed.Capability("x", "x", "x", name).present() is False
