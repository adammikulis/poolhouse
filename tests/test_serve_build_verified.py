"""The llama.cpp patches are built against the upstream commit they were verified on, and a
patch set that no longer applies fails the build without touching the checkout.

The day this was written ``poolhouse-serve build`` fetched upstream's head, ``git apply --3way``
left ``common/speculative.cpp`` full of conflict markers inside the managed ``src`` tree, and the
next build started from that wreck. These tests run real git against a local bare repository
standing in for ggml-org/llama.cpp (only the https-only transport is swapped for a plain git
call) and a real patch that stops applying one upstream commit later. Only the cmake configure,
the compile and the install of the binary are stubbed: ``test_serve_build.py`` covers those.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.serve import build, build_patches, build_paths, build_source
from poolhouse.serve.build_paths import BuildFailed

_REAL_RUN = subprocess.run

PATCH = ("diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n"
         "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n")


def _run(cwd: Path, *args: str) -> str:
    done = _REAL_RUN(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                      "-c", "protocol.file.allow=always", *args],
                     cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _commit(work: Path, name: str, text: str, message: str) -> str:
    (work / name).write_text(text)
    _run(work, "add", name)
    _run(work, "commit", "-qm", message)
    return _run(work, "rev-parse", "HEAD")


def _real_git(*args, cwd=None, url=""):
    done = _REAL_RUN(["git", "-c", "protocol.file.allow=always", *args],
                     cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise BuildFailed(f"git {' '.join(args)} failed: {(done.stderr or '').strip()}")
    return done


def _build_args(**over):
    base = {"commit": "", "jobs": 1, "source": "", "force": False, "upstream_head": False}
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    """A bare repo with two commits the patch applies to, and the commit that breaks it."""
    work = tmp_path / "work"
    work.mkdir()
    _run(work, "init", "-q", "-b", "master")
    first = _commit(work, "a.txt", "one\ntwo\nthree\n", "first")
    second = _commit(work, "b.txt", "unrelated\n", "second")
    bare = tmp_path / "upstream.git"
    _run(tmp_path, "clone", "-q", "--bare", str(work), str(bare))

    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001-x.patch").write_text(PATCH)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "poolhouse"))
    monkeypatch.setenv("POOLHOUSE_LLAMA_PATCHES", str(patches))
    monkeypatch.setattr(build_source, "REPO_URL", str(bare))
    monkeypatch.setattr(build_source, "_git", _real_git)
    monkeypatch.setattr(build_source, "_configure", lambda source: None)
    monkeypatch.setattr(build_source, "_compile", lambda source, jobs: None)

    def install(build_dir, dest, commit, *, extra=None):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "BUILD.json").write_text(json.dumps({"commit": commit, **(extra or {})}))
        return dest

    monkeypatch.setattr(build_source, "_install_source_build", install)

    def pin(commit: str, stamp: str | None = None) -> None:
        (patches / "verified.json").write_text(json.dumps(
            {"commit": commit, "patches": build_paths.patch_stamp() if stamp is None else stamp}))

    def push(name: str, text: str, message: str) -> str:
        sha = _commit(work, name, text, message)
        _run(work, "push", "-q", str(bare), "master")
        return sha

    return SimpleNamespace(first=first, second=second, patches=patches, pin=pin, push=push,
                           bare=bare)


class TestTheVerifiedManifest:
    def test_no_manifest_pins_nothing(self, upstream):
        assert build_patches.verified_upstream() == ""

    def test_the_manifest_commit_is_the_one_a_build_defaults_to(self, upstream):
        upstream.pin(upstream.first)
        assert build_patches.verified_upstream() == upstream.first

    def test_a_manifest_naming_no_full_sha_is_refused(self, upstream):
        upstream.pin("abc1234")
        with pytest.raises(BuildFailed, match="full upstream commit SHA"):
            build_patches.verified_upstream()

    def test_a_manifest_that_is_not_json_is_refused(self, upstream):
        (upstream.patches / "verified.json").write_text("{nope")
        with pytest.raises(BuildFailed, match="not a verified-commit manifest"):
            build_patches.verified_upstream()

    def test_patches_changed_since_verified_still_name_the_commit_but_warn(
            self, upstream, capsys):
        upstream.pin(upstream.first, stamp="p0000000")
        assert build_patches.verified_upstream() == upstream.first
        assert "changed since" in capsys.readouterr().err

    def test_the_shipped_manifest_names_the_shipped_patches(self, monkeypatch):
        """A patch edited without being checked against upstream again fails here."""
        monkeypatch.delenv("POOLHOUSE_LLAMA_PATCHES", raising=False)
        data = json.loads(build_patches.manifest_path().read_text())
        assert build_patches.FULL_SHA.fullmatch(data["commit"])
        assert data["patches"] == build_paths.patch_stamp()


class TestTheDefaultCommit:
    def test_a_build_lands_on_the_verified_commit_not_masters_tip(self, upstream):
        upstream.pin(upstream.first)
        dest, tag = build_source.build_from_source(_build_args())
        assert tag.startswith(upstream.first[:7])
        assert json.loads((dest / "BUILD.json").read_text())["commit"] == tag
        tree = build_paths.src_dir()
        assert _run(tree, "rev-parse", "HEAD") == upstream.first
        assert not (tree / "b.txt").exists()

    def test_upstream_head_floats_at_masters_tip(self, upstream):
        upstream.pin(upstream.first)
        _, tag = build_source.build_from_source(_build_args(upstream_head=True))
        assert tag.startswith(upstream.second[:7])
        assert (build_paths.src_dir() / "b.txt").is_file()

    def test_an_explicit_commit_wins_over_the_manifest(self, upstream):
        upstream.pin(upstream.second)
        _, tag = build_source.build_from_source(_build_args(commit=upstream.first))
        assert tag.startswith(upstream.first[:7])

    def test_with_no_manifest_it_floats_as_before(self, upstream):
        _, tag = build_source.build_from_source(_build_args())
        assert tag.startswith(upstream.second[:7])


class TestAPatchThatNoLongerApplies:
    @pytest.fixture
    def broken(self, upstream, tmp_path):
        """src is built and patched at the verified commit; then upstream changes the line
        the patch edits, and the head build is the one that must fail."""
        upstream.pin(upstream.first)
        build_source.build_from_source(_build_args())
        tree = build_paths.src_dir()
        before = {"head": _run(tree, "rev-parse", "HEAD"),
                  "status": _run(tree, "status", "--porcelain"),
                  "text": (tree / "a.txt").read_text()}
        current = build_paths.current_link()
        current.parent.mkdir(parents=True, exist_ok=True)
        good = tmp_path / "good-build"
        good.mkdir()
        current.symlink_to(good)
        upstream.push("a.txt", "one\ndeux\nthree\n", "reword")
        return SimpleNamespace(tree=tree, before=before, current=current, good=good)

    def test_the_build_fails_and_names_the_patch(self, upstream, broken):
        with pytest.raises(BuildFailed, match=r"0001-x\.patch does not apply to upstream"):
            build_source.build_from_source(_build_args(upstream_head=True))

    def test_the_checkout_is_left_exactly_as_it_was(self, upstream, broken):
        with pytest.raises(BuildFailed):
            build_source.build_from_source(_build_args(upstream_head=True))
        tree = broken.tree
        assert _run(tree, "rev-parse", "HEAD") == broken.before["head"]
        assert _run(tree, "status", "--porcelain") == broken.before["status"]
        assert (tree / "a.txt").read_text() == broken.before["text"]
        assert "<<<<<<<" not in (tree / "a.txt").read_text()
        assert _run(tree, "ls-files", "--unmerged") == ""

    def test_no_trial_worktree_is_left_behind(self, upstream, broken):
        with pytest.raises(BuildFailed):
            build_source.build_from_source(_build_args(upstream_head=True))
        assert _run(broken.tree, "worktree", "list", "--porcelain").count("worktree ") == 1
        assert not list(build_paths.root().glob("trial-*"))

    def test_the_current_build_is_untouched(self, upstream, broken, capsys):
        assert build.cmd_build(SimpleNamespace(
            check=False, list=False, rollback=False, persist=False, adopt="", name="",
            repo="", source_kind="source", **vars(_build_args(upstream_head=True)))) == 2
        assert broken.current.resolve() == broken.good.resolve()
        assert "does not apply" in capsys.readouterr().err

    def test_the_message_gives_the_rebase_commands(self, upstream, broken):
        with pytest.raises(BuildFailed) as raised:
            build_source.build_from_source(_build_args(upstream_head=True))
        text = str(raised.value)
        assert f"git -C {broken.tree} worktree add {broken.tree}.rebase " in text
        assert "git apply --3way PATCH" in text and "git diff --cached > PATCH" in text
        assert "0001-x.patch" in text and str(build_patches.manifest_path()) in text
        assert "were not touched" in text
        assert "not upstream's head" in text

    def test_the_verified_commit_still_builds_after_the_failure(self, upstream, broken):
        with pytest.raises(BuildFailed):
            build_source.build_from_source(_build_args(upstream_head=True))
        _, tag = build_source.build_from_source(_build_args(force=True))
        assert tag.startswith(upstream.first[:7])
        assert "TWO" in (broken.tree / "a.txt").read_text()


@pytest.mark.parametrize('broken_patch', [False, True])
def test_failed_trial_cleanup_preserves_its_lock_bytes_and_unrelated_missing_registration(upstream, tmp_path, capsys, broken_patch):
    source = upstream.bare.parent / 'work'
    rev = upstream.push('a.txt', 'one\ndeux\nthree\n', 'conflicting change') if broken_patch else upstream.first
    other = tmp_path / 'unavailable'
    _run(source, 'worktree', 'add', '--detach', str(other), upstream.first)
    registration = Path((other / '.git').read_text().split('gitdir:', 1)[1].strip())
    metadata = {p.name: p.read_bytes() for p in registration.iterdir() if p.is_file()}
    other.rename(tmp_path / 'preserved-unavailable')
    trials = []
    retained = {}

    def locked_trial(*args, cwd=None):
        if args[0] == 'apply':
            trials.append(Path(cwd))
            _run(source, 'worktree', 'lock', '--reason', 'another operation holds this trial', str(cwd))
        if args[:2] == ('worktree', 'remove'):
            own = Path((trials[0] / '.git').read_text().split('gitdir:', 1)[1].strip())
            for path in (trials[0] / 'a.txt', trials[0] / '.git', own / 'locked'):
                retained[path] = path.read_bytes()
        return _real_git(*args, cwd=cwd)

    if broken_patch:
        with pytest.raises(BuildFailed, match='does not apply to upstream'):
            build_patches.check_patches(source, rev, locked_trial)
    else:
        assert build_patches.check_patches(source, rev, locked_trial) is None
    [trial] = trials
    assert (trial.is_dir(), registration.is_dir()) == (True, True)
    assert retained and all(path.read_bytes() == data for path, data in retained.items())
    assert 'locked' in _run(source, 'worktree', 'list', '--porcelain')
    assert f'worktree {other.resolve()}\n' in _run(source, 'worktree', 'list', '--porcelain') + '\n'
    assert {p.name: p.read_bytes() for p in registration.iterdir() if p.is_file()} == metadata
    warning = capsys.readouterr().err
    assert 'preserved' in warning and str(trial) in warning


@pytest.mark.parametrize('diagnostic_fails', [False, True])
def test_preserved_trial_warning_is_redacted_and_never_changes_patch_success(tmp_path, monkeypatch, capsys, diagnostic_fails):
    trial = tmp_path / 'trial'
    trial.mkdir()
    patch = tmp_path / 'test.patch'
    patch.touch()
    secret = 'unique-patch-trial-private-value'
    monkeypatch.setenv('PATCH_TRIAL_SECRET', secret)
    monkeypatch.setattr(build_patches, '_worktree', lambda *_args: trial)
    monkeypatch.setattr(build_patches, 'patch_files', lambda: [patch])

    def git(*args, **_kwargs):
        if args[0] == 'worktree':
            raise BuildFailed(f'removal refused {secret}')

    if diagnostic_fails:
        def unavailable(*_args):
            raise OSError('diagnostic stream unavailable')
        monkeypatch.setattr(build_patches, 'warn', unavailable)
    assert build_patches.check_patches(tmp_path, 'HEAD', git) is None
    assert trial.exists()
    output = capsys.readouterr().err
    assert secret not in output
    if not diagnostic_fails:
        assert 'preserved' in output and 'cleanup failed' in output
