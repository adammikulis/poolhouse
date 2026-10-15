"""Fixed-origin GitHub App and prerelease requests."""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import os
import re
import selectors
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import httpguard, platform
from poolhouse.net.darwin_group import quiescent

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"
REPOSITORY = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
PATH = re.compile(rf"(?:/app/installations/[1-9][0-9]*/access_tokens|/repos/{REPOSITORY}/(?:installation|commits/[0-9a-f]{{40}}|git/ref/(?:heads|tags)/[A-Za-z0-9_.%/-]+|releases(?:/tags/[A-Za-z0-9_.%-]+|/[1-9][0-9]*(?:/assets)?)?))")


class Failed(RuntimeError):
    """A fixed GitHub operation was refused or failed."""


class Gone(Failed):
    """The requested GitHub object does not exist."""


def _fetch(url: str, method: str, body: bytes | None, headers: dict[str, str]) -> Any:
    def exact(destination: str) -> None:
        if destination != url:
            raise Failed("GitHub publication cannot redirect")

    limits = httpguard.Limits(max_bytes=8 << 20, timeout=30, deadline_s=120,
                              vet=exact, follow_redirects=False)
    try:
        reply = httpguard.fetch(url, method=method, data=body, headers=headers, limits=limits)
    except (OSError, ValueError, http.client.HTTPException):
        raise Failed("GitHub transport failed") from None
    if reply.status == 404:
        raise Gone("GitHub object not found")
    if not 200 <= reply.status < 300:
        raise Failed(f"GitHub answered {reply.status}")
    try:
        return json.loads(reply.body) if reply.body else {}
    except (ValueError, UnicodeError):
        raise Failed("GitHub returned an invalid response") from None


def request(method: str, path: str, *, bearer: str = "", payload: dict | None = None,
            hint: str = "") -> Any:
    """Perform one supported operation at the fixed GitHub API origin."""
    if method not in {"GET", "POST"} or not PATH.fullmatch(path) or '..' in path or '//' in path or '%' in path:
        raise Failed("unsupported GitHub publication operation")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}
    reply = _fetch(API + path, method, json.dumps(payload).encode() if payload is not None else None, headers)
    if not isinstance(reply, dict):
        raise Failed("GitHub returned an invalid object")
    return reply


def upload(repo: str, release_id: int, asset: tuple[str, bytes], bearer: str) -> Any:
    """Upload pinned asset bytes to one release at GitHub's fixed upload origin."""
    name, body = asset
    if (not re.fullmatch(REPOSITORY, repo) or type(release_id) is not int or release_id <= 0
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or len(body) > 512 << 20):
        raise Failed("invalid GitHub release asset")
    url = f"{UPLOAD}/repos/{repo}/releases/{release_id}/assets?name={urllib.parse.quote(name)}"
    return _fetch(url, "POST", body, {"Authorization": f"Bearer {bearer}",
                                     "Content-Type": "application/octet-stream"})


@dataclass(frozen=True)
class GitPush:
    repo: str
    branch: str
    commit: str
    token: str = field(repr=False)


def push(plan: GitPush, directory: Path, env: dict[str, str]) -> None:
    """Publish one exact development ref through a fixed authenticated HTTPS transport."""
    if (not re.fullmatch(REPOSITORY, plan.repo) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", plan.branch)
            or plan.branch in {"main", "master"} or not re.fullmatch(r"[0-9a-f]{40}", plan.commit)):
        raise Failed("invalid development push destination")
    hooks = directory / "no-transport-hooks"
    hooks.mkdir()
    credential = base64.b64encode(f"x-access-token:{plan.token}".encode()).decode()
    options = {"credential.helper": "", "credential.interactive": "never", "core.askPass": "",
               "core.hooksPath": str(hooks), "http.followRedirects": "false", "http.proxy": "",
               "http.https://github.com/.extraHeader": f"Authorization: Basic {credential}",
               "push.followTags": "false", "submodule.recurse": "false"}
    child = dict(env)
    child["GIT_CONFIG_COUNT"] = str(len(options))
    for index, (key, value) in enumerate(options.items()):
        child[f"GIT_CONFIG_KEY_{index}"] = key
        child[f"GIT_CONFIG_VALUE_{index}"] = value
    if any(not hasattr(os, name) for name in ("waitid", "WNOWAIT", "P_PID", "WEXITED", "WNOHANG")):
        raise Failed("this runtime cannot safely supervise the Git transport")
    process = None
    try:
        process = platform.start_process(["git", "push", "--no-follow-tags", f"https://github.com/{plan.repo}.git",
                                          f"{plan.commit}:refs/heads/{plan.branch}"], cwd=directory,
                                         env=child, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if _exchange(process):
            raise Failed("the repository App development push failed")
    except (OSError, subprocess.TimeoutExpired):
        raise Failed("the repository App development transport failed") from None
    finally:
        pending = sys.exception()
        if process is not None:
            try:
                _reap(process)
            except (OSError, subprocess.TimeoutExpired):
                if pending is None:
                    raise Failed("the owned Git transport cleanup is unconfirmed") from None
                pending.add_note("the owned Git transport cleanup is unconfirmed")


def _exchange(process, timeout: float = 180) -> int:
    deadline, count = time.monotonic() + timeout, 0
    with selectors.DefaultSelector() as ready:
        for stream in (process.stdout, process.stderr):
            ready.register(stream, selectors.EVENT_READ)
        while True:
            ended = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if ended is not None and not ready.get_map():
                return ended.si_status if ended.si_code == os.CLD_EXITED else -ended.si_status
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("fixed Git publication", timeout)
            for event, _ in ready.select(min(0.1, max(0, deadline - time.monotonic()))):
                body = os.read(event.fileobj.fileno(), 64 << 10)
                count += len(body)
                if count > 1 << 20:
                    raise Failed("the Git transport output exceeded its bound")
                if not body:
                    ready.unregister(event.fileobj)


def _reap(process) -> None:
    try:
        if sys.platform == 'darwin':
            _darwin_reap(process)
            return
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        with contextlib.suppress(ProcessLookupError):
            platform.terminate_process_group(process)
        time.sleep(0.1)
        with contextlib.suppress(ProcessLookupError):
            platform.terminate_process_group(process, force=True)
        process.wait(timeout=5)
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _owned_signal(process, *, force=False) -> None:
    held = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if held is not None and held.si_pid != process.pid:
        raise OSError('the owned transport leader could not be verified')
    with contextlib.suppress(ProcessLookupError):
        if force:
            platform.terminate_process_group(process, force=True)
        else:
            platform.terminate_process_group(process)


def _darwin_reap(process) -> None:
    failures = []
    for force in (False, True):
        stopped = False
        try:
            stopped = quiescent(process)
        except OSError as error:
            failures.append(error)
        if not stopped:
            try:
                _owned_signal(process, force=force)
            except OSError as error:
                failures.append(error)
        if not force:
            time.sleep(0.1)
    deadline = time.monotonic() + 5
    try:
        while not quiescent(process):
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('owned Git transport cleanup', 5)
            time.sleep(0.01)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as error:
        failures.append(error)
    if failures:
        raise failures[0]
