"""The red-team lab attacks with forged traffic, plants decoys and rotates keys: none of that may reach the real home."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from poolhouse import home
from poolhouse.redteam import run
from poolhouse.redteam.lab import lab, own_home
from poolhouse.redteam.scenarios import Options

pytest_plugins = ["serve_node", "real_model"]


def test_the_lab_runs_in_its_own_home_and_gives_the_old_one_back(tmp_path, monkeypatch):
    mine = tmp_path / "mine"
    monkeypatch.setenv(home.ROOT_ENV, str(mine))
    with lab() as made:
        inside = home.home()
        assert inside != mine and made.root in inside.parents
    assert home.home() == mine and os.environ[home.ROOT_ENV] == str(mine)


def test_own_home_removes_the_variable_it_set_when_there_was_none(tmp_path, monkeypatch):
    monkeypatch.delenv(home.ROOT_ENV, raising=False)
    with own_home(tmp_path / "x") as inside:
        assert os.environ[home.ROOT_ENV] == str(inside)
    assert home.ROOT_ENV not in os.environ


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Every path under ``root`` (a link is one entry, never followed) with its size and modification time."""
    seen: dict[str, tuple[int, int]] = {}
    for where, dirs, files in os.walk(root):
        for name in [*dirs, *files]:
            info = (Path(where) / name).lstat()
            seen[str(Path(where, name).relative_to(root))] = (info.st_size, info.st_mtime_ns)
    return seen


@pytest.mark.slow
@pytest.mark.redteam
@pytest.mark.timeout(1800)
def test_a_run_on_a_real_model_writes_nothing_to_the_home_it_started_in_once_the_lease_is_up(
        tool_model, monkeypatch):
    """The lease is the run's own and writes where it must (its server's log grows while it answers), so the home
    is read the moment the lease is up and again when the scenarios are done: the forged requests, the decoys, the
    quarantines and the rotated keys the scenarios and the daemon they attack make all land in the home `execute`
    swaps in, whatever the order of the swap and the daemon's start."""
    start = home.home()
    seen: dict[str, dict[str, tuple[int, int]]] = {}
    served: list[int] = []
    leased, scenarios = run.serve, run._scenarios

    @contextmanager
    def watched_lease(*args, **kwargs):
        with leased(*args, **kwargs) as info:
            seen["lease up"] = snapshot(start)
            served.append(info.port)
            yield info

    async def audited(plan, one, report):
        await scenarios(plan, one, report)
        seen["scenarios done"] = snapshot(start)

    monkeypatch.setattr(run, "serve", watched_lease)
    monkeypatch.setattr(run, "_scenarios", audited)
    report = run.execute(run.Plan(("fleet", "sentinel"), str(tool_model), Options(limit=1)))
    assert {a.target for a in report.attempts} >= {"fleet", "sentinel"} and report.meta["model"] == tool_model.name
    before, after = seen["lease up"], seen["scenarios done"]
    own_log = f"logs/llama-server-{served[0]}-"
    changed = sorted(name for name in before.keys() | after.keys()
                     if before.get(name) != after.get(name) and not name.startswith(own_log))
    assert not changed, f"the scenarios wrote under the home the run started in: {changed[:12]}"
