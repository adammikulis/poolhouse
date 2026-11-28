"""The upstream commit the llama.cpp patches were last verified against, and a trial apply that
cannot leave a checkout conflicted.

A patch set is a rebase of a few files onto upstream's master, and master moves faster than the
patches do: the day this was written ``git apply --3way`` failed on ``common/speculative.cpp``
and left the managed ``src`` tree half-patched, with conflict markers in it. So the patch
directory carries ``verified.json`` -- the upstream commit the set was last checked to apply to
-- a build lands on that commit unless asked to float (``--upstream-head``), and every build
applies the patches into a throwaway worktree first. Only a set that applies there touches the
real checkout, so a failure leaves ``src`` and ``current`` exactly as they were.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path

from poolhouse.log import warn
from poolhouse.redact.secrets import env_secrets, redact
from poolhouse.serve.build_paths import BuildFailed, patch_files, patch_stamp, patches_dir, root

__all__ = ["MANIFEST", "check_patches", "manifest_path", "pinned_commit", "pinned_release", "rebase_help",
           "verified_upstream"]

MANIFEST = "verified.json"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
TAG = re.compile(r"b[0-9]+")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
RELEASES = "https://github.com/ggml-org/llama.cpp/releases/download"


def manifest_path() -> Path:
    """The file beside the patches naming the upstream commit they were verified against."""
    return patches_dir() / MANIFEST


def verified_upstream() -> str:
    """The upstream commit the patch set was last verified to apply to, ``""`` when the
    directory has no patches or no manifest (nothing is then pinned: master's tip is built).

    A manifest whose ``patches`` stamp is not the current set's is still followed -- it names
    the best known base -- but is warned about, since the trial apply is what decides.
    """
    path = manifest_path()
    if not patch_files() or not path.is_file():
        return ""
    data, commit = _manifest(path)
    if data.get("patches") != patch_stamp():
        warn(f"the patches changed since {commit[:9]} was verified: after checking them against "
             f"it, set \"patches\" to \"{patch_stamp()}\" in {path}")
    return commit


def _manifest(path: Path) -> tuple[dict, str]:
    """The manifest at ``path`` and the full commit it names."""
    try:
        data = json.loads(path.read_text())
        commit = str(data["commit"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BuildFailed(f"{path} is not a verified-commit manifest ({exc}); it needs "
                          '{"commit": "<40 hex>", "patches": "<stamp>"}') from None
    if not FULL_SHA.fullmatch(commit):
        raise BuildFailed(f"{path} names {commit!r}, which is not a full upstream commit SHA")
    return data, commit


def pinned_commit() -> str:
    """The upstream commit anything fetched from llama.cpp's source is held to, with or without patches to apply.
    Raises `BuildFailed` when nothing is pinned, so nothing is fetched."""
    return _manifest(manifest_path())[1]


def pinned_release() -> dict:
    """The one llama.cpp release that is downloaded, in the shape GitHub reports a release in, each asset with the
    sha256 the manifest records for it. Raises `BuildFailed` when none is pinned, so none is downloaded."""
    path = manifest_path()
    try:
        release = _manifest(path)[0]["release"]
        tag = str(release["tag"])
        assets = [{"name": name, "size": int(one["size"]), "digest": f"sha256:{one['sha256']}",
                   "browser_download_url": f"{RELEASES}/{tag}/{name}"} for name, one in sorted(release["assets"].items())]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise BuildFailed(f"{path} pins no llama.cpp release ({exc}), so none is downloaded") from None
    if not TAG.fullmatch(tag) or not assets or not all(DIGEST.fullmatch(one["digest"]) for one in assets):
        raise BuildFailed(f"{path} pins a llama.cpp release without a build tag and a sha256 for each asset")
    return {"tag_name": tag, "assets": assets}


def rebase_help(source: Path, rev: str, failed: Path, detail: str) -> str:
    """What failed, what was left alone, and the commands that rebase the patch set."""
    work = f"{source}.rebase"
    order = " ".join(item.name for item in patch_files())
    return "\n".join([
        f"{failed.name} does not apply to upstream {rev}: {detail}",
        f"  {source} and the current build were not touched. To rebase the patches onto it:",
        f"    git -C {source} worktree add {work} {rev}",
        f"    cd {work}",
        f"    for each of {order} (in {patches_dir()}), in that order:",
        "      git apply --3way PATCH     # resolve the conflict markers, then git add the files",
        "      git diff --cached > PATCH && git commit -qm PATCH",
        f"  then set \"commit\" to {rev} and \"patches\" to the stamp the next build warns about, in "
        f"{manifest_path()}.",
        f"  Until then `poolhouse-serve build` lands on the commit in {MANIFEST}, not upstream's head.",
    ])


def _worktree(source: Path, rev: str, git) -> Path:
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    trial = Path(tempfile.mkdtemp(prefix="trial-", dir=base))
    git("worktree", "add", "--detach", str(trial), rev, cwd=source)
    return trial


def check_patches(source: Path, rev: str, git) -> None:
    """Apply every patch, in order, to a throwaway worktree of ``source`` at ``rev``.

    ``git`` is the build's own runner (it raises `BuildFailed`). Raises `BuildFailed` with the
    rebase commands when one does not apply; ``source`` itself is never written to either way.
    """
    files = patch_files()
    if not files:
        return
    trial = _worktree(source, rev, git)
    try:
        for item in files:
            try:
                git("apply", "--3way", str(item.resolve()), cwd=trial)
            except BuildFailed as exc:
                raise BuildFailed(rebase_help(source, rev, item, str(exc))) from None
    finally:
        try:
            git("worktree", "remove", "--force", str(trial), cwd=source)
        except BuildFailed as exc:
            # A diagnostic must not replace the patch decision or destroy a refused trial.
            with contextlib.suppress(Exception):
                reason = str(exc).splitlines()[0] if str(exc) else "Git refused trial removal"
                message, _ = redact(f"patch trial preserved at {trial}; cleanup failed: {reason}", env_secrets(os.environ))
                warn(" ".join(message.split())[:1000])
