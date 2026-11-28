"""Which review a change needs, from the files it changes: the one path map. A change needs an attack review when a path it changes
belongs to an area of `AREAS`, is a guard, authority or red-team test, holds code of a kind `CONTENT` names, or is named by nothing
in `LIGHT`; every other change takes the lighter review. `scripts/lead-review` and the merge guard (`release_check`) ask here.

Commits and files are read from the git objects as stored. The standing of this checkout (replace refs, grafts, attributes, the
working tree) changes no answer.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from poolhouse import worktreerules

__all__ = ["AREAS", "CONTENT", "LIGHT", "MARKER", "Unreadable", "area", "attack_reasons", "by_path", "commits", "fetched",
           "tiered", "weakened"]

TIMEOUT_S = 15
"""As `release_check.TIMEOUT_S`: one git command of the merge guard, inside the guard's own limit (`worktreerules.release_refusal`)."""
MARKER = "scripts/hooks/primary-only"
"""The file whose presence on the base branch says the repository asks for these reviews."""
HOOK = Path(__file__).resolve().parents[3] / "scripts" / "hooks" / "weakened-assertions"
"""The one definition of a guard, authority or red-team test and of what weakens one, in the source tree of whoever judges."""
UNCLASSIFIED = "not named as a lighter review"

AREAS: dict[str, tuple[str, ...]] = {
    "merge machinery": (
        r"scripts/(lead-review|review-report|dev-branch-rule|github-protection(-apply)?|release-key|primary_sync\.py|worktrees)",
        r"scripts/sealed_git[^/]*\.py", r"src/poolhouse/devbranch\.py",
        r"src/poolhouse/workspace/(landing(_cli)?|release_check|review_depth|merge_review)\.py", r"app/poolhouse-node/src/landing\.rs",
        r"\.github/.*"),
    "guards and hooks": (
        r"scripts/(hooks|gates)/.*", r"scripts/(budgets|install-hooks\.(py|sh)|githooks_ready\.py|prose_enforce\.py)",
        r"\.(claude|codex|agents)/.*", r"docs/.*\.patch", r"src/poolhouse/guard/.*",
        r"src/poolhouse/(protectedwrites|worktreerules|guardpaths|guardgit|caller|agent_hooks|delegate_guard)\.py",
        r"src/poolhouse/(harness|hook)[^/]*\.py", r"src/poolhouse/decide/guards/.*", r"src/poolhouse/(graph|train)/guard\.py",
        r"tests/(conftest|walkbound|state_attribution|foreign_writers|github_support)\.py", r"tests/known-fixtures\.txt"),
    "tests of guards, access and isolation": (
        r"tests/(.*/)?[^/]*(redteam|red_team|guard|auth|sandbox|isolat|confine|keystore|credential|secret|permission|protected"
        r"|grant|trust|release_check|lead_review|merge_checks|review_depth|attack|worktree|landing|hook|coverage-map)[^/]*(/.*)?",),
    "sandbox and isolation": (
        r"src/poolhouse/(sandbox|isolation)/.*", r"src/poolhouse/([^/]*confined[^/]*|safelaunch|real_machine)\.py",
        r"src/poolhouse/(agent|serve)/confined\.py", r"scripts/(agent-sandbox|agent_sandbox[^/]*|testwritedeny\.py)",
        r"scripts/testkit/.*"),
    "access control": (
        r"src/poolhouse/(authority|session_identity|roles|chatpolicy|coding_policy)\.py", r"src/poolhouse/graph/access\.py",
        r"src/poolhouse/testing/(identity|live)\.py", r"app/poolhouse-authority/.*",
        r"app/poolhouse-node/src/(grants|gov|membership|access)[^/]*(/.*)?"),
    "network listeners and proxies": (
        r"src/poolhouse/(net|serve|fleet)/.*", r"src/poolhouse/(http|httpguard|lan|loopback_window)\.py",
        r"src/poolhouse/graph/serve\.py", r"contracts/egress-hosts\.json"),
    "keys and credentials": (
        r"src/poolhouse/credentials/.*",
        r"src/poolhouse/(keystore[^/]*|serverkeys|secretpaths|sealing|kdf|macauth|node_key|node_signing)\.py"),
    "installation and provisioning": (
        r"src/poolhouse/(provision|pinned|device_setup|setup|opencode_install|codex_install|installed|node_build|build_update)\.py",
        r"src/poolhouse/runtime[^/]*\.py", r"src/poolhouse/requirements\.lock", r"packaging/.*", r"patches/.*", r"pyproject\.toml",
        r"scripts/(setup-wsl-dev|lock-requirements|test-on-linux-setup)"),
    "file or branch deletion": (r"src/poolhouse/(maintenance[^/]*|fleet/uninstall)\.py", r"scripts/(testreap|queue_doctor)\.py"),
}
"""The areas whose changes are attack reviews whatever else names them, as regular expressions over the whole path (any case)."""

CONTENT: dict[str, str] = {
    "file or branch deletion": (
        r"rmtree\(|\bos\.(remove|unlink|rmdir|removedirs)\(|\.unlink\(|\.rmdir\(|\bremove_(file|dir|dir_all)\(|\b(rm|unlink|rmdir)Sync\("
        r"|\brm\s+-[a-zA-Z]*[rf]|\bbranch[\"', ]+-[dD]\b|\bworktree[\"', ]+remove\b|\bRemove-Item\b|\bDETACH\s+DELETE\b|\bDROP\s+TABLE\b"),
    "network listeners and proxies": (
        r"\.listen\(|serve_forever|HTTPServer\(|create_server\(|start_(unix_)?server\(|\b(Tcp|Unix|Udp)(Listener|Socket)::bind"
        r"|createServer\(|\.bind\(\("),
    "keys and credentials": (
        r"\bkeyring\.|\bpoolhouse\.(keystore|credentials)\b|\bfrom poolhouse import [^\n]*\b(keystore|credentials)\b|\bprivate_key\b"
        r"|PRIVATE KEY|\bSecItem[A-Z]|[\"']Authorization[\"']"),
}
"""What a changed file holds, before or after, that makes its change an attack review wherever the file lies. Tests and documents are
not read for it."""

LIGHT: tuple[str, ...] = (
    r"docs/.*\.(md|png|jpe?g|svg|gif|webp|json|txt)",
    r"(AGENTS|CLAUDE|README|CHANGELOG|CONTRIBUTING|CODE_OF_CONDUCT|HANDOFF|SECURITY|THIRD_PARTY_NOTICES)\.md", r"LICENSE", r"NOTICE",
    r"budgets\.json", r"examples/.*", r"tests/.*", r"contracts/(grammars|recipes)/.*",
    r"contracts/(model_tiers|vague-predicates|name-shapes|phone-friction)\.json", r"contracts/[a-z-]+(\.[a-z-]+)*\.schema\.json",
    r"contracts/glossary-(allowlist|rules)\.toml",
    r"src/poolhouse/(backend|bench|data|decide|draft|entities|gguf|graph|jsonl|memory|spec|speech|testing|train|ui|vision|walk|world)/.*",
    r"src/poolhouse/(decide_cli|deciders|errors|extraction|features|features_cli|geo|json_objects|kvtype|lazy|limits|log|markup"
    r"|records|tolerant|tool_schema|units)\.py",
)
"""The paths whose changes take the lighter review when no area, test rule or content names them, as regular expressions (this case)."""

PROSE = (".md", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp", ".json", ".toml", ".txt", ".lock", ".npy")


class Unreadable(RuntimeError):
    """Git could not say what a change holds."""


class Change(NamedTuple):
    """One path a change touches: its modes and blob ids before and after (all zeros for none)."""

    path: str
    old_mode: str
    new_mode: str
    old: str
    new: str


def area(path: str) -> str:
    """The area of `AREAS` that holds ``path``, or an empty string."""
    return next((name for name, patterns in AREAS.items() if any(re.fullmatch(p, path, re.I) for p in patterns)), "")


def by_path(path: str) -> str:
    """Why the path map alone makes a change of ``path`` an attack review, empty when it takes the lighter one."""
    return area(path) or ("" if any(re.fullmatch(p, path) for p in LIGHT) else UNCLASSIFIED)


def _git(root: str | Path, *words: str, given: bytes | None = None, stored: bool = True, ok: tuple[int, ...] = (0,)) -> bytes:
    """The output of ``git words`` in ``root``; ``stored`` reads objects with no replace ref, graft or user setting."""
    moved = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES")
    env = {k: v for k, v in os.environ.items() if k not in moved}
    if stored:
        env |= {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1"}
    try:
        done = subprocess.run(["git", "-C", str(root), *words], input=given, capture_output=True, timeout=TIMEOUT_S, check=False,
                              env=env)
    except (OSError, subprocess.SubprocessError) as error:
        raise Unreadable(f"git {words[0]} did not run ({type(error).__name__})") from None
    if done.returncode not in ok:
        why = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Unreadable(f"git {words[0]} failed: {why[-1] if why else done.returncode}")
    return done.stdout


def _text(root: str | Path, *words: str) -> str:
    return _git(root, *words).decode("utf-8", "surrogateescape").strip()


def tiered(root: str | Path, *revisions: str) -> bool:
    """Whether the repository at ``root`` asks for review tiers: it is an enforced checkout (`worktreerules.in_poolhouse`), or a
    commit ``revisions`` name carries `MARKER`."""
    if worktreerules.in_poolhouse(root):
        return True
    for revision in revisions:
        try:
            _git(root, "cat-file", "-e", f"{revision}:{MARKER}")
        except Unreadable:
            continue
        return True
    return False


def fetched(root: str | Path, number: str, base: str, head: str) -> str:
    """Origin's ``base``, fetched now, as a commit id; ``head`` of pull request ``number`` is in the repository afterwards."""
    tracked = f"refs/remotes/origin/{base}"
    # into the branch's own ref: FETCH_HEAD is one file that any other fetch in this checkout rewrites
    _git(root, "fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/{base}:{tracked}", stored=False)
    held = _text(root, "rev-parse", "--verify", f"{tracked}^{{commit}}")
    have = ("cat-file", "-e", f"{head}^{{commit}}")
    try:
        _git(root, *have)
    except Unreadable:
        _git(root, "fetch", "--quiet", "--no-tags", "origin", f"refs/pull/{number}/head", stored=False)
        try:
            _git(root, *have)
        except Unreadable:
            raise Unreadable(f"the commits of pull request {number} cannot be read: {head} is not in this repository") from None
    return held


def commits(root: str | Path, held: str, head: str) -> list[str]:
    """The commits that merging ``head`` would add to ``held``: ``head`` first."""
    found = _text(root, "rev-list", head, f"^{held}").split()
    return [head, *(sha for sha in found if sha != head)]


def _changes(root: str | Path, base: str, head: str) -> Iterator[Change]:
    """Every path that differs between a merge base of ``base`` and ``head`` and ``head``, a rename as its removal and addition."""
    for fork in _text(root, "merge-base", "--all", base, head).split():
        raw = _git(root, "diff-tree", "-r", "-z", "--raw", "--no-renames", "--no-abbrev", "--ignore-submodules=none", fork, head)
        fields = raw.split(b"\0")
        for about, name in zip(fields[:-1:2], fields[1::2], strict=True):
            old_mode, new_mode, old, new, _ = about.decode("ascii").lstrip(":").split(" ")
            yield Change(name.decode("utf-8", "surrogateescape"), old_mode, new_mode, old, new)


def _blobs(root: str | Path, ids: list[str]) -> dict[str, str]:
    """The text of each blob of ``ids``."""
    if not ids:
        return {}
    out = _git(root, "cat-file", "--batch", given="".join(f"{one}\n" for one in ids).encode())
    found, at = {}, 0
    for one in ids:
        end = out.index(b"\n", at)
        words = out[at:end].split()
        if len(words) != 3 or words[1] != b"blob":
            raise Unreadable(f"the file {one} cannot be read")
        size = int(words[2])
        found[one] = out[end + 1:end + 1 + size].decode("utf-8", "replace")
        at = end + size + 2
    return found


def _hook():
    """The `weakened-assertions` hook of the judging source tree as a module, or None where it has none."""
    if not HOOK.is_file():
        return None
    loader = importlib.machinery.SourceFileLoader("_poolhouse_weakened_assertions", str(HOOK))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    kept = list(sys.path)
    try:
        loader.exec_module(module)
    finally:
        sys.path[:] = kept
    return module


def _judged_by_content(change: Change, texts: list[str], hook) -> str:
    """The reason the files themselves make ``change`` an attack review, or an empty string."""
    if change.path.startswith("tests/"):
        if change.path.endswith(".py") and (hook is None or any(hook.guarded(change.path, text) for text in (texts or [""]))):
            return "guard, authority or red-team test"
        return ""
    if change.path.lower().endswith(PROSE):
        return ""
    return next((name for name, pattern in CONTENT.items() if any(re.search(pattern, text) for text in texts)), "")


def attack_reasons(root: str | Path, base: str, head: str) -> list[str]:
    """``reason: path`` for each path that makes merging ``head`` into ``base`` an attack review; empty for a lighter review."""
    found: dict[str, str] = {}
    unread: list[Change] = []
    for change in _changes(root, base, head):
        links = {"120000", "160000"} & {change.old_mode, change.new_mode}
        if why := "a link or submodule" if links else by_path(change.path):
            found[change.path] = why
        else:
            unread.append(change)
    ids = {one for change in unread for one in (change.old, change.new) if one.strip("0")}
    texts = _blobs(root, sorted(ids))
    hook = _hook() if unread else None
    for change in unread:
        if why := _judged_by_content(change, [texts[one] for one in (change.old, change.new) if one in texts], hook):
            found[change.path] = why
    return sorted(f"{why}: {path}" for path, why in found.items())


def weakened(root: str | Path, base: str, head: str) -> list[str]:
    """What `weakened-assertions` reads as weaker in the guard, authority and red-team tests that merging ``head`` would change."""
    hook = _hook()
    if hook is None:
        return ["the weakened-assertions hook is not in this source tree, so nothing was compared"]
    out: list[str] = []
    for fork in _text(root, "merge-base", "--all", base, head).split():
        diff = _git(root, *hook.DIFF, fork, head, "--", "tests").decode("utf-8", "replace")

        def before(path: str, fork: str = fork) -> str:
            return _git(root, "show", f"{fork}:{path}", ok=(0, 128)).decode("utf-8", "replace")

        out.extend(one for one in hook.weakened(diff, before) if one not in out)
    return out
