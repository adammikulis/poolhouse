"""Authenticated fixed repository publication through the installed GitHub App holder."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from poolhouse import authority, credentials, home, real_machine, worktreerules
from poolhouse.board.session import connect
from poolhouse.net import github_release as remote
from poolhouse.workspace import github_app
from poolhouse.workspace.landing import eligible

SHA = re.compile(r"[0-9a-f]{40}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
PERMISSIONS = {"contents": "write", "workflows": "write", "metadata": "read"}


@dataclass(frozen=True)
class Source:
    checkout: Path
    branch: str
    commit: str
    repo: str


def environment(directory: Path) -> dict[str, str]:
    """Return a private Git environment without ambient configuration or credentials."""
    env = {"PATH": os.defpath, "HOME": str(directory), "XDG_CONFIG_HOME": str(directory),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_ALLOW_PROTOCOL": "https", "PYTHON": sys.executable}
    for name in ("POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_HOME", "CLAUDECODE", "POOLHOUSE_AGENT"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    env["POOLHOUSE_NONINTERACTIVE"] = "1"
    return env


def _read(checkout: Path, args: tuple[str, ...]) -> bytes:
    done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(checkout), *args],
                          env=environment(checkout), capture_output=True, timeout=30, check=False)
    if done.returncode:
        raise remote.Failed("the source repository could not be verified")
    return done.stdout


def local(checkout: Path, *args: str) -> str:
    """Read source Git text without user configuration, filters, replacements or prompts."""
    return _read(checkout, args).decode().strip()


def source(checkout: Path, repo: str) -> Source:
    """Validate the actual primary development checkout and its exact selected commit."""
    checkout = checkout.resolve()
    found = worktreerules.checkouts(checkout)
    if found is None or found[0] != found[1] or found[0] != checkout:
        raise remote.Failed("publication uses the primary development checkout")
    branch = local(checkout, "symbolic-ref", "--short", "HEAD")
    commit = local(checkout, "rev-parse", "HEAD^{commit}")
    origin = local(checkout, "config", "--local", "--get", "remote.origin.url")
    if (not BRANCH.fullmatch(branch) or branch in {"main", "master"} or not SHA.fullmatch(commit)
            or origin not in {f"https://github.com/{repo}.git", f"git@github.com:{repo}.git"}):
        raise remote.Failed("the configured repository and development branch must match the App")
    return Source(checkout, branch, commit, repo)


def authorize(action: str):
    """Authenticate a live eligible main session and audit its fixed publication action."""
    real_machine.require("GitHub App publication")
    if not Path(__file__).resolve().is_relative_to(home.home().resolve() / "runtimes"):
        raise remote.Failed("GitHub publication uses the installed credential holder")
    session = connect()
    who = session.whoami()
    if who.retired or who.parent or eligible(who) or who.name != os.environ.get("POOLHOUSE_WORKSPACE_AGENT"):
        raise remote.Failed("GitHub publication requires the authenticated live lead session")
    authority.require("github.release", action)
    return who


def identity() -> github_app.Identity:
    """Open only the configured repository App item inside the credential holder."""
    held = credentials._open(github_app.ITEM)
    if held is None:
        raise remote.Failed("the repository App is not configured; its initial setup needs the owner")
    who = github_app.decode(held)
    if not who.installation_id:
        raise remote.Failed("the repository App installation is unfinished")
    return who


def _hooks(selected: Source, directory: Path) -> Path:
    stamp = Path(__file__).parents[1] / "fleet" / "built-from"
    built = stamp.read_text().strip()
    if not SHA.fullmatch(built) or local(selected.checkout, "rev-parse", f"{built}^{{commit}}") != built:
        raise remote.Failed("the installed hook source cannot be verified")
    hooks = directory / "scripts" / "hooks"
    hooks.mkdir(parents=True)
    rows = _read(selected.checkout, ("ls-tree", "-rz", built, "scripts/hooks")).split(b"\0")[:-1]
    if len(rows) > 500:
        raise remote.Failed("the installed hook inventory is too large")
    for row in rows:
        metadata, raw_name = row.split(b'\t', 1)
        name = raw_name.decode('ascii')
        mode, kind, digest = metadata.decode().split()
        relative = Path(name).relative_to("scripts/hooks")
        if kind != "blob" or mode not in {"100644", "100755"} or '..' in relative.parts or not re.fullmatch(r'scripts/hooks/[A-Za-z0-9_./-]+', name):
            raise remote.Failed("installed hook files must be regular verified source blobs")
        if int(local(selected.checkout, "cat-file", "-s", digest)) > 2 << 20:
            raise remote.Failed("an installed hook is too large")
        body = _read(selected.checkout, ("cat-file", "blob", digest))
        target = hooks / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        target.chmod(0o700 if mode == "100755" else 0o600)
    if any(not (hooks / name).is_file() for name in ("pre-push", "pushed", "no-data-files")):
        raise remote.Failed("the installed pre-push hook inventory is incomplete")
    return hooks


def _view(selected: Source, directory: Path) -> None:
    local(directory, "init", "--quiet", f"--initial-branch={selected.branch}")
    objects = Path(local(selected.checkout, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    (directory / ".git/objects/info/alternates").write_text(str(objects) + '\n')
    local(directory, "update-ref", f"refs/heads/{selected.branch}", selected.commit)


def _commit_ref(reply: object) -> str:
    if not isinstance(reply, dict) or not isinstance(reply.get("object"), dict):
        raise remote.Failed("GitHub returned an invalid commit reference")
    target = reply["object"]
    if target.get("type") != "commit" or not isinstance(target.get("sha"), str) or not SHA.fullmatch(target["sha"]):
        raise remote.Failed("GitHub returned an invalid commit reference")
    return target["sha"]


def push_development(checkout: Path) -> dict[str, str]:
    """Push one exact development tip through the installed repository App."""
    authorize("push the exact reviewed development tip")
    who = identity()
    selected = source(checkout, who.repo)
    authority.require("github.release", json.dumps({"operation": "push-development", "repo": who.repo,
                                                   "branch": selected.branch, "sha": selected.commit}, sort_keys=True))
    path = f"/repos/{who.repo}/git/ref/heads/{selected.branch}"
    before = _commit_ref(remote.request("GET", path))
    if not SHA.fullmatch(before) or local(checkout, "merge-base", before, selected.commit) != before:
        raise remote.Failed("the development push must fast-forward the current remote tip")
    if before == selected.commit:
        return {"sha": selected.commit}
    with tempfile.TemporaryDirectory(prefix="poolhouse-app-push-") as temporary:
        directory = Path(temporary)
        _view(selected, directory)
        hooks = _hooks(selected, directory)
        env = environment(directory)
        line = f"refs/heads/{selected.branch} {selected.commit} refs/heads/{selected.branch} {before}\n"
        checked = subprocess.run(["/bin/sh", str(hooks / "pre-push"), "origin",
                                  f"https://github.com/{who.repo}.git"], cwd=directory, env=env,
                                 input=line, capture_output=True, text=True, timeout=120, check=False)
        if checked.returncode:
            raise remote.Failed("the maintained pre-push checks refused this development tip")
        if source(checkout, who.repo) != selected:
            raise remote.Failed("the selected development tip changed before publication")
        latest = _commit_ref(remote.request("GET", path))
        if latest != before:
            raise remote.Failed("the remote development tip changed before publication")
        token = github_app.mint(who, PERMISSIONS, who.repo)["token"]
        remote.push(remote.GitPush(who.repo, selected.branch, selected.commit, token), directory, env)
    after = _commit_ref(remote.request("GET", path, bearer=token))
    if after != selected.commit:
        raise remote.Failed("GitHub did not report the requested development tip")
    return {"sha": selected.commit}


@dataclass(frozen=True)
class Asset:
    name: str
    body: bytes = field(repr=False)
    sha256: str


def _asset(directory: Path, name: str, limit: int = 64 << 20) -> Asset:
    import hashlib

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise remote.Failed("release assets have fixed plain filenames")
    descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or not 0 < before.st_size <= limit:
            raise remote.Failed("release assets must be bounded owned regular files")
        body = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        named = (directory / name).lstat()
        identity_fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (len(body) != before.st_size
                or any(getattr(after, key) != getattr(before, key) for key in identity_fields)
                or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)):
            raise remote.Failed("a release asset changed while being pinned")
    return Asset(name, body, hashlib.sha256(body).hexdigest())


def _contents(assets: dict[str, Asset], manifest: dict) -> None:
    import io
    import plistlib
    import zipfile
    from email import message_from_bytes

    version, python_version = manifest["version"], manifest["python_version"]
    wheel = assets[f"poolhouse-{python_version}-py3-none-any.whl"]
    app = assets[next(name for name in assets if name.endswith('.zip'))]
    with zipfile.ZipFile(io.BytesIO(wheel.body)) as archive:
        metadata = f"poolhouse-{python_version}.dist-info/METADATA"
        names = (metadata, "poolhouse/fleet/built-from")
        if any(archive.getinfo(name).file_size > 64 << 10 for name in names):
            raise remote.Failed("wheel provenance metadata is too large")
        if (message_from_bytes(archive.read(metadata))["Version"] != python_version
                or archive.read(names[1]).decode().strip() != manifest["source_commit"]):
            raise remote.Failed("wheel version or source provenance does not match the release")
    with zipfile.ZipFile(io.BytesIO(app.body)) as archive:
        name = "Poolhouse.app/Contents/Info.plist"
        if archive.getinfo(name).file_size > 64 << 10:
            raise remote.Failed("application version metadata is too large")
        if plistlib.loads(archive.read(name)).get("CFBundleShortVersionString") != version:
            raise remote.Failed("application version does not match the release")


def artifacts(selected: Source) -> tuple[dict, dict[str, Asset]]:
    """Pin the four build outputs and verify their version, source and checksum contract."""
    from packaging.version import Version

    version = local(selected.checkout, "show", f"{selected.commit}:version.txt")
    parsed = Version(version)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-dev\.[0-9]+", version) or parsed.dev is None:
        raise remote.Failed("this command publishes development prereleases only")
    directory = selected.checkout / "dist" / f"{parsed.major}.{parsed.minor}dev{parsed.dev}"
    if directory.is_symlink() or directory.resolve() != directory:
        raise remote.Failed("release output must use its fixed local build directory")
    info = _asset(directory, "build-info.json", 64 << 10)
    manifest = json.loads(info.body)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), dict):
        raise remote.Failed("release build provenance must be a valid object")
    names = {f"poolhouse-{parsed}-py3-none-any.whl", f"Poolhouse-{parsed.major}.{parsed.minor}dev{parsed.dev}-macos-arm64.zip"}
    if (manifest.get("schema") != 1 or manifest.get("version") != version
            or manifest.get("python_version") != str(parsed) or manifest.get("tag") != f"v{version}"
            or not SHA.fullmatch(str(manifest.get("source_commit", "")))
            or set(manifest.get("assets", {})) != names):
        raise remote.Failed("release build provenance does not match the selected version")
    commit = manifest["source_commit"]
    if (local(selected.checkout, "merge-base", commit, selected.commit) != commit
            or local(selected.checkout, "show", f"{commit}:version.txt") != version):
        raise remote.Failed("release source is not the versioned development ancestor")
    pinned = {name: _asset(directory, name) for name in sorted(names)}
    for name, asset in pinned.items():
        if manifest["assets"][name] != {"sha256": asset.sha256, "bytes": len(asset.body)}:
            raise remote.Failed("release artifact bytes do not match build provenance")
    pinned[info.name] = info
    sums = _asset(directory, "SHA256SUMS", 64 << 10)
    expected = {f"{asset.sha256}  {name}" for name, asset in pinned.items()}
    if set(sums.body.decode().splitlines()) != expected:
        raise remote.Failed("release checksums do not match pinned artifact bytes")
    pinned[sums.name] = sums
    _contents(pinned, manifest)
    return manifest, pinned


def _found(path: str, token: str):
    try:
        return remote.request("GET", path, bearer=token)
    except remote.Gone:
        return None


def _release_metadata(release: object, manifest: dict, repo: str) -> str:
    tag, commit = manifest["tag"], manifest["source_commit"]
    if not isinstance(release, dict):
        raise remote.Failed("GitHub returned an invalid release object")
    if (release.get("tag_name") != tag or release.get("name") != manifest["version"]
            or release.get("target_commitish") != commit or release.get("prerelease") is not True or release.get("draft") is not False
            or type(release.get("id")) is not int or release["id"] <= 0):
        raise remote.Failed("the existing GitHub release does not match this prerelease")
    url = f"https://github.com/{repo}/releases/tag/{tag}"
    if release.get("html_url") != url:
        raise remote.Failed("GitHub returned an unexpected public release URL")
    return url


def publish_release(checkout: Path) -> dict[str, object]:
    """Publish the fixed versioned development prerelease with matching immutable assets."""
    authorize("publish the reviewed development prerelease")
    who = identity()
    selected = source(checkout, who.repo)
    manifest, pinned = artifacts(selected)
    commit, tag = manifest["source_commit"], manifest["tag"]
    authority.require("github.release", json.dumps({"operation": "publish-release", "repo": who.repo,
        "tag": tag, "sha": commit, "assets": {name: asset.sha256 for name, asset in pinned.items()}}, sort_keys=True))
    if source(checkout, who.repo) != selected:
        raise remote.Failed("the selected source changed before publication")
    token = github_app.mint(who, PERMISSIONS, who.repo)["token"]
    root = f"/repos/{who.repo}"
    published = _commit_ref(remote.request("GET", f"{root}/git/ref/heads/{selected.branch}", bearer=token))
    if not SHA.fullmatch(published) or local(checkout, "merge-base", commit, published) != commit:
        raise remote.Failed("release source is not on the published development branch")
    existing_tag = _found(f"{root}/git/ref/tags/{tag}", token)
    if existing_tag is not None and existing_tag.get("object") != {"type": "commit", "sha": commit}:
        obj = existing_tag.get("object", {})
        if obj.get("type") != "commit" or obj.get("sha") != commit:
            raise remote.Failed("the existing release tag points somewhere else")
    release = _found(f"{root}/releases/tags/{tag}", token)
    if release is None:
        release = remote.request("POST", f"{root}/releases", bearer=token, payload={"tag_name": tag,
            "target_commitish": commit, "name": manifest["version"], "draft": False, "prerelease": True,
            "make_latest": "false", "body": "Development prerelease. Full release verification remains unfinished."})
    url = _release_metadata(release, manifest, who.repo)
    actual_tag = remote.request("GET", f"{root}/git/ref/tags/{tag}", bearer=token).get("object", {})
    if actual_tag.get("type") != "commit" or actual_tag.get("sha") != commit:
        raise remote.Failed("GitHub release tag does not match the pinned source")
    existing = release.get("assets", [])
    if not isinstance(existing, list) or len(existing) > 100:
        raise remote.Failed("the existing release asset inventory is invalid")
    if any(not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in existing):
        raise remote.Failed("the existing release asset inventory is invalid")
    indexed = {row["name"]: row for row in existing}
    if len(indexed) != len(existing) or set(indexed) - set(pinned):
        raise remote.Failed("the release contains unexpected or duplicate assets")
    for name, row in indexed.items():
        asset = pinned[name]
        if (row.get("digest") != f"sha256:{asset.sha256}" or row.get("size") != len(asset.body)
                or row.get("browser_download_url") != f"https://github.com/{who.repo}/releases/download/{tag}/{name}"):
            raise remote.Failed("an existing release asset does not match its pinned digest")
    urls = []
    for name, asset in pinned.items():
        row = indexed.get(name)
        if row is None:
            row = remote.upload(who.repo, release["id"], (name, asset.body), token)
        if row.get("digest") != f"sha256:{asset.sha256}" or row.get("size") != len(asset.body):
            raise remote.Failed("an existing release asset does not match its pinned digest")
        expected = f"https://github.com/{who.repo}/releases/download/{tag}/{name}"
        if row.get("browser_download_url") != expected:
            raise remote.Failed("GitHub returned an unexpected public asset URL")
        urls.append(expected)
    return {"release_url": url, "assets": urls}
