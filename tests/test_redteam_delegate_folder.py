"""Red team: the scratch repository a delegate reads is made with its folder as one argument, and a link in the copied folder stays a link."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from poolhouse import delegate

pytestmark = pytest.mark.redteam

HOSTILE = "a b;touch PWNED;$(touch PWNED)`touch PWNED`"


def test_a_hostile_folder_and_temporary_folder_reach_git_as_one_argument_and_a_link_is_not_followed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("outside the folder")
    source = tmp_path / HOSTILE
    source.mkdir()
    (source / "--upload-pack=touch PWNED").write_text("x")
    (source / "link").symlink_to(secret)
    scratch = tmp_path / f"tmp {HOSTILE}"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    made = Path(delegate._folder(str(source), edit=False))
    assert made.parent == scratch and (made / ".git").is_dir()
    assert (made / "link").is_symlink() and (made / "link").readlink() == secret
    assert (made / "--upload-pack=touch PWNED").read_text() == "x"
    assert not list(tmp_path.rglob("PWNED"))
