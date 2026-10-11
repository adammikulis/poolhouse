"""A session's token file is private to its owner: it never goes through a link, into a git work tree or onto a Windows
mount, and on Windows only the owning account reads it."""

import os
import subprocess

import pytest

from poolhouse import private_path, windows_private
from poolhouse.board import credentials
from poolhouse.errors import Denied

if os.name == "nt":
    import ntsecuritycon
    import win32security


def test_token_folder_rejects_windows_mount(tmp_path, monkeypatch):
    state = tmp_path / "node"
    monkeypatch.setattr(private_path, "windows_mount", lambda path: True)
    state.mkdir()
    assert "Windows-mounted filesystem" in private_path.problem(state)
    with pytest.raises(ValueError, match="Windows-mounted filesystem"):
        credentials.store(state, "demo", "agent", "a-token")
    assert not (state / "client").exists()


def test_token_folder_inside_a_git_work_tree_is_refused(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    state = repo / "node"
    with pytest.raises(ValueError, match="git work tree"):
        credentials.store(state, "demo", "agent", "a-token")
    assert not (state / "client").exists()


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX symbolic link")
def test_a_linked_token_folder_is_refused_and_nothing_is_written_through_it(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    state = tmp_path / "node"
    state.mkdir()
    (state / "client").symlink_to(elsewhere)
    with pytest.raises(ValueError, match="symlink or Windows reparse point"):
        credentials.store(state, "demo", "agent", "a-token")
    assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX symbolic link")
def test_a_token_file_that_is_a_link_is_refused_when_read(tmp_path):
    held = credentials.store(tmp_path, "demo", "agent", "a-token")
    elsewhere = tmp_path / "elsewhere.token"
    elsewhere.write_text("another-token\n")
    elsewhere.chmod(0o600)
    held.unlink()
    held.symlink_to(elsewhere)
    with pytest.raises(Denied):
        credentials.load(tmp_path, "demo", "agent")


@pytest.mark.skipif(os.name != "nt", reason="native Windows access control")
def test_created_token_and_folder_are_private(tmp_path):
    held = "fixture-token-of-an-agent"
    target = credentials.store(tmp_path, "demo", "fixture-agent", held)
    assert private_path.problem(target.parent) == ""
    assert private_path.problem(target) == ""
    assert credentials.load(tmp_path, "demo", "fixture-agent") == held


@pytest.mark.skipif(os.name != "nt", reason="native Windows access control")
def test_another_account_grant_refuses_token_reads(tmp_path):
    target = credentials.store(tmp_path, "demo", "fixture-agent", "fixture-token-of-an-agent")
    descriptor = win32security.GetNamedSecurityInfo(
        str(target), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    everyone = win32security.CreateWellKnownSid(win32security.WinWorldSid)
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ, everyone)
    try:
        win32security.SetNamedSecurityInfo(
            str(target), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION,
            None, None, acl, None)
        with pytest.raises(Denied, match="another account"):
            credentials.load(tmp_path, "demo", "fixture-agent")
    finally:
        windows_private.restrict(target)


@pytest.mark.skipif(os.name != "nt", reason="native Windows junctions")
def test_junction_token_folder_is_refused_without_changing_target_acl(tmp_path):
    target = tmp_path / "other-private-directory"
    target.mkdir()
    marker = target / "kept.txt"
    marker.write_text("preserved", encoding="utf-8")
    security = win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
    before = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
        win32security.GetFileSecurity(str(target), security), win32security.SDDL_REVISION_1, security)
    state = tmp_path / "node"
    state.mkdir()
    junction = state / "client"
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                             capture_output=True, check=False)
    assert created.returncode == 0, created.stderr
    try:
        assert "reparse point" in private_path.problem(junction)
        with pytest.raises(ValueError, match="reparse point"):
            credentials.store(state, "demo", "agent", "a-token")
        after = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            win32security.GetFileSecurity(str(target), security), win32security.SDDL_REVISION_1, security)
        assert after == before and marker.read_text(encoding="utf-8") == "preserved"
    finally:
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="native Windows access control")
def test_restrict_refuses_foreign_owner_before_any_security_mutation(tmp_path, monkeypatch):
    path = tmp_path / "foreign-storage"
    path.mkdir()
    foreign = win32security.CreateWellKnownSid(win32security.WinWorldSid)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(foreign, False)
    read = win32security.GetNamedSecurityInfo
    monkeypatch.setattr(win32security, "GetNamedSecurityInfo", lambda name, *args:
                        descriptor if name == str(path) else read(name, *args))
    monkeypatch.setattr(win32security, "SetNamedSecurityInfo",
                        lambda *args: pytest.fail("foreign ownership was changed"))
    with pytest.raises(ValueError, match="belongs to another user"):
        windows_private.restrict(path)
    with pytest.raises(ValueError, match="belongs to another user"):
        credentials.store(path, "demo", "agent", "a-token")
    assert not (path / "client").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows access control")
def test_owned_broad_permissions_are_repaired_without_changing_owner(tmp_path):
    path = tmp_path / "own-storage"
    path.mkdir()
    security = win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION
    descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT, security)
    owner = descriptor.GetSecurityDescriptorOwner()
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ,
                           win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                     win32security.DACL_SECURITY_INFORMATION, None, None, acl, None)
    assert windows_private.problem(path)
    credentials.store(path, "demo", "agent", "a-token")
    assert windows_private.problem(path) == ""
    assert win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION).GetSecurityDescriptorOwner() == owner


@pytest.mark.skipif(os.name != "nt", reason="native Windows junctions")
def test_junction_ancestor_refused_before_creating_the_token_folder(tmp_path):
    target = tmp_path / "unchanged"
    target.mkdir()
    junction = tmp_path / "redirect"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                          capture_output=True, check=False)
    assert made.returncode == 0, made.stderr
    try:
        state = junction / "new-node"
        with pytest.raises(ValueError, match="reparse point"):
            windows_private.validate(state)
        with pytest.raises(ValueError, match="reparse point"):
            credentials.store(state, "demo", "agent", "a-token")
        assert list(target.iterdir()) == []
    finally:
        junction.rmdir()
