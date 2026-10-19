"""Which landings need an independent review: a positive list of security-adjacent paths, and a path nobody classified fails closed."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import land_git as lg

from poolhouse import protectedwrites

SENSITIVE_ROOTS = frozenset({
    ".agents", ".claude", ".github", ".gitattributes", ".gitignore", ".release-please-manifest.json", "AGENTS.md", "CLAUDE.md",
    "SECURITY.md", "app", "budgets.json", "packaging", "patches", "pyproject.toml", "release-please-config.json", "version.txt"})
ORDINARY_ROOTS = frozenset({
    ".python-version", "CHANGELOG.md", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "HANDOFF.md", "LICENSE", "NOTICE", "README.md",
    "THIRD_PARTY_NOTICES.md", ".task-report.md", ".task.patch"})
SPLIT_ROOTS = frozenset({"contracts", "docs", "scripts", "src", "tests"})
SRC_ORDINARY = frozenset({
    "__init__.py", "asking.py", "bench", "briefing.py", "checks.py", "cli", "command.py", "command_capture.py", "data",
    "datasheet", "decide", "decide_cli.py", "deciders.py", "doctor.py", "draft", "entities", "errors.py", "extraction.py",
    "features_cli.py", "files.py", "files_windows.py", "geo.py", "gguf", "graph", "gym", "ingest", "installed.py",
    "json_objects.py", "jsonl", "kvtype.py", "log.py", "markup.py", "media", "messages.py", "migrate_paths.py",
    "migrate_scan.py", "py.typed", "records.py", "sources", "spec", "speech", "subscribers.py", "surface", "telemetry.py",
    "testing", "tool_schema.py", "train", "ui", "units.py", "vision", "walk", "world"})
SRC_SENSITIVE = frozenset({
    "activity", "agent", "agent_dependency.py", "agent_hooks.py", "agent_hooks_stale.py", "api", "authority.py", "backend",
    "board", "board_names.py", "build_update.py", "chat.py", "chatpolicy.py", "claude.py", "client", "codex.py", "coding.py",
    "contracts", "coordination", "credentials", "desktop.py", "devbranch.py", "device_host.py", "device_setup.py", "do.py",
    "features.py", "fleet", "gate.py", "gitstate.py", "guard", "harness.py", "harness_claims.py", "harnesshook.py",
    "harnessid.py", "harnessing.py", "harnesspolicy.py", "home.py", "hook_bootstrap.py", "hook_diagnostics.py", "hookcheck.py",
    "http.py", "http_cancel.py", "httpguard.py", "hub", "inbox", "interventions.py", "jobs.py", "keystore.py",
    "keystore_guard.py", "lan.py", "leasetable.py", "legacy.py", "limits.py", "lock.py", "macauth.py", "mcp.py", "memory",
    "migrate.py", "net", "node_app.py", "node_binary.py", "node_build.py", "node_health.py", "node_join.py",
    "node_join_check.py", "node_key.py", "node_launch.py", "node_permission.py", "node_pool.py", "node_signing.py",
    "node_supervise.py", "optional.py", "paths.py", "person.py", "pi.py", "platform.py", "private_counters.py", "private_path.py",
    "protectedwrites.py", "real_machine.py", "redact", "redteam", "reputation", "requests", "roles.py", "rules.py",
    "runtime.py", "runtime_await.py", "runtime_board.py", "runtime_bridges.py", "runtime_cli.py", "runtime_coalesce.py",
    "runtime_deploy.py", "runtime_git.py", "runtime_host.py", "runtime_launchers.py", "runtime_source.py", "runtime_stale.py", "runtime_store.py",
    "runtime_trust.py", "runtime_unmerged.py", "safenames.py", "safetext.py", "sandbox", "scrape", "sealing.py",
    "secretpaths.py", "sentinel", "serve", "serverkeys.py", "setup.py", "taint", "tar_libraries.py", "testfarm", "trees.py",
    "web.py", "win32.py", "windows_private.py", "workspace", "worktreerules.py"})
SRC_SENSITIVE_UNDER = ("cli/daemon.py", "cli/wsl_daemon.py", "data/profiles.json", "testing/canary.py", "testing/fakegithub.py",
                       "testing/injection_corpus.py", "testing/live.py")
AREA = re.compile("keystore|keyring|keychain|crypt|vault|secret|credential|grant|authority|lease|broker|guard|redteam|sentinel|"
                  "sandbox|admission|identity|sealing|injection|real_machine|token|verif|"
                  "(?<![a-z])(sign(ing|ed|ature|atures)?|serv(e|ers?|ing))(?![a-z])")
SCRIPTS_ORDINARY = frozenset({
    "__pycache__", "commit_types_chart.py", "compare-harnesses", "evaluate-jolt.mjs", "experiments", "frontend-assets.mjs",
    "nomenclature.py", "notices.py", "notices_static.md", "prepare_medmcqa.py", "rename_to_poolhouse.py"})
CONTRACTS_ORDINARY = frozenset({
    "extraction-document.schema.json", "extraction.schema.json", "glossary-allowlist.toml", "glossary-rules.toml", "grammars",
    "phone-friction.json", "recipes", "sampling.schema.json", "vague-predicates.json"})
POLICY = {"agents.md", "claude.md", "skill.md", "security.md"}
SENSITIVE_DOCS = ("agent", "security", "keystore", "landing", "node", "phone", "pool", "install", "workspace", "person", "compromis",
                  "sentinel", "service", "gates", "test-execution")


def sensitive(name: str) -> bool:
    """Whether the changed path ``name`` is security-adjacent, or in an area nobody classified."""
    path = PurePosixPath(name)
    parts = path.parts
    if protectedwrites.touches_guards([name]) or path.name.casefold() in POLICY:
        return True
    top = parts[0]
    if top in SENSITIVE_ROOTS:
        return True
    if top in ORDINARY_ROOTS:
        return False
    if top not in SPLIT_ROOTS:
        return True
    if top == "src":
        if parts[:2] != ("src", "poolhouse") or len(parts) < 3 or parts[2] not in SRC_ORDINARY:
            return True
        inside = "/".join(parts[2:])
        return bool(AREA.search(inside.casefold())) or any(inside == p or inside.startswith(p + "/") for p in SRC_SENSITIVE_UNDER)
    if top == "scripts":
        return len(parts) < 2 or parts[1] not in SCRIPTS_ORDINARY
    if top == "tests":
        return (name in {"tests/conftest.py", "tests/known-fixtures.txt", "tests/land_support.py", "tests/test_pushed_review.py"}
                or path.name.startswith("test_land") or bool(AREA.search(path.name.casefold())))
    if top == "contracts":
        return len(parts) < 2 or parts[1] not in CONTRACTS_ORDINARY
    return path.suffix != ".md" or path.name.casefold().startswith(SENSITIVE_DOCS)


def fork_point(root: Path, upstream: str, tip: str) -> str:
    """The commit where ``tip`` leaves ``upstream``, or an empty string when they share none."""
    done = lg.sealed(root, "merge-base", upstream, tip, check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def merged_parents(root: Path, tip: str) -> tuple[str, str]:
    """The two parents of ``tip`` when it is a merge commit whose tree is exactly the conflict-free automatic merge of them, else two empty strings."""
    parents = lg.sealed(root, "rev-list", "--parents", "-n", "1", tip).stdout.split()[1:]
    if len(parents) != 2:
        return "", ""
    try:
        tree, _ = lg.merge_tree(root, parents[0], parents[1])
    except RuntimeError:
        return "", ""
    return (parents[0], parents[1]) if tree and tree == lg.tree_id(root, tip) else ("", "")


def required(root: Path, base: str, tip: str) -> tuple[bool, str, str]:
    """Whether the diff from ``base`` to ``tip`` needs an independent review, the commit it starts from and the first sensitive path.

    ``base`` is the id of the commit the diff starts from; anything else, or a commit this checkout lacks, makes every file of ``tip`` part of it.
    """
    start = lg.commit_id(root, base) or lg.empty_tree(root)
    flags = ("-r", "--raw", "--no-abbrev", "--no-renames", "--ignore-submodules=none", "-z")
    raw = lg.sealed(root, "diff-tree", *flags, start, tip).stdout.split("\0")
    for i in range(0, len(raw) - 1, 2):
        fields = raw[i].split()
        name = raw[i + 1]
        if len(fields) < 5:
            raise ValueError("the pinned diff has malformed file modes")
        before, after, status = fields[0].removeprefix(":"), fields[1], fields[-1]
        odd = {"120000", "100755"} & {before, after} or (before != after and "000000" not in (before, after))
        if odd or sensitive(name) or (status == "D" and name.startswith("tests/")):
            return True, start, name
    return False, start, ""
