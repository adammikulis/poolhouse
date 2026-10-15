"""Authenticated fixed repository publication through the installed GitHub App holder."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
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
           "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "https", "PYTHON": sys.executable}
    for name in ("POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_HOME", "CLAUDECODE", "POOLHOUSE_AGENT"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    env["POOLHOUSE_NONINTERACTIVE"] = "1"
    return env


def local(checkout: Path, *args: str) -> str:
    """Read source Git objects without user configuration, filters or prompts."""
    done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(checkout), *args],
                          env=environment(checkout), capture_output=True, text=True, timeout=30, check=False)
    if done.returncode:
        raise remote.Failed("the source repository could not be verified")
    return done.stdout.strip()


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
    rows = local(selected.checkout, "ls-tree", "-r", built, "scripts/hooks").splitlines()
    if len(rows) > 500:
        raise remote.Failed("the installed hook inventory is too large")
    for row in rows:
        metadata, name = row.split('\t', 1)
        mode, kind, digest = metadata.split()
        relative = Path(name).relative_to("scripts/hooks")
        if kind != "blob" or mode not in {"100644", "100755"} or '..' in relative.parts:
            raise remote.Failed("installed hook files must be regular verified source blobs")
        if int(local(selected.checkout, "cat-file", "-s", digest)) > 2 << 20:
            raise remote.Failed("an installed hook is too large")
        body = local(selected.checkout, "show", f"{built}:{name}")
        target = hooks / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body + '\n')
        target.chmod(0o700 if mode == "100755" else 0o600)
    return hooks


def _view(selected: Source, directory: Path) -> None:
    local(directory, "init", "--quiet", f"--initial-branch={selected.branch}")
    objects = Path(local(selected.checkout, "rev-parse", "--path-format=absolute", "--git-path", "objects"))
    (directory / ".git/objects/info/alternates").write_text(str(objects) + '\n')
    local(directory, "update-ref", f"refs/heads/{selected.branch}", selected.commit)


def push_development(checkout: Path) -> dict[str, str]:
    """Push one exact development tip through the installed repository App."""
    authorize("push the exact reviewed development tip")
    who = identity()
    selected = source(checkout, who.repo)
    authority.require("github.release", json.dumps({"operation": "push-development", "repo": who.repo,
                                                   "branch": selected.branch, "sha": selected.commit}, sort_keys=True))
    path = f"/repos/{who.repo}/git/ref/heads/{selected.branch}"
    before = remote.request("GET", path)["object"]["sha"]
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
        if local(checkout, "rev-parse", "HEAD") != selected.commit:
            raise remote.Failed("the selected development tip changed before publication")
        latest = remote.request("GET", path)["object"]["sha"]
        if latest != before:
            raise remote.Failed("the remote development tip changed before publication")
        token = github_app.mint(who, PERMISSIONS, who.repo)["token"]
        remote.push(remote.GitPush(who.repo, selected.branch, selected.commit, token), directory, env)
    after = remote.request("GET", path, bearer=token)["object"]["sha"]
    if after != selected.commit:
        raise remote.Failed("GitHub did not report the requested development tip")
    return {"sha": selected.commit}
