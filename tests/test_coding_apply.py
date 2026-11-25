"""Applying a change set to the person's checkout: the person's save between any two steps is never overwritten, and a crash is completed or undone."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from poolhouse import coding_apply as apply_module, files
from poolhouse.coding_apply import Edit, digest, recover

BASE, RESULT, PERSON = b"base = 1\n", b"result = 2\n", b"person = 3\n"


@pytest.fixture
def world(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_bytes(BASE)
    return project, tmp_path / "state" / "log", tmp_path / "state" / "kept"


def edit(data: bytes | None = RESULT, base: bytes | None = BASE, path: str = "src/app.py", mode: int = 0o644, first: bool = False) -> Edit:
    return Edit(path, digest(base), data, mode, kept_first=first)


def apply(project: Path, edits: list[Edit], *, log: Path, keep: Path, **options) -> list[apply_module.Outcome]:
    """`coding_apply.apply` with the directory of the displaced versions beside the kept one, opened as the launcher opens it."""
    directory = keep.parent / "restored"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return apply_module.apply(project, edits, apply_module.Places(log, keep, descriptor, **options))
    finally:
        os.close(descriptor)


def restored(keep: Path) -> dict[str, bytes]:
    """The versions kept first: the name each is kept under, and its bytes."""
    return {each.name: each.read_bytes() for each in (keep.parent / "restored").iterdir()}


def left_over(project: Path) -> list[str]:
    return sorted(each.name for each in project.rglob("*") if each.name.startswith((apply_module.TEMP, apply_module.CONFLICT)))


def everything(project: Path, keep: Path) -> set[bytes]:
    return {each.read_bytes() for each in [*project.rglob("*"), *keep.rglob("*")] if each.is_file() and not each.is_symlink()}


def test_a_change_is_applied_by_exchange_with_the_stage_mode_and_leaves_no_name_behind(world):
    project, log, keep = world
    [done] = apply(project, [edit(mode=0o664)], log=log, keep=keep)
    assert (done.state, done.method) == ("applied", "exchange") and (project / "src" / "app.py").read_bytes() == RESULT
    assert (project / "src" / "app.py").stat().st_mode & 0o777 == 0o644 and left_over(project) == [] and not keep.exists()
    assert [line.split('"step": "')[1].split('"')[0] for line in log.read_text().splitlines()] == ["intent", "prepared", "swapped", "done", "finished"]


def test_a_live_file_that_is_neither_the_base_nor_the_result_is_kept_as_the_person_left_it(world):
    project, log, keep = world
    (project / "src" / "app.py").write_bytes(PERSON)
    [done] = apply(project, [edit()], log=log, keep=keep)
    assert done.state == "conflict" and (project / "src" / "app.py").read_bytes() == PERSON and done.kept.read_bytes() == RESULT
    [same] = apply(project, [edit(data=PERSON)], log=log, keep=keep)
    assert same.state == "unchanged"


def person_saves(project: Path, how: str, at: str):
    """The person's editor saving the live file the moment the step ``at`` is reached, in place, by a rename over it or by a delete and a re-create."""
    def after(path: str, step: str) -> None:
        if step != at:
            return
        live = project / path
        if how == "in place":
            with live.open("r+b") as stream:
                stream.truncate()
                stream.write(PERSON)
        elif how == "rename":
            fresh = live.with_name("editor.tmp")
            fresh.write_bytes(PERSON)
            fresh.replace(live)
        else:
            live.unlink()
            live.write_bytes(PERSON)
    return after


@pytest.mark.parametrize("step", ["held", "prepared", "swapped"])
@pytest.mark.parametrize("how", ["in place", "rename", "delete and re-create"])
def test_publication_never_overwrites_a_concurrent_edit(world, how, step):
    project, log, keep = world
    [done] = apply(project, [edit()], log=log, keep=keep, after=person_saves(project, how, step))
    live = (project / "src" / "app.py").read_bytes()
    beside = [each.read_bytes() for each in project.rglob("*") if apm_conflict(each)]
    assert PERSON in (live, *beside), f"the person's version is under the name or beside it ({how} at {step}: {done})"
    assert live == RESULT or RESULT in (done.kept.read_bytes() if done.kept else b"",) or done.state == "applied"
    if step != "swapped":
        assert done.state == "conflict" and done.kept.read_bytes() == RESULT, "a save after step 1 is found by the verification and undone"
    assert not [name for name in left_over(project) if name.startswith(apply_module.TEMP)], "no temporary name is left"


def apm_conflict(each: Path) -> bool:
    return each.is_file() and each.name.startswith("app.py" + apply_module.CONFLICT)


def displaced(project: Path) -> Path:
    return next(each for each in project.rglob("*") if each.name.startswith(apply_module.TEMP))


def test_the_exchange_moves_the_change_time_of_the_displaced_file_so_the_check_is_the_digest_read_through_the_held_descriptor(world):
    """The ctime question of 4.4.3 step 4: a file renamed by the exchange has a new change time with nothing written, so comparing it would refuse
    every application; the identity holds the inode, size, modification time and mode, and the digest is read again through the descriptor held since step 1."""
    project, log, keep = world
    live = project / "src" / "app.py"
    seen = {}

    def after(path: str, step: str) -> None:
        if step == "held":
            seen["before"] = live.stat()
        if step == "swapped":
            seen["after"] = displaced(project).stat()

    [done] = apply(project, [edit()], log=log, keep=keep, after=after)
    assert done.state == "applied" and (seen["after"].st_ino, seen["after"].st_mtime_ns) == (seen["before"].st_ino, seen["before"].st_mtime_ns)
    assert seen["after"].st_ctime_ns != seen["before"].st_ctime_ns, "the exchange itself moved the change time: it cannot be part of the identity"


def test_a_save_that_keeps_the_size_and_the_modification_time_is_found_by_the_digest_read_through_the_held_descriptor(world):
    project, log, keep = world
    same_size = b"mine = 3\n"
    assert len(same_size) == len(BASE)

    def after(path: str, step: str) -> None:
        if step == "swapped":
            held = displaced(project)
            before = held.stat()
            with held.open("r+b") as stream:
                stream.write(same_size)
            os.utime(held, ns=(before.st_atime_ns, before.st_mtime_ns))

    [done] = apply(project, [edit()], log=log, keep=keep, after=after)
    assert done.state == "conflict" and (project / "src" / "app.py").read_bytes() == same_size and done.kept.read_bytes() == RESULT
    assert left_over(project) == []


def test_a_new_link_is_installed_where_nothing_is_and_never_replaces_a_file_or_a_link(world):
    project, log, keep = world
    (project / "src" / "real.py").write_bytes(BASE)
    [made] = apply(project, [Edit("src/alias.py", None, None, link="real.py")], log=log, keep=keep)
    assert (made.state, made.method) == ("applied", "link") and (project / "src" / "alias.py").readlink() == Path("real.py") and left_over(project) == []
    [again] = apply(project, [Edit("src/alias.py", None, None, link="real.py")], log=log, keep=keep)
    assert again.state == "refused", "a link already there is not opened, so it is not replaced"
    (project / "src" / "file.py").write_bytes(PERSON)
    [raced] = apply(project, [Edit("src/file.py", None, None, link="real.py")], log=log, keep=keep)
    assert raced.state == "conflict" and (project / "src" / "file.py").read_bytes() == PERSON and raced.kept.read_text() == "real.py"
    appears = lambda path, step: (project / path).write_bytes(PERSON) if step == "prepared" else None  # noqa: E731
    [late] = apply(project, [Edit("src/late.py", None, None, link="real.py")], log=log, keep=keep, after=appears)
    assert late.state == "conflict" and (project / "src" / "late.py").read_bytes() == PERSON and left_over(project) == []


def test_an_exchange_the_filesystem_cannot_do_is_a_claim_that_never_replaces(world, monkeypatch):
    project, log, keep = world
    monkeypatch.setattr(files, "swap", lambda *args, **kwargs: (_ for _ in ()).throw(OSError(errno.ENOTSUP, "no exchange here")))
    [done] = apply(project, [edit()], log=log, keep=keep)
    assert (done.state, done.method) == ("applied", "claim") and (project / "src" / "app.py").read_bytes() == RESULT and left_over(project) == []
    (project / "src" / "app.py").write_bytes(BASE)
    [raced] = apply(project, [edit()], log=log, keep=keep, after=person_saves(project, "in place", "prepared"))
    assert raced.state == "conflict" and (project / "src" / "app.py").read_bytes() == PERSON


def test_a_new_file_is_installed_by_a_rename_that_never_replaces_and_a_deletion_is_verified_before_it_is_final(world):
    project, log, keep = world
    [made] = apply(project, [edit(base=None, path="src/new.py")], log=log, keep=keep)
    assert (made.state, made.method) == ("applied", "create") and (project / "src" / "new.py").read_bytes() == RESULT
    race = lambda path, step: (project / path).write_bytes(PERSON) if step == "prepared" else None  # noqa: E731
    [lost] = apply(project, [edit(base=None, path="src/other.py")], log=log, keep=keep, after=race)
    assert lost.state == "conflict" and (project / "src" / "other.py").read_bytes() == PERSON and left_over(project) == []
    [gone] = apply(project, [edit(data=None, path="src/app.py")], log=log, keep=keep)
    assert gone.state == "applied" and not (project / "src" / "app.py").exists()
    (project / "src" / "app.py").write_bytes(BASE)
    [kept] = apply(project, [edit(data=None)], log=log, keep=keep, after=person_saves(project, "in place", "held"))
    assert kept.state == "conflict" and (project / "src" / "app.py").read_bytes() == PERSON and left_over(project) == []
    (project / "src" / "app.py").write_bytes(BASE)
    recreated = lambda path, step: (project / path).write_bytes(PERSON) if step == "claimed" else None  # noqa: E731
    [again] = apply(project, [edit(data=None)], log=log, keep=keep, after=recreated)
    assert again.state == "applied" and (project / "src" / "app.py").read_bytes() == PERSON, "a file the person made after the claim is not the one deleted"


def test_a_link_anywhere_in_the_live_path_is_refused_and_nothing_is_written_through_it(world, tmp_path):
    project, log, keep = world
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_bytes(BASE)
    (project / "linked").symlink_to(outside)
    (project / "src" / "tail.py").symlink_to(outside / "x.py")
    done = apply(project, [edit(path="linked/x.py"), edit(path="src/tail.py")], log=log, keep=keep)
    assert [each.state for each in done] == ["refused", "refused"]
    assert (outside / "x.py").read_bytes() == BASE and sorted(each.name for each in outside.iterdir()) == ["x.py"]


class Crash(Exception):
    pass


def crash_at(step: str, then=lambda path: None):
    def after(path: str, reached: str) -> None:
        if reached == step:
            raise Crash(path)
    return after


def test_a_crash_after_the_exchange_is_completed_by_the_next_launch_or_undone_when_the_person_saved(world):
    project, log, keep = world
    with pytest.raises(Crash):
        apply(project, [edit()], log=log, keep=keep, after=crash_at("swapped"))
    assert left_over(project), "the crash left the displaced version behind"
    [done] = recover(project, log)
    assert done.state == "applied" and (project / "src" / "app.py").read_bytes() == RESULT and left_over(project) == []
    log.unlink()
    (project / "src" / "app.py").write_bytes(BASE)

    def saved_then_crashed(path: str, step: str) -> None:
        if step == "held":
            (project / path).write_bytes(PERSON)
        crash_at("swapped")(path, step)

    with pytest.raises(Crash):
        apply(project, [edit()], log=log, keep=keep, after=saved_then_crashed)
    [undone] = recover(project, log)
    assert undone.state == "conflict" and (project / "src" / "app.py").read_bytes() == PERSON and left_over(project) == []


def test_a_version_an_overwrite_or_a_deletion_displaces_is_kept_first_read_only_and_named_by_its_digest_when_git_does_not_hold_it(world):
    project, log, keep = world
    seen = {}

    def after(path: str, step: str) -> None:
        if step == "restored":
            seen["copies"], seen["live"] = restored(keep), (project / path).read_bytes()

    [done] = apply(project, [edit(first=True)], log=log, keep=keep, after=after)
    name = digest(BASE)
    assert (done.state, done.restored) == ("applied", name) and (project / "src" / "app.py").read_bytes() == RESULT
    assert seen == {"copies": {name: BASE}, "live": BASE}, "the copy is on disk before anything is displaced"
    assert restored(keep) == {name: BASE} and (keep.parent / "restored" / name).stat().st_mode & 0o777 == 0o400
    assert not [each for each in (keep.parent / "restored").iterdir() if each.name.startswith(apply_module.TEMP)], "no half-written copy is left"
    (project / "src" / "app.py").write_bytes(PERSON)
    [gone] = apply(project, [edit(data=None, base=PERSON, first=True)], log=log, keep=keep)
    assert (gone.state, gone.restored) == ("applied", digest(PERSON)) and not (project / "src" / "app.py").exists()
    assert restored(keep) == {name: BASE, digest(PERSON): PERSON}


def test_a_file_git_holds_is_not_copied_and_a_new_file_displaces_nothing(world):
    project, log, keep = world
    [held] = apply(project, [edit()], log=log, keep=keep)
    [made] = apply(project, [edit(base=None, path="src/new.py", first=True)], log=log, keep=keep)
    assert (held.state, held.restored, made.state, made.restored) == ("applied", "", "applied", "") and restored(keep) == {}


def test_two_displaced_files_with_the_same_bytes_share_one_copy(world):
    project, log, keep = world
    (project / "src" / "twin.py").write_bytes(BASE)
    done = apply(project, [edit(first=True), edit(path="src/twin.py", first=True)], log=log, keep=keep)
    assert [each.state for each in done] == ["applied", "applied"] and {each.restored for each in done} == {digest(BASE)}
    assert restored(keep) == {digest(BASE): BASE}


def test_a_displaced_version_that_cannot_be_kept_stops_the_change_and_changes_nothing(world):
    project, log, keep = world
    (keep.parent / "restored").mkdir(parents=True)
    (keep.parent / "restored").chmod(0o500)
    try:
        [done] = apply(project, [edit(first=True)], log=log, keep=keep)
    finally:
        (keep.parent / "restored").chmod(0o700)
    assert done.state == "unkept" and (project / "src" / "app.py").read_bytes() == BASE and left_over(project) == [] and restored(keep) == {}


def test_a_save_between_the_hold_and_the_copy_is_a_conflict_and_keeps_nothing(world):
    project, log, keep = world
    [done] = apply(project, [edit(first=True)], log=log, keep=keep, after=person_saves(project, "in place", "held"))
    assert done.state == "conflict" and done.restored == "" and (project / "src" / "app.py").read_bytes() == PERSON and done.kept.read_bytes() == RESULT
    assert restored(keep) == {} and left_over(project) == []


@pytest.mark.parametrize("how", ["in place", "rename", "delete and re-create"])
def test_a_save_after_the_copy_is_found_by_the_verification_and_what_is_displaced_is_the_persons_version(world, how):
    project, log, keep = world
    [done] = apply(project, [edit(first=True)], log=log, keep=keep, after=person_saves(project, how, "restored"))
    assert done.state == "conflict" and done.restored == "" and (project / "src" / "app.py").read_bytes() == PERSON, "the person's newest version is live again"
    assert left_over(project) == []


def test_a_crash_after_the_swap_leaves_the_copy_the_completed_swap_depends_on(world):
    project, log, keep = world
    with pytest.raises(Crash):
        apply(project, [edit(first=True)], log=log, keep=keep, after=crash_at("swapped"))
    assert restored(keep) == {digest(BASE): BASE}, "the copy was made before the swap, so a crash at any later step loses nothing"
    [done] = recover(project, log)
    assert done.state == "applied" and (project / "src" / "app.py").read_bytes() == RESULT and left_over(project) == []
