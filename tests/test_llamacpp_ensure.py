"""`poolhouse-serve llama-cpp ensure`: update to upstream master when it is ahead and nothing is
computing. `status`, `update`, the broker's lease table and the measuring lock are stood in for at the
process boundary; no build, network or GPU is involved."""

from __future__ import annotations

import argparse
import json
import os

import pytest

from poolhouse import home
from poolhouse.serve import llamacpp_cli, llamacpp_ensure, llamacpp_state
from poolhouse.serve.build_paths import BuildFailed
from poolhouse.serve.llamacpp_update import Outcome
from poolhouse.testing.ensure_fakes import MASTER, FakeEnsure, status_answer

NEXT = "b" * 40


def held(model="/m/Qwen3.8-27B-UD-Q4_K_XL.gguf", port=8080):
    return {"servers": [{"port": port, "model": model, "holders": [{"lease": "l1"}]}], "queue": []}


def test_it_updates_to_master_when_behind_and_idle():
    fake = FakeEnsure()
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.checks == 1 and fake.updates == [MASTER]
    assert (done.state, done.code) == ("updated", 0)
    assert done.line == "updated: build 11300 -> b11359-aaaaaaaaa"


def test_it_does_nothing_when_the_build_is_current():
    fake = FakeEnsure(status_answer(newer="no"))
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.updates == [] and (done.state, done.code) == ("current", 0)
    assert done.line.startswith("current: build 11300")


def test_a_held_lease_defers_and_changes_nothing():
    fake = FakeEnsure(leases=held())
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.updates == [] and done.code == 0
    assert done.line.startswith("deferred: ") and "Qwen3.8-27B-UD-Q4_K_XL.gguf" in done.line
    assert llamacpp_state.load()["failed"] == {}


def test_a_lease_waiting_for_the_card_defers():
    fake = FakeEnsure(leases={"servers": [], "queue": [{"model": "m", "pid": 5}]})
    assert llamacpp_ensure.ensure(fake.env()).line.startswith("deferred: ")
    assert fake.updates == []


def test_a_resident_server_nobody_holds_does_not_defer():
    fake = FakeEnsure(leases={"servers": [{"port": 8080, "model": "/m/x.gguf", "holders": []}], "queue": []})
    assert llamacpp_ensure.ensure(fake.env()).state == "updated"


def test_the_measuring_lock_defers():
    fake = FakeEnsure(measuring={"pid": 4242, "argv": ["sweep", "--serve", "x"], "started": "2026-10-12T09:00:00"})
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.updates == [] and done.code == 0
    assert done.line.startswith("deferred: ") and "poolhouse-bench sweep" in done.line and "4242" in done.line


def test_a_running_queue_defers():
    fake = FakeEnsure(queue={"pid": 77, "file": "evening.queue"})
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.updates == [] and done.line.startswith("deferred: ") and "evening.queue" in done.line


def test_the_real_measuring_lock_file_and_queue_record_are_read(monkeypatch, tmp_path):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    bench = home.state("bench")
    bench.mkdir(parents=True)
    env = llamacpp_ensure.Env(status=lambda *_: status_answer(), update=lambda *a, **k: pytest.fail("updated"),
                              leases=lambda: {"servers": [], "queue": []})
    (bench / "measuring.json").write_text(json.dumps({"pid": os.getpid(), "argv": ["run"]}))
    assert "a measurement holds the card" in llamacpp_ensure.ensure(env).line
    (bench / "measuring.json").write_text(json.dumps({"pid": os.getpid(), "argv": ["run"], "ended": "now"}))
    (bench / "queue.json").write_text(json.dumps({"pid": os.getpid(), "file": "night.queue"}))
    assert "night.queue" in llamacpp_ensure.ensure(env).line
    (bench / "queue.json").write_text(json.dumps({"pid": os.getpid(), "file": "night.queue", "ended": "now"}))
    env.update = lambda ref, env, **more: Outcome("built", "b2-x", "v")
    assert llamacpp_ensure.ensure(env).state == "updated"


def test_a_pinned_build_is_left_alone_with_one_plain_line():
    fake = FakeEnsure(status_answer(pinned="b11000-abc"))
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.updates == [] and done.code == 2
    assert done.line == "pinned: the track is pinned to b11000-abc, so newer upstream builds are not used; `poolhouse-serve llama-cpp pin --off` resumes tracking"


def test_a_pinned_build_that_is_current_is_not_an_error():
    assert llamacpp_ensure.ensure(FakeEnsure(status_answer(pinned="b1-x", newer="no")).env()).code == 0


def test_offline_says_so_and_changes_nothing():
    fake = FakeEnsure(status_answer(newer="unknown (offline)", master="", tag="", upstream_note="no route to api.github.com"))
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.updates == [] and done.code == 2
    assert done.line == "offline: upstream llama.cpp could not be checked (no route to api.github.com); nothing changed"


def test_a_host_that_needs_approval_exits_three():
    got = status_answer(newer="unknown (source host not approved)", master="", tag="", upstream_note="approve github.com")
    done = llamacpp_ensure.ensure(FakeEnsure(got).env())
    assert done.code == 3 and "approve github.com" in done.line


def test_a_failed_smoke_keeps_the_active_build_and_exits_one():
    fake = FakeEnsure(outcome=Outcome("failed", "b11359-aaaaaaaaa", "health: no answer", None))
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.updates == [MASTER] and (done.state, done.code) == ("failed", 1)
    assert done.line == ("failed: b11359-aaaaaaaaa did not pass its smoke test (health: no answer); "
                         "the active build is unchanged")
    assert llamacpp_state.load()["failed"]["commit"] == MASTER


def test_a_commit_that_failed_is_not_retried_until_upstream_moves():
    llamacpp_state.record_failure(MASTER, "health: no answer", at=1.0)
    fake = FakeEnsure()
    done = llamacpp_ensure.ensure(fake.env())
    assert fake.updates == [] and (done.state, done.code) == ("skipped", 0)
    assert done.line.startswith("skipped: master aaaaaaaaa failed its smoke test") and "until upstream moves" in done.line
    fake.got = status_answer(master=NEXT)
    assert llamacpp_ensure.ensure(fake.env()).state == "updated" and fake.updates == [NEXT]


def test_a_missing_toolchain_is_an_error_not_a_recorded_failure():
    fake = FakeEnsure(outcome=BuildFailed("cmake is missing; Poolhouse installs nothing"))
    with pytest.raises(BuildFailed):
        llamacpp_ensure.ensure(fake.env())
    assert llamacpp_state.load()["failed"] == {}


def test_a_young_build_is_kept_within_max_behind_hours(tmp_path):
    fake = FakeEnsure()
    env = fake.env()
    env.age_hours = lambda: 3.0
    done = llamacpp_ensure.ensure(env, max_behind_hours=24)
    assert fake.updates == [] and done.code == 0 and done.line.startswith("current: ")
    env.age_hours = lambda: 30.0
    assert llamacpp_ensure.ensure(env, max_behind_hours=24).state == "updated"


def test_ensure_never_prunes(monkeypatch):
    monkeypatch.setattr(llamacpp_state, "remove", lambda *_: pytest.fail("pruned"))
    monkeypatch.setattr(llamacpp_state, "prune_candidates", lambda *_a, **_k: pytest.fail("pruned"))
    assert llamacpp_ensure.ensure(FakeEnsure().env()).state == "updated"


def cli(monkeypatch, fake, json=False):
    monkeypatch.setattr(llamacpp_ensure, "default_env", lambda upstream, pipeline, jobs, say=None: fake.env())
    args = argparse.Namespace(action="ensure", json=json, jobs=0, max_behind_hours=0.0)
    return llamacpp_cli.cmd_llama_cpp(args)


def test_the_command_prints_the_line_and_returns_the_exit_code(monkeypatch, capsys):
    assert cli(monkeypatch, FakeEnsure(leases=held())) == 0
    assert capsys.readouterr().out.startswith("deferred: ")
    assert cli(monkeypatch, FakeEnsure(outcome=Outcome("failed", "b1-x", "d", None))) == 1
    assert cli(monkeypatch, FakeEnsure(status_answer(pinned="b1-x"))) == 2


def test_the_command_prints_json(monkeypatch, capsys):
    assert cli(monkeypatch, FakeEnsure(), json=True) == 0
    said = json.loads(capsys.readouterr().out)
    assert said["state"] == "updated" and said["exit"] == 0 and said["line"].startswith("updated: ")


def test_a_status_check_remembers_what_upstream_said():
    llamacpp_ensure.ensure(FakeEnsure().env())
    assert llamacpp_state.load()["seen"] == {"at": 1_000_000.0, "tag": "b11359", "master": MASTER}


def test_build_tools_are_found_in_the_homebrew_directories_when_a_unit_path_lacks_them(tmp_path, monkeypatch):
    from poolhouse.serve import llamacpp_compile

    brew = tmp_path / "brew"
    brew.mkdir()
    (brew / "cmake").write_text("#!/bin/sh\n")
    (brew / "cmake").chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(llamacpp_compile, "HOMEBREW_PATH", str(tmp_path / "none"))
    assert llamacpp_compile._which("cmake") is None
    monkeypatch.setattr(llamacpp_compile, "HOMEBREW_PATH", str(brew))
    assert llamacpp_compile._which("cmake") == str(brew / "cmake")
