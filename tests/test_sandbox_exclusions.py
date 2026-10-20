"""Protected paths override writable repository grants."""

import os
import shlex
from dataclasses import replace

import pytest

from poolhouse import sandbox
from poolhouse.sandbox.bubblewrap import arguments
from poolhouse.sandbox.seatbelt import profile

pytest_plugins = ["tests.sandbox_kit"]


def test_exclusions_accept_future_paths_and_reject_noncanonical_paths(tmp_path):
    future = str(tmp_path / ".git" / "hooks")
    held = sandbox.Policy(deny_write=(future, future)).validated()
    assert held.deny_write == (future,)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    for path in ("relative", f"{tmp_path}/../hooks", f"{tmp_path}//hooks",
                 str(alias / "future"), f"{tmp_path}/bad\npath"):
        with pytest.raises(sandbox.PolicyError):
            sandbox.Policy(deny_read=(path,)).validated()


def test_exclusions_reject_replaceable_ancestors(tmp_path):
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    with pytest.raises(sandbox.PolicyError, match="replaceable ancestry"):
        sandbox.Policy(deny_write=(str(unsafe / "config"),)).validated()


@pytest.mark.parametrize("kind", ["hardlink", "symlink"])
def test_preexisting_aliases_in_protected_tree_are_refused(tmp_path, kind):
    protected = tmp_path / "hooks"
    protected.mkdir()
    original = tmp_path / "original"
    original.write_text("private")
    if kind == "hardlink":
        os.link(original, protected / "alias")
    else:
        (protected / "alias").symlink_to(original)
    with pytest.raises(sandbox.PolicyError, match="aliases"):
        sandbox.Policy(deny_write=(str(protected),)).validated()


def test_final_profile_exclusions_override_allows_and_freeze_read_secrets(tmp_path):
    secret = str(tmp_path / "future")
    held = sandbox.Policy(read=(str(tmp_path),), write=(str(tmp_path),),
                          cache=str(tmp_path), deny_read=(secret,)).validated()
    text = profile(held, "/bin/sh", "excluded")
    assert text.index("(deny file-link)") > text.rindex("(allow ")
    assert f'(deny file-read* (subpath "{secret}"))' in text
    assert f'(deny file-write* (subpath "{secret}"))' in text
    assert '(deny file-write-unlink (literal ' in text


@pytest.mark.parametrize("field", ["deny_read", "deny_write"])
def test_bubblewrap_fails_closed_for_exclusion_policies(tmp_path, field):
    held = sandbox.Policy(**{field: (str(tmp_path / "future"),)}).validated()
    with pytest.raises(sandbox.PolicyError, match="cannot enforce"):
        arguments(held, "/bin/sh")


def test_native_exclusions_preserve_sibling_edits_and_deny_secret_reads(tmp_path, seatbelt):
    from tests.sandbox_kit import policy

    protected = tmp_path / "control"
    protected.mkdir()
    secret = protected / "secret"
    secret.write_text("never-show-this")
    held = replace(policy(write=[tmp_path]), deny_read=(str(protected),))
    denied = sandbox.run(["/bin/cat", str(secret)], held, diagnose="never")
    assert denied.returncode != 0 and "never-show-this" not in denied.stdout
    good = sandbox.run(["/bin/sh", "-c", 'printf edited > "$1"', "sh", str(tmp_path / "code")],
                       held, diagnose="never")
    assert good.returncode == 0 and (tmp_path / "code").read_text() == "edited"
    metadata = sandbox.run(["/usr/bin/stat", str(secret)], held, diagnose="never")
    assert metadata.returncode != 0


@pytest.mark.parametrize("attack", ["overwrite", "delete", "rename", "ancestor", "link", "symlink"])
def test_native_protected_write_tree_cannot_be_changed_or_aliased(tmp_path, seatbelt, attack):
    from tests.sandbox_kit import policy

    protected = tmp_path / ".git" / "hooks"
    protected.mkdir(parents=True)
    original = protected / "hook"
    original.write_text("original")
    alias = tmp_path / "alias"
    commands = {
        "overwrite": ["/bin/sh", "-c", 'printf changed > "$1"', "sh", str(original)],
        "delete": ["/bin/rm", str(original)],
        "rename": ["/bin/mv", str(protected), str(alias)],
        "ancestor": ["/bin/mv", str(protected.parent), str(alias)],
        "link": ["/bin/ln", str(original), str(alias)],
        "symlink": ["/bin/sh", "-c", f'ln -s {shlex.quote(str(original))} "$1" && printf changed > "$1"',
                    "sh", str(alias)],
    }
    held = replace(policy(write=[tmp_path]), deny_write=(str(protected),))
    result = sandbox.run(commands[attack], held, diagnose="never")
    assert result.returncode != 0
    assert original.read_text() == "original"
    if attack != "symlink":
        assert not alias.exists()


def test_native_future_protection_and_ancestor_replacement(tmp_path, seatbelt):
    from tests.sandbox_kit import policy

    container = tmp_path / "repo"
    container.mkdir()
    future = container / ".git" / "config"
    held = replace(policy(write=[tmp_path]), deny_write=(str(future),))
    future.parent.mkdir()
    created = sandbox.run(["/usr/bin/touch", str(future)], held, diagnose="never")
    moved = sandbox.run(["/bin/mv", str(container), str(tmp_path / "moved")], held, diagnose="never")
    assert created.returncode != 0 and not future.exists()
    assert moved.returncode != 0 and container.exists()


def test_native_clone_cannot_copy_secret_and_normal_sibling_copy_works(tmp_path, seatbelt):
    from tests.sandbox_kit import policy

    secret = tmp_path / "secret"
    secret.write_text("private contents")
    source = tmp_path / "source"
    source.write_text("public contents")
    held = replace(policy(write=[tmp_path]), deny_read=(str(secret),))
    copied = tmp_path / "copy"
    denied = sandbox.run(["/bin/cp", "-c", str(secret), str(copied)], held, diagnose="never")
    assert denied.returncode != 0
    assert not copied.exists() or copied.read_text() != "private contents"
    normal = sandbox.run(["/bin/cp", str(source), str(copied)], held, diagnose="never")
    assert normal.returncode == 0 and copied.read_text() == "public contents"


def test_backend_revalidates_aliases_created_after_policy_validation(tmp_path):
    original = tmp_path / "config"
    original.write_text("protected")
    held = sandbox.Policy(deny_write=(str(original),)).validated()
    os.link(original, tmp_path / "alias")
    with pytest.raises(sandbox.PolicyError, match="aliases"):
        profile(held, "/bin/sh", "alias")


def test_exclusions_cannot_be_downgraded_to_unsandboxed_execution(tmp_path):
    from poolhouse.sandbox.container import Container

    marker = tmp_path / "ran"
    held = sandbox.Policy(deny_write=(str(tmp_path / "control"),))
    with pytest.raises(sandbox.PolicyError, match="cannot run without"):
        sandbox.run(["/usr/bin/touch", str(marker)], held, via=Container(),
                    unsandboxed=sandbox.AllowUnsandboxed("requested fallback"))
    assert not marker.exists()
