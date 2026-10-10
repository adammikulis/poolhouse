"""A device on an older build than its pool updates itself at a safe boundary, never mid-lease, and a pinned one is left alone."""

import subprocess
from pathlib import Path

import pytest

from poolhouse import build_update, node_pool, person, runtime_store
from poolhouse.build_update import Steps

OLD, NEW = "a" * 40, "b" * 40
AUDIT: list = []


def member(seq, *, name="peer", state="newer", fix="", me=False):
    return {"fingerprint": name * 20, "name": name, "status": "active", "self": me, "build": "b" * 7, "build_seq": seq,
            "build_state": state, "fix": fix, "wire": 1}


def pool(mine=5, *, theirs=9, held=0, queued=0, wire_fix=""):
    return {"build": {"build": OLD, "build_seq": mine, "wire": 1, "state": "catching up" if theirs > mine else "current"},
            "leases": {"held": held, "queued": queued},
            "members": [member(mine, name="me", state="self", me=True), member(theirs, fix=wire_fix)]}


class Calls:
    """Stub steps over a selection that the stubbed ensure moves, like the real one does."""

    def __init__(self, *, ensure_code=0, switches=True, clock=1000.0):
        self.selected, self.log, self.clock = OLD, [], clock
        self.ensure_code, self.switches = ensure_code, switches

    def steps(self):
        def fetch(checkout):
            self.log.append("fetch")
            return 0, "Fast-forward"

        def ensure(checkout):
            self.log.append("ensure")
            if self.switches:
                self.selected = NEW
            return self.ensure_code, "built" if not self.ensure_code else "health check failed"

        def rollback(commit):
            self.log.append(f"rollback {commit[:1]}")
            self.selected = commit
            return 0, ""

        return Steps(fetch=fetch, ensure=ensure, rollback=rollback, release=lambda: self.log.append("release") or {"ok": True, "version": "1.2"},
                     selected=lambda: self.selected, now=lambda: self.clock)


@pytest.fixture(autouse=True)
def _a_person_with_a_source_checkout(monkeypatch, tmp_path):
    for name in person.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(build_update.PIN_ENV, raising=False)
    monkeypatch.setattr(build_update, "checkout_of", lambda: tmp_path)
    monkeypatch.setattr(build_update.writer, "record", lambda *a, **k: AUDIT.append((a, k)) or True)
    AUDIT.clear()
    monkeypatch.setattr(build_update.person, "marked", lambda *a: "")


def test_a_member_on_a_newer_build_makes_this_device_stale_and_an_older_one_does_not():
    assert build_update.newest(pool(5, theirs=9))["build_seq"] == 9
    assert build_update.newest(pool(9, theirs=5)) is None
    assert build_update.newest(pool(9, theirs=9)) is None


def test_the_update_runs_when_nothing_holds_a_lease_and_moves_the_selected_runtime():
    calls = Calls()
    result = build_update.update_once(pool=pool(), steps=calls.steps())
    assert result.action == "updated" and calls.log == ["fetch", "ensure"]
    assert [k["outcome"] for _, k in AUDIT] == ["updated"] and AUDIT[0][0] == ("runtime.update",)
    assert runtime_store.read_state()["update"]["fails"] == 0


@pytest.mark.parametrize(("held", "queued"), [(1, 0), (0, 1)])
def test_the_update_waits_while_a_lease_is_held_or_queued_and_runs_when_it_is_free(held, queued):
    calls = Calls()
    waiting = build_update.update_once(pool=pool(held=held, queued=queued), steps=calls.steps())
    assert waiting.action == "waiting" and "lease" in waiting.detail and calls.log == []
    assert build_update.update_once(pool=pool(), steps=calls.steps()).action == "updated"
    assert calls.log == ["fetch", "ensure"]


def test_a_failed_health_check_selects_the_previous_runtime_again_and_backs_off_before_trying_again():
    calls = Calls(ensure_code=1)
    failed = build_update.update_once(pool=pool(), steps=calls.steps())
    assert failed.action == "failed" and "health check failed" in failed.detail and "rolled back to aaaaaaa" in failed.detail
    assert calls.selected == OLD and calls.log == ["fetch", "ensure", "rollback a"]
    again = build_update.update_once(pool=pool(), steps=calls.steps())
    assert again.action == "waiting" and calls.log == ["fetch", "ensure", "rollback a"], "no retry inside the backoff"
    calls.clock += build_update.BACKOFF_S + 1
    calls.ensure_code = 0
    assert build_update.update_once(pool=pool(), steps=calls.steps()).action == "updated"


def test_the_backoff_doubles_with_each_failure_and_is_capped():
    calls = Calls(ensure_code=1)
    for fails in range(3):
        build_update.update_once(pool=pool(), steps=calls.steps())
        assert runtime_store.read_state()["update"]["next_at"] == calls.clock + build_update.BACKOFF_S * 2**fails
        calls.clock += build_update.BACKOFF_MAX_S
    assert min(build_update.BACKOFF_S * 2**20, build_update.BACKOFF_MAX_S) == build_update.BACKOFF_MAX_S


def test_a_build_that_does_not_become_the_selected_runtime_is_a_failure_not_a_success():
    calls = Calls(switches=False)
    assert build_update.update_once(pool=pool(), steps=calls.steps()).action == "failed"


def test_a_pinned_device_is_untouched_and_says_so(monkeypatch):
    monkeypatch.setenv(build_update.PIN_ENV, "1")
    calls = Calls()
    assert build_update.update_once(pool=pool(), steps=calls.steps()).action == "pinned" and calls.log == []
    shown = pool()
    shown["build"]["state"] = "pinned"
    assert "pinned" in node_pool.build_line(shown) and node_pool.build_findings(shown)[0].good
    monkeypatch.delenv(build_update.PIN_ENV)
    build_update.pin(True)
    assert build_update.update_once(pool=pool(), steps=calls.steps()).action == "pinned" and calls.log == []
    build_update.pin(False)
    assert build_update.update_once(pool=pool(), steps=calls.steps()).action == "updated"


def test_a_device_that_is_current_or_has_no_node_does_nothing():
    calls = Calls()
    assert build_update.update_once(pool=pool(9, theirs=9), steps=calls.steps()).action == "current"
    assert build_update.update_once(pool=pool(9, theirs=3), steps=calls.steps()).action == "current"
    assert calls.log == []
    assert build_update.update_once(Path("/no/such/state"), steps=calls.steps()).action == "no-node"


def test_the_off_switch_turns_the_updater_off(monkeypatch):
    monkeypatch.setenv(build_update.OFF_ENV, "off")
    calls = Calls()
    assert build_update.update_once(pool=pool(), steps=calls.steps()).action == "off" and calls.log == []


def test_a_release_install_takes_the_signed_update_path(monkeypatch):
    monkeypatch.setattr(build_update, "checkout_of", lambda: None)
    calls = Calls()
    assert build_update.update_once(pool=pool(), steps=calls.steps()).action == "updated" and calls.log == ["release"]


def test_a_process_an_agent_started_asks_for_the_background_build_and_is_tried_again_after_a_wait(monkeypatch):
    monkeypatch.setattr(build_update.person, "marked", lambda *a: "CLAUDECODE")
    calls = Calls(switches=False)
    started = build_update.update_once(pool=pool(), steps=calls.steps())
    assert started.action == "started" and runtime_store.read_state()["update"]["fails"] == 1


def test_an_incompatible_member_is_shown_with_the_exact_fix_in_status_doctor_and_peers():
    fix = "run `poolhouse runtime ensure` on peer"
    shown = pool(5, theirs=5, wire_fix=fix)
    shown["members"][1]["build_state"] = "needs update"
    shown["build"]["state"] = "needs update"
    assert any("needs update" in line and fix in line for line in node_pool.member_lines(shown))
    assert "NEEDS UPDATE" in node_pool.build_line(shown)
    rows = node_pool.build_findings(shown)
    assert [f.good for f in rows] == [False, False] and rows[1].note == fix
    assert fix in node_pool.render(shown, as_json=False)


def test_a_stale_device_is_reported_as_stale_by_the_doctor_and_current_is_not():
    assert "STALE" in node_pool.build_line(pool())
    assert node_pool.build_findings(pool())[0].good is False
    assert node_pool.build_findings(pool(9, theirs=9))[0].good is True


def _git(where, *words):
    return subprocess.run(["git", "-C", str(where), *words], capture_output=True, text=True, check=True).stdout.strip()


def _repos(tmp_path):
    origin, clone, other = tmp_path / "origin.git", tmp_path / "clone", tmp_path / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True)
    for where in (clone, other):
        subprocess.run(["git", "clone", "-q", str(origin), str(where)], check=True, capture_output=True)
        _git(where, "config", "user.email", "t@example.com")
        _git(where, "config", "user.name", "t")
        _git(where, "checkout", "-q", "-B", "dev")
    (other / "a").write_text("1")
    _git(other, "add", "a")
    _git(other, "commit", "-q", "-m", "one")
    _git(other, "push", "-q", "origin", "dev")
    _git(clone, "fetch", "-q")
    _git(clone, "merge", "-q", "--ff-only", "origin/dev")
    return clone, other


def test_the_fast_forward_takes_new_commits_of_the_development_branch_from_the_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "dev")
    clone, other = _repos(tmp_path)
    (other / "b").write_text("2")
    _git(other, "add", "b")
    _git(other, "commit", "-q", "-m", "two")
    _git(other, "push", "-q", "origin", "dev")
    code, _ = build_update.fast_forward(clone)
    assert code == 0 and _git(clone, "rev-parse", "HEAD") == _git(other, "rev-parse", "HEAD")


def test_the_fast_forward_never_merges_a_diverged_checkout_or_leaves_the_development_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "dev")
    clone, other = _repos(tmp_path)
    (other / "b").write_text("2")
    _git(other, "add", "b")
    _git(other, "commit", "-q", "-m", "two")
    _git(other, "push", "-q", "origin", "dev")
    (clone / "c").write_text("local")
    _git(clone, "add", "c")
    _git(clone, "commit", "-q", "-m", "local")
    before = _git(clone, "rev-parse", "HEAD")
    code, _ = build_update.fast_forward(clone)
    assert code != 0 and _git(clone, "rev-parse", "HEAD") == before
    _git(clone, "checkout", "-q", "-b", "side")
    code, text = build_update.fast_forward(clone)
    assert code != 0 and "not the development branch" in text


def test_the_build_order_is_the_commit_count_and_is_remembered(tmp_path):
    clone, _ = _repos(tmp_path)
    head = _git(clone, "rev-parse", "HEAD")
    assert build_update.count(head, clone) == 1
    assert runtime_store.read_state()["seqs"] == {head: 1}
    assert build_update.count(head, None) == 0 and build_update.count("", clone) == 0


def test_a_hostile_origin_address_or_branch_name_never_reaches_git(tmp_path, monkeypatch):
    clone, _ = _repos(tmp_path)
    marker = tmp_path / "ran"
    _git(clone, "config", "remote.origin.url", f"--upload-pack=touch {marker}")
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "dev")
    code, text = build_update.fast_forward(clone)
    assert code != 0 and "not an https or ssh git address" in text and not marker.exists()
    _git(clone, "config", "remote.origin.url", str(tmp_path / "origin.git"))
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "--upload-pack=x")
    code, text = build_update.fast_forward(clone)
    assert code != 0 and not marker.exists()


def test_a_hostile_build_id_is_never_passed_to_the_node_or_to_git(tmp_path, monkeypatch):
    clone, _ = _repos(tmp_path)
    monkeypatch.setattr(runtime_store, "selection", lambda root=None: {"commit": "--exec=touch x"})
    assert build_update.node_args() == []
    assert build_update.count("--output=" + str(tmp_path / "out"), clone) == 0 and not (tmp_path / "out").exists()
    monkeypatch.setattr(runtime_store, "selection", lambda root=None: {"commit": "c" * 40})
    monkeypatch.setattr(build_update, "count", lambda commit, checkout: 7)
    assert build_update.node_args() == ["--build-id", "c" * 40, "--build-seq", "7"]
    monkeypatch.setenv(build_update.PIN_ENV, "yes")
    assert build_update.node_args()[-1] == "--pinned"
