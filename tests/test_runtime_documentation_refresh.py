"""Automatic documentation refresh omission never asserts a new build or input equivalence."""

import argparse
import importlib
import subprocess

import pytest

from poolhouse import runtime, runtime_cli, runtime_deploy, runtime_git, runtime_store

REAL_PLANNED = runtime_cli._planned
pytest_plugins = ["node_kit"]

PROJECT = '''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "poolhouse"
version = "0.3.0-dev.1"
readme = "README.md"
license-files = ["LICENSE"]
license = "Apache-2.0"
[tool.hatch.build.targets.wheel]
packages = ["src/poolhouse"]
[tool.hatch.build.targets.wheel.force-include]
"contracts" = "poolhouse/contracts/_data"
"patches" = "poolhouse/serve/_patches"
'''


def git(repo, *words):
    result = subprocess.run(["git", "-C", str(repo), *words], text=True, capture_output=True, check=True,
                            env={**runtime_git.environment(), "GIT_AUTHOR_NAME": "Codex", "GIT_AUTHOR_EMAIL": "codex@agents.invalid",
                                 "GIT_COMMITTER_NAME": "Codex", "GIT_COMMITTER_EMAIL": "codex@agents.invalid"})
    return result.stdout.strip()


def save(repo, path, text="changed"):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "--quiet", "--message", "chore: fixture")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def source(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=dev")
    files = {"pyproject.toml": PROJECT, "app/poolhouse-node/Cargo.toml": '[package]\nname="poolhouse-node"\nversion="0.3.0"\n',
             "src/poolhouse/__init__.py": "", "README.md": "Poolhouse", "LICENSE": "fixture license",
             "contracts/node/schema.json": "{}", "patches/engine.patch": "fixture patch"}
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", *files)
    git(repo, "commit", "--quiet", "--message", "chore: fixture")
    previous = git(repo, "rev-parse", "HEAD")
    return repo, previous


def same(repo, previous, wanted):
    return importlib.import_module("poolhouse.runtime_inputs").documentation_only(repo, previous, wanted)


@pytest.mark.parametrize("path", ["docs/notes/proof.md", "docs/node-contract.md", "docs/native-plan-macos.md",
                                 "docs/native-plan-windows-linux.md"])
def test_only_named_regular_coordination_markdown_may_omit_automatic_refresh(source, path):
    repo, previous = source
    wanted = save(repo, path)
    assert same(repo, previous, wanted)


@pytest.mark.parametrize("path", ["README.md", "docs/api.md", "src/poolhouse/data/model.json", "app/Cargo.lock",
                                 "app/poolhouse-node/src/main.rs", "packaging/runtime-floor", "contracts/node/schema.json",
                                 "patches/engine.patch", "scripts/hooks/post-merge", ".gitattributes", "unknown-input"])
def test_every_other_changed_record_keeps_normal_refresh(source, path):
    repo, previous = source
    assert not same(repo, previous, save(repo, path))


@pytest.mark.parametrize("mode", ["executable", "symlink"])
def test_documentation_path_cannot_hide_executable_or_indirect_inputs(source, mode):
    repo, previous = source
    path = repo / "docs/notes/proof.md"
    path.parent.mkdir(parents=True)
    if mode == "executable":
        path.write_text("executable")
        path.chmod(0o755)
    else:
        path.symlink_to("../../README.md")
    git(repo, "add", "docs/notes/proof.md")
    git(repo, "commit", "--quiet", "--message", "chore: fixture")
    assert not same(repo, previous, git(repo, "rev-parse", "HEAD"))


@pytest.mark.parametrize("config", [PROJECT.replace('readme = "README.md"', 'readme = "docs/node-contract.md"'),
                                   PROJECT + '\n[tool.hatch.build.hooks.custom]\npath="builder.py"\n',
                                   PROJECT.replace('"contracts" =', '"docs/notes" =')])
def test_existing_build_metadata_does_not_turn_coordination_notes_into_auto_deployment(source, config):
    repo, _ = source
    previous = save(repo, "pyproject.toml", config)
    assert same(repo, previous, save(repo, "docs/notes/proof.md"))


def test_existing_custom_builder_does_not_trigger_automatic_deployment_for_notes(source):
    repo, _ = source
    previous = save(repo, "app/poolhouse-node/build.rs", 'fn main() {}')
    assert same(repo, previous, save(repo, "docs/notes/proof.md"))


@pytest.mark.parametrize("path,text", [
    ("app/poolhouse-node/Cargo.toml", '[package]\nname="poolhouse-node"\n[target.\'cfg(unix)\'.dependencies]\nlocal={path="../local"}\n'),
    ("app/poolhouse-node/Cargo.toml", '[package]\nname="poolhouse-node"\n[dependencies]\nlocal={workspace=true}\n'),
    ("app/Cargo.toml", '[workspace]\n[patch.crates-io]\nlocal={path="local"}\n'),
    ("app/poolhouse-node/Cargo.toml", '[package]\nname="poolhouse-node"\n[lib]\npath="../../docs/notes/code.md"\n'),
    ("app/poolhouse-node/Cargo.toml", '[package]\nname="poolhouse-node"\n[[bin]]\nname="poolhouse-node"\npath="../../docs/notes/code.md"\n'),
    ("pyproject.toml", PROJECT.replace('license = "Apache-2.0"', 'license = {file="docs/notes/license.md"}')),
])
def test_existing_manifest_consumers_do_not_change_documentation_notification_policy(source, path, text):
    repo, _ = source
    previous = save(repo, path, text)
    assert same(repo, previous, save(repo, "docs/notes/proof.md"))


@pytest.mark.parametrize("path,text", [
    ("app/poolhouse-node/src/lib.rs", 'const TEXT: &str = include_str!("../../../docs/notes/proof.md");'),
    ("src/poolhouse/fleet/runtime_wheel.py", 'from pathlib import Path\nTEXT=Path("docs/notes/proof.md").read_text()'),
])
def test_notes_as_build_inputs_still_require_explicit_deployment(source, path, text):
    repo, _ = source
    previous = save(repo, path, text)
    assert same(repo, previous, save(repo, "docs/notes/proof.md"))


def test_metadata_cannot_alias_excluded_documentation_through_a_symlink(source):
    repo, _ = source
    save(repo, "docs/notes/proof.md")
    (repo / "README.md").unlink()
    (repo / "README.md").symlink_to("docs/notes/proof.md")
    git(repo, "add", "README.md")
    git(repo, "commit", "--quiet", "--message", "chore: fixture")
    previous = git(repo, "rev-parse", "HEAD")
    assert not same(repo, previous, save(repo, "docs/notes/proof.md", "more documentation"))


def test_comparison_requires_full_available_ancestor_commits(source):
    repo, previous = source
    wanted = save(repo, "docs/notes/proof.md")
    assert not same(repo, previous[:12], wanted)
    assert not same(repo, "f" * 40, wanted)
    assert not same(repo, wanted, previous)


@pytest.fixture
def automatic(source, tmp_path, monkeypatch):
    repo, previous = source
    wanted = save(repo, "docs/notes/proof.md")
    chosen = runtime.Runtime(tmp_path / "installed", previous, "0.3.0-dev.1", runtime.identity())
    plan = runtime_deploy.Plan(repo, wanted, None)
    monkeypatch.setattr(runtime_cli.authority_caller, "is_live_child", lambda **kw: False)
    monkeypatch.setattr(runtime_cli, "_planned", lambda args: plan)
    monkeypatch.setattr(runtime_deploy, "settled", lambda plan: "")
    monkeypatch.setattr(runtime, "available", lambda: chosen)
    monkeypatch.setattr(runtime_store, "rejected", lambda prefix: False)
    monkeypatch.setattr(runtime_store, "epoch_of", lambda prefix: 1)
    snapshot = (str(chosen.prefix), chosen.commit, chosen.version, chosen.identity, "1")
    monkeypatch.setattr(runtime_deploy, "automatic_selection", lambda: snapshot)
    monkeypatch.setattr(runtime_cli, "_building", lambda: "")
    monkeypatch.setattr(runtime_cli, "_audited", lambda *a, **kw: None)
    starts = []
    monkeypatch.setattr(runtime_cli, "_start_background", starts.append)
    args = argparse.Namespace(hook_refresh=True, background=True, checkout=str(repo), agent="", settle=False,
                              now=False, force=False, force_build=False, allow_unmerged=False, automatic_from="",
                              ref="HEAD", timeout=1, wait=1, launchers="")
    return args, plan, chosen, starts, snapshot


def test_automatic_documentation_refresh_starts_no_worker_or_settlement_and_keeps_built_from(automatic, capsys):
    args, _plan, chosen, starts, _snapshot = automatic
    before = chosen.commit
    assert runtime_cli._ensure(args) == 0
    assert starts == [] and chosen.commit == before
    assert "automatic documentation-only refresh" in capsys.readouterr().out


def test_status_note_does_not_relabel_the_selected_commit_as_the_new_source(automatic):
    _args, plan, chosen, _starts, _snapshot = automatic
    assert "automatic documentation-only refresh" in runtime_cli._verdict(plan)
    assert chosen.commit[:7] in runtime_cli._verdict(plan)
    assert chosen.commit != plan.commit and not runtime_deploy.current(plan)


@pytest.mark.parametrize("change", ["explicit", "force", "force-build", "rejected", "floor", "selection"])
def test_explicit_or_unverified_or_changed_ownership_cannot_use_the_automatic_skip(automatic, monkeypatch, change):
    args, plan, _chosen, starts, snapshot = automatic
    if change == "explicit":
        args.hook_refresh = False
    elif change == "force":
        args.force = True
    elif change == "force-build":
        args.force_build = True
    elif change == "rejected":
        monkeypatch.setattr(runtime_store, "rejected", lambda prefix: True)
    elif change == "floor":
        object.__setattr__(plan, "floor", 2)
    else:
        seen = []
        def switched():
            seen.append(1)
            return snapshot if len(seen) == 1 else (*snapshot[:1], "e" * 40, *snapshot[2:])
        monkeypatch.setattr(runtime_deploy, "automatic_selection", switched)
    assert runtime_cli._ensure(args) == 0
    assert len(starts) == 1


def test_source_admission_still_precedes_an_automatic_noop(automatic, monkeypatch):
    args, _plan, _chosen, starts, _snapshot = automatic
    def refuse(args):
        raise runtime_deploy.DeployError("source refused")
    monkeypatch.setattr(runtime_cli, "_planned", refuse)
    with pytest.raises(runtime_deploy.DeployError, match="source refused"):
        runtime_cli._ensure(args)
    assert starts == []


@pytest.fixture
def unpublished(workspace_node, automatic, monkeypatch):
    from poolhouse import authority, node_launch, person
    from poolhouse.board import place

    args, plan, chosen, starts, snapshot = automatic
    repo = plan.checkout
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    git(repo, "update-ref", "refs/remotes/origin/dev", chosen.commit)
    monkeypatch.delenv("POOLHOUSE_BOARD", raising=False)
    workspace_node.board = place.resolve(workspace_node.client, repo)
    monkeypatch.setenv("POOLHOUSE_BOARD", workspace_node.board)
    actor = workspace_node.member("unpublished-docs-root", harness="codex")
    for name in (*person.AGENT_MARKERS, authority.AGENT_ENV, authority.SUBAGENT_ENV,
                 "POOLHOUSE_SESSION_HARNESS", "POOLHOUSE_SESSION_ID", "POOLHOUSE_WORKSPACE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", actor.session)
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "dev")
    runtime_store.write_state({"checkout": str(repo)})
    monkeypatch.setattr(runtime_cli, "_planned", REAL_PLANNED)
    return args, plan, chosen, starts, snapshot


def test_real_unpublished_documentation_hook_does_not_start_a_publication_waiter(unpublished, capsys):
    from poolhouse import runtime_source

    args, plan, chosen, starts, _snapshot = unpublished
    with pytest.raises(runtime_source.Unpublished) as refused:
        runtime_cli.plan_from(args)
    assert refused.value.checkout == plan.checkout and refused.value.commit == plan.commit
    assert runtime_cli._ensure(args) == 0
    assert starts == []
    output = capsys.readouterr().out
    assert "unpublished" in output and "no deployment" in output
    assert chosen.commit[:7] in output and chosen.commit != plan.commit


@pytest.mark.parametrize("change", ["code", "force", "force-build", "explicit", "rejected", "floor", "selection"])
def test_nonmatching_unpublished_hooks_keep_the_normal_publication_waiter(unpublished, monkeypatch, change):
    args, plan, chosen, starts, snapshot = unpublished
    if change == "code":
        save(plan.checkout, "src/poolhouse/__init__.py", "VALUE = 2")
    elif change == "force":
        args.force = True
    elif change == "force-build":
        args.force_build = True
    elif change == "explicit":
        args.hook_refresh = False
    elif change == "rejected":
        monkeypatch.setattr(runtime_store, "rejected", lambda prefix: True)
    elif change == "floor":
        save(plan.checkout, "packaging/runtime-floor", "2")
    else:
        seen = []
        def switched():
            seen.append(1)
            return snapshot if len(seen) == 1 else (*snapshot[:1], "e" * 40, *snapshot[2:])
        monkeypatch.setattr(runtime_deploy, "automatic_selection", switched)
    assert runtime_cli._ensure(args) == 0
    assert len(starts) == 1 and "--await-published" in starts[0]
    assert chosen.commit == snapshot[1]


def test_unpublished_prospective_mismatch_never_enters_deployment(unpublished, monkeypatch):
    from dataclasses import replace

    args, plan, _chosen, starts, _snapshot = unpublished
    original = runtime_cli.plan_from

    def moved(args, *, deploying=True):
        return original(args) if deploying else replace(plan, commit="e" * 40)

    monkeypatch.setattr(runtime_cli, "plan_from", moved)
    assert runtime_cli._ensure(args) == 0
    assert len(starts) == 1 and "--await-published" in starts[0]


def test_unpublished_noop_still_validates_automatic_baseline(unpublished):
    args, _plan, _chosen, starts, _snapshot = unpublished
    args.automatic_from = "{}"
    with pytest.raises(runtime_deploy.DeployError):
        runtime_cli._ensure(args)
    assert starts == []


def test_code_after_omitted_notes_is_compared_from_actual_installed_source(automatic):
    args, plan, chosen, starts, _snapshot = automatic
    assert runtime_cli._ensure(args) == 0 and starts == []
    code = save(plan.checkout, "src/poolhouse/__init__.py", "VALUE = 2")
    target = save(plan.checkout, "docs/notes/another.md")
    object.__setattr__(plan, "commit", target)
    assert chosen.commit != code and chosen.commit != target
    assert runtime_cli._ensure(args) == 0 and len(starts) == 1
    assert not runtime_deploy.current(plan)


@pytest.mark.parametrize("change", ["head-race", "epoch-race", "unhealthy", "baseline"])
def test_unstable_or_unverified_source_and_selection_keep_normal_refresh(automatic, monkeypatch, change):
    args, plan, chosen, starts, snapshot = automatic
    if change == "head-race":
        monkeypatch.setattr(runtime_deploy, "resolve_commit", lambda checkout: "e" * 40)
    elif change == "epoch-race":
        calls = []
        def moved():
            calls.append(1)
            return snapshot if len(calls) == 1 else (*snapshot[:4], "2")
        monkeypatch.setattr(runtime_deploy, "automatic_selection", moved)
    elif change == "unhealthy":
        monkeypatch.setattr(runtime_deploy, "healthy", lambda plan: None)
    else:
        object.__setattr__(plan, "automatic_from", (*snapshot[:1], "e" * 40, *snapshot[2:]))
    assert runtime_cli._ensure(args) == 0 and len(starts) == 1
    assert chosen.commit == snapshot[1]


def test_documentation_omission_preserves_unrelated_dirty_work(automatic):
    args, plan, chosen, starts, snapshot = automatic
    work = plan.checkout / "src/poolhouse/__init__.py"
    work.write_bytes(b"unfinished source work\n")
    before = git(plan.checkout, "status", "--porcelain")
    assert runtime_cli._ensure(args) == 0 and starts == []
    assert work.read_bytes() == b"unfinished source work\n"
    assert git(plan.checkout, "status", "--porcelain") == before
    assert chosen.commit == snapshot[1] and chosen.commit != plan.commit
    assert not runtime_deploy.current(plan)
