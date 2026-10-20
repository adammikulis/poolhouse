import contextlib
import threading
from types import SimpleNamespace

import pytest

from poolhouse.fleet import updates
from poolhouse.fleet.daemon import DaemonRuntime
from poolhouse.fleet.setup_jobs import Jobs
from poolhouse.lock import only_one

OLD, NEW = "1" * 40, "2" * 40


def candidate(monkeypatch, tmp_path):
    """Only owned temp download and fixed git replies; production update logic runs."""
    monkeypatch.setattr(updates, "running_path", lambda: tmp_path / "app")
    monkeypatch.setattr(updates, "current_version", lambda: "1.0.0")
    monkeypatch.setattr(updates, "check", lambda: updates.Release("2.0.0", "", "", ({"name": "app.zip"},), 0))
    monkeypatch.setattr(updates, "asset_for", lambda release: release.assets[0])
    monkeypatch.setattr(updates, "download_release", lambda release, asset, directory: tmp_path / "app.zip")
    monkeypatch.setattr(updates, "_installed_commit", lambda: OLD)
    monkeypatch.setattr(updates.signing, "RELEASE_KEY", "ssh-ed25519 AAAAfixture")

    def git(argv):
        if argv[0] == "ls-remote":
            return 0, NEW
        if argv == ["rev-parse", "HEAD"]:
            return 0, OLD
        if argv == ["rev-parse", "FETCH_HEAD"]:
            return 0, NEW
        return 0, ""
    return git


@pytest.mark.parametrize('kind', ['release', 'branch'])
def test_update_holds_admission_through_restart(tmp_path, monkeypatch, kind):
    jobs = Jobs(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def install(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        calls.append('installed')
        if kind == 'release':
            return {'installed': True}
        assert rejected()
        return 0, "installed"

    def rejected():
        with pytest.raises(ValueError, match='runtime update'):
            jobs.start('libraries', {}, lambda progress: calls.append('orphan'))
        return True

    def restart():
        assert rejected()
        calls.append('restarted')
        return True

    def admission(ready=lambda: True):
        return only_one(tmp_path / 'runtime-install.lock', wait=False)
    schedule = updates.UpdateSchedule(interval=0, first_after_s=0, rounds=1)
    git = candidate(monkeypatch, tmp_path)
    if kind == 'release':
        monkeypatch.setattr(updates, 'install', install)
        thread = updates.watch(wanted=lambda: True, idle=lambda: not jobs.active(), restart=restart,
                               admission=admission, schedule=schedule)
    else:
        thread = updates.track(updates.TrackedBranch('https://example.test/repo', 'dev', tmp_path),
                               idle=lambda: not jobs.active(), runtime=updates.UpdateRuntime(git=git, pip=install, restart=restart, admission=admission),
                               schedule=schedule)
    assert entered.wait(5)
    assert rejected()
    assert not jobs.all()
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert 'installed' in calls and 'orphan' not in calls


@pytest.mark.parametrize('kind', ['release', 'branch'])
def test_queued_jobs_prevent_update_and_admit_more_jobs(tmp_path, monkeypatch, kind):
    jobs = Jobs(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def install(progress):
        entered.set()
        assert release.wait(5)
        return {}

    jobs.start('libraries', {'add': 'one'}, install)
    assert entered.wait(5)
    second = jobs.start('libraries', {'add': 'two'}, lambda progress: {})
    assert second['state'] == 'queued'
    calls = []
    monkeypatch.setattr(updates, 'apply_if_newer', lambda: calls.append('release'))
    monkeypatch.setattr(updates, 'track_once', lambda *a, **k: calls.append('branch'))
    def admission(ready=lambda: True):
        return only_one(tmp_path / 'runtime-install.lock', wait=False)
    schedule = updates.UpdateSchedule(interval=0, first_after_s=0, rounds=1)
    if kind == 'release':
        thread = updates.watch(wanted=lambda: True, idle=lambda: not jobs.active(), admission=admission, schedule=schedule)
    else:
        thread = updates.track(updates.TrackedBranch('https://example.test/repo', 'dev', tmp_path),
                               idle=lambda: not jobs.active(), runtime=updates.UpdateRuntime(admission=admission), schedule=schedule)
    thread.join(5)
    assert not calls
    release.set()
    jobs._worker.join(5)
    assert not jobs.active()


@pytest.mark.parametrize('tracked', [False, True])
def test_daemon_wires_shared_update_admission(tmp_path, monkeypatch, tracked):
    daemon = object.__new__(DaemonRuntime)
    daemon.root = tmp_path
    daemon.settings = SimpleNamespace(track_branch='dev' if tracked else '', track_repo='', auto_update=True, setup_done=False)
    daemon.port = 8770
    daemon.httpd = SimpleNamespace(shutdown=lambda: None)
    daemon.runner = SimpleNamespace(status=lambda: {'busy': False, 'queued': 0})
    daemon.downloads = SimpleNamespace(active=lambda: [])
    daemon.interface = None
    daemon.web = True
    daemon.initial_setup = True
    daemon.bench_host = SimpleNamespace(measuring=lambda: False)
    daemon.serving = SimpleNamespace(live=lambda: [])
    daemon.environment = SimpleNamespace()
    daemon.managed_alignment = SimpleNamespace(start=lambda *a: None)
    monkeypatch.setattr("poolhouse.fleet.managed_alignment.idle", lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(updates, 'checkout_here', lambda: tmp_path)
    captured = {}
    monkeypatch.setattr(updates, 'track', lambda source, **kwargs: captured.update(kwargs))
    monkeypatch.setattr(updates, 'watch', lambda **kwargs: captured.update(kwargs))
    daemon.updates()
    assert not captured['idle']()
    daemon.settings.setup_done = True
    assert captured['idle']()
    admission = captured['runtime'].admission if tracked else captured['admission']
    with admission(), pytest.raises(ValueError, match='runtime update'):
        Jobs(tmp_path).start('libraries', {}, lambda progress: {})
    daemon.initial_setup = False
    daemon.settings.setup_done = False
    assert captured['idle']()


@pytest.mark.parametrize('kind', ['release', 'branch'])
def test_update_defers_when_admission_is_held(tmp_path, monkeypatch, kind):
    calls = []
    git = candidate(monkeypatch, tmp_path)
    monkeypatch.setattr(updates, 'install', lambda *a, **k: calls.append('release'))

    def admission(ready=lambda: True):
        return only_one(tmp_path / 'runtime-install.lock', wait=False)

    schedule = updates.UpdateSchedule(interval=0, first_after_s=0, rounds=1)
    with admission():
        if kind == 'release':
            thread = updates.watch(wanted=lambda: True, idle=lambda: True, admission=admission, schedule=schedule)
        else:
            thread = updates.track(updates.TrackedBranch('https://example.test/repo', 'dev', tmp_path),
                                   runtime=updates.UpdateRuntime(git=git, pip=lambda path: calls.append("branch"), admission=admission), schedule=schedule)
        thread.join(5)
        assert not thread.is_alive()
    assert not calls


@pytest.mark.parametrize("announce", [False, True])
def test_leaving_last_pool_stops_discovery_and_rotates_auth(tmp_path, monkeypatch, announce):
    from poolhouse import macauth
    from poolhouse.fleet import daemon as module
    from poolhouse.fleet.discovery import derive_token

    runtime = object.__new__(DaemonRuntime)
    old_key = b'k' * 32
    runtime.root = tmp_path
    runtime.pool_key_path = tmp_path / 'pool.key'
    runtime.key = old_key
    runtime.token = module.load_or_create_token(tmp_path, old_key, profile=runtime.pool_key_path)
    stopped = []
    runtime.advertisers = {'old': SimpleNamespace(stop=lambda: stopped.append(True))}
    runtime.advertiser = runtime.advertisers['old']
    runtime.announcement_lock = threading.RLock()
    runtime.announce = announce
    runtime.members = SimpleNamespace(ensure_self=lambda identity, name: None)
    runtime.identity, runtime.name = None, 'fixture'
    runtime.fetcher = SimpleNamespace(key=old_key)
    runtime.settings = SimpleNamespace(pool_mode='dev')
    runtime.daemon = SimpleNamespace(pool_mode='dev')
    monkeypatch.setattr(module, 'memberships', lambda path: [])
    runtime.joined_a_pool()
    assert stopped == [True] and runtime.advertisers == {}
    assert runtime.key is None and runtime.fetcher.key is None and runtime.advertiser is None
    assert runtime.token != derive_token(old_key)
    assert runtime.token.startswith(macauth.PREFIX)
    assert module.load_or_create_token(tmp_path, profile=runtime.pool_key_path) == runtime.token
    assert runtime.every_token() == set()
    auth = macauth.Authenticator(lambda: {runtime.token, *runtime.every_token()})
    url = "https://device.test/status"
    headers = {"Host": "device.test", **macauth.sign(derive_token(old_key), "GET", url, None)}
    assert not auth.check("GET", "/status", headers, None).ok
    headers = {"Host": "device.test", **macauth.sign(runtime.token, "GET", url, None)}
    assert auth.check("GET", "/status", headers, None).ok



def test_unannounced_pool_change_updates_machine_auth(tmp_path, monkeypatch):
    from poolhouse.fleet import daemon as module
    from poolhouse.fleet.discovery import derive_token

    runtime = object.__new__(DaemonRuntime)
    runtime.root = tmp_path
    runtime.pool_key_path = tmp_path / 'pool.key'
    runtime.key = b'a' * 32
    runtime.token = derive_token(runtime.key)
    runtime.fetcher = SimpleNamespace(key=runtime.key)
    runtime.advertiser = None
    runtime.advertisers = {}
    runtime.announcement_lock = threading.RLock()
    runtime.announce = False
    runtime.members = SimpleNamespace(ensure_self=lambda identity, name: None)
    runtime.identity, runtime.name = None, 'fixture'
    member = SimpleNamespace(group='new', key=b'b' * 32)
    monkeypatch.setattr(module, 'memberships', lambda path: [member])
    runtime.start_announcing()
    assert runtime.key == member.key and runtime.fetcher.key == member.key
    assert runtime.token == derive_token(member.key)
    assert runtime.advertiser is None and not runtime.advertisers


@pytest.mark.parametrize('kind', ['follow', 'disabled', 'release', 'branch'])
def test_unchanged_or_disabled_poll_never_enters_destructive_admission(tmp_path, monkeypatch, kind):
    git = candidate(monkeypatch, tmp_path)
    calls = []

    @contextlib.contextmanager
    def admission(ready=lambda: True):
        calls.append('retire warm broker')
        yield

    schedule = updates.UpdateSchedule(interval=0, first_after_s=0, rounds=1)
    if kind == 'follow':
        monkeypatch.setattr(updates, 'selected_commit', lambda: OLD)
        thread = updates.follow_runtime(idle=lambda: True, admission=admission, schedule=schedule)
    elif kind == 'branch':
        monkeypatch.setattr(updates, '_installed_commit', lambda: NEW)
        def same(argv):
            return (0, NEW) if argv == ['rev-parse', 'HEAD'] else git(argv)
        thread = updates.track(updates.TrackedBranch('https://example.test/repo', 'dev', tmp_path),
                               runtime=updates.UpdateRuntime(git=same, admission=admission), schedule=schedule)
    else:
        monkeypatch.setattr(updates, 'check', lambda: updates.Release('1.0.0', '', '', (), 0))
        thread = updates.watch(wanted=lambda: kind != 'disabled', idle=lambda: True,
                               admission=admission, schedule=schedule)
    thread.join(5)
    assert not thread.is_alive()
    assert calls == []


@pytest.mark.parametrize('raced', [False, True])
def test_changed_selected_runtime_enters_once_and_rechecks_target(tmp_path, monkeypatch, raced):
    candidate(monkeypatch, tmp_path)
    chosen, calls = [NEW], []
    monkeypatch.setattr(updates, 'selected_commit', lambda: chosen[0])
    monkeypatch.setattr(updates, 'restart_after_update', lambda: calls.append('restart') or 'restarted')

    @contextlib.contextmanager
    def admission(ready=lambda: True):
        calls.append('enter')
        if raced:
            chosen[0] = OLD
        yield
        calls.append('leave')

    thread = updates.follow_runtime(idle=lambda: True, admission=admission,
                                    schedule=updates.UpdateSchedule(interval=0, first_after_s=0, rounds=1))
    thread.join(5)
    assert not thread.is_alive()
    assert calls == (['enter', 'leave'] if raced else ['enter', 'restart', 'leave'])


@pytest.mark.parametrize('race', ['wanted', 'version', 'path'])
def test_release_preparation_race_never_admits_or_installs(tmp_path, monkeypatch, race):
    candidate(monkeypatch, tmp_path)
    ready = [True]
    calls = []

    def download(*args):
        if race == 'wanted':
            ready[0] = False
        elif race == 'version':
            monkeypatch.setattr(updates, 'current_version', lambda: '2.0.0')
        else:
            monkeypatch.setattr(updates, 'running_path', lambda: tmp_path / 'other')
        return tmp_path / 'app.zip'

    monkeypatch.setattr(updates, 'download_release', download)
    monkeypatch.setattr(updates, 'install', lambda *a, **k: calls.append('install'))
    result = updates.apply_if_newer(admission=lambda: calls.append('admit'), ready=lambda: ready[0])
    assert not result['installed'] and calls == []


@pytest.mark.parametrize('failure', ['signature', 'ancestor', 'changed_head', 'changed_fetch'])
def test_branch_refusal_or_candidate_race_never_admits(tmp_path, monkeypatch, failure):
    git = candidate(monkeypatch, tmp_path)
    calls, reads = [], [0]

    def answering(argv):
        if failure == 'signature' and 'verify-commit' in argv:
            return 1, 'unsigned'
        if failure == 'ancestor' and argv[0] == 'merge-base':
            return 1, ''
        if argv == ['rev-parse', 'HEAD']:
            reads[0] += 1
            if failure == 'changed_head' and reads[0] > 1:
                return 0, '3' * 40
        if failure == 'changed_fetch' and argv == ['rev-parse', 'FETCH_HEAD']:
            return 0, '3' * 40
        return git(argv)

    got = updates.track_once(updates.TrackedBranch('https://example.test/repo', 'dev', tmp_path),
                             runtime=updates.UpdateRuntime(git=answering, admission=lambda: calls.append('admit')))
    assert not got.installed and not got.pulled and calls == []


@pytest.mark.parametrize('kind', ['follow', 'release', 'branch'])
def test_actual_code_admission_rechecks_after_lock_before_retiring_broker(tmp_path, monkeypatch, kind):
    from poolhouse.fleet import managed_alignment

    git = candidate(monkeypatch, tmp_path)
    chosen, wanted, calls = [NEW], [True], []
    monkeypatch.setattr(updates, 'selected_commit', lambda: chosen[0])
    monkeypatch.setattr(updates, 'restart_after_update', lambda: calls.append('restart'))
    monkeypatch.setattr(updates, 'install', lambda *a, **k: calls.append('install'))

    @contextlib.contextmanager
    def lock():
        calls.append('lock')
        chosen[0] = OLD
        wanted[0] = False
        yield

    @contextlib.contextmanager
    def drain(*args):
        calls.append('drain')
        yield

    monkeypatch.setattr(managed_alignment, 'idle', drain)
    daemon = SimpleNamespace(update_admission=lock, environment=object(), nothing_running=lambda: True)
    def admission(ready):
        return managed_alignment.code_update_admission(daemon, ready)
    schedule = updates.UpdateSchedule(interval=0, first_after_s=0, rounds=1)
    if kind == 'follow':
        thread = updates.follow_runtime(idle=lambda: True, admission=admission, schedule=schedule)
    elif kind == 'release':
        thread = updates.watch(wanted=lambda: wanted[0], idle=lambda: True,
                               admission=admission, schedule=schedule)
    else:
        thread = updates.track(updates.TrackedBranch('https://example.test/repo', 'dev', tmp_path),
                               idle=lambda: wanted[0], schedule=schedule,
                               runtime=updates.UpdateRuntime(git=git, admission=admission,
                                                             pip=lambda path: calls.append('install')))
    thread.join(5)
    assert not thread.is_alive()
    assert calls == ['lock']
