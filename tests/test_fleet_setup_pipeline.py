"""Preparation admission and scripted native protocol integration, not model ability."""
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import lock
from poolhouse.fleet import setup_preparation as preparation
from poolhouse.fleet.models import Models

pytest_plugins = ['serve_node', 'native_server_kit', 'test_fleet_setup_probe']
BINDING = {'scope': 'one', 'device': 'device', 'principal': 'session'}


def ui_for(tmp_path):
    return SimpleNamespace(root=tmp_path, setup_jobs=None, _setup_jobs_lock=threading.Lock(),
                           settings=SimpleNamespace(context=2048, download_sources='internet'), downloads=None)


def body():
    return {'idempotency_key': 'a' * 32, 'model_id': 'installed:' + 'b' * 64, 'download': False}


def finish(ui, ident):
    jobs = preparation.setup_jobs.jobs(ui)
    worker = jobs._worker
    if worker is not None:
        worker.join(timeout=15)
        assert not worker.is_alive()
    return jobs.get(ident)


def test_one_ui_has_one_cached_preparation_owner_without_starting_jobs(tmp_path):
    ui = ui_for(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(lambda _: preparation.service(ui), range(8)))
    assert all(value is values[0] for value in values)
    assert ui.setup_jobs is None and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('changed', [
    {'download': 1}, {'model_id': None}, {'idempotency_key': True}, {'idempotency_key': 'x' * 32},
    {'project_id': '../outside'}, {'project_id': True}, {'url': 'https://invalid.test/model'},
])
def test_invalid_selection_never_resolves_models_or_enqueues(tmp_path, monkeypatch, changed):
    ui = ui_for(tmp_path)
    monkeypatch.setattr(preparation, 'selected_model', lambda *args: pytest.fail('invalid selection resolved'))
    with pytest.raises(ValueError):
        preparation.service(ui).start({**body(), **changed}, binding=BINDING, current=lambda: True)
    assert ui.setup_jobs is None


def test_same_key_attaches_interrupted_without_rediscovering_or_repeating(tmp_path, monkeypatch):
    ui = ui_for(tmp_path)
    service = preparation.service(ui)
    effects = []
    monkeypatch.setattr(preparation, 'selected_model', lambda *_: (object(), None))

    def unknown(selection, progress):
        effects.append('one effect with lost receipt')
        raise OSError('unknown')

    monkeypatch.setattr(service, '_execute', unknown)
    first = service.start(body(), binding=BINDING, current=lambda: True)
    assert finish(ui, first['id'])['state'] == 'interrupted'
    monkeypatch.setattr(preparation, 'selected_model', lambda *_: pytest.fail('attached command resolved model again'))
    assert service.start(body(), binding=BINDING, current=lambda: True)['id'] == first['id']
    with pytest.raises(ValueError, match='different selection'):
        service.start({**body(), 'download': True}, binding=BINDING, current=lambda: True)
    assert effects == ['one effect with lost receipt']


def test_concurrent_same_key_uses_same_existing_job_and_only_one_operation(tmp_path, monkeypatch):
    ui = ui_for(tmp_path)
    service = preparation.service(ui)
    entered, release = threading.Event(), threading.Event()
    effects = []
    monkeypatch.setattr(preparation, 'selected_model', lambda *_: (object(), None))

    def operation(selection, progress):
        effects.append(progress.row['id'])
        entered.set()
        assert release.wait(5)
        return {}

    monkeypatch.setattr(service, '_execute', operation)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(service.start, body(), binding=BINDING, current=lambda: True) for _ in range(2)]
            results = [future.result(timeout=5) for future in futures]
        assert results[0]['id'] == results[1]['id']
        assert entered.wait(5)
    finally:
        release.set()
    assert finish(ui, results[0]['id'])['state'] == 'done'
    assert effects == [results[0]['id']]


def test_authority_revoked_during_selection_never_submits_operation(tmp_path, monkeypatch):
    ui = ui_for(tmp_path)
    active = [True]

    def resolve(*_):
        active[0] = False
        return object(), None

    monkeypatch.setattr(preparation, 'selected_model', resolve)
    with pytest.raises(PermissionError):
        preparation.service(ui).start(body(), binding=BINDING, current=lambda: active[0])
    assert preparation.setup_jobs.jobs(ui).all() == []


@pytest.fixture
def native_preparation(tmp_path, native_probe, monkeypatch):
    ui = ui_for(tmp_path / 'setup')
    ui.root.mkdir()
    ui.models = Models(store=tmp_path / 'managed', roots=[native_probe.model.parent])
    info = native_probe.model.stat()
    row = {'path': str(native_probe.model), 'name': native_probe.model.name, 'servable': True,
           'capabilities': {'chat': True}, 'size_bytes': info.st_size, 'mtime': info.st_mtime,
           'context_length': 2048, 'files': []}
    # The scripted native fixture is not real model metadata or model ability.
    monkeypatch.setattr(ui.models, 'library', lambda: [row])
    monkeypatch.setattr(preparation.llama, 'ensure_server', lambda *args, **kwargs: native_probe.info.process.args[0])
    calls = []

    def start(model, *, context, current):
        assert current() is True
        assert model.path == native_probe.model and context == 2048
        calls.append(str(model.path))
        return SimpleNamespace(port=native_probe.info.port, context=2048)

    ui.start_serving = start
    ui.stop_serving = lambda *_: pytest.fail('preparation must not stop shared residency')
    request = {**body(), 'model_id': preparation._model_id(row)}
    return SimpleNamespace(ui=ui, request=request, native=native_probe, starts=calls)


def test_fixed_pipeline_reuses_native_resident_and_proves_current_status(native_preparation):
    case = native_preparation
    service = preparation.service(case.ui)
    row = service.start(case.request, binding=BINDING, current=lambda: True)
    final = finish(case.ui, row['id'])
    assert final['state'] == 'done', final['error']
    assert final['stage'] == 'ready' and set(final['probes']) == set(preparation.setup_jobs.PROBES)
    assert final['serving_receipt']['artifact_digest'] == hashlib.sha256(case.native.model.read_bytes()).hexdigest()
    result = service.status(row['id'], binding=BINDING, current=lambda: True)
    assert result['result']['model'] == str(case.native.model)
    assert result['result']['chat_ready'] is True and result['result']['tools_ready'] is True
    assert result['result']['historical'] is False
    assert case.starts == [str(case.native.model)]
    assert service.start(case.request, binding=BINDING, current=lambda: True)['id'] == row['id']
    assert case.starts == [str(case.native.model)]
    case.native.model.write_bytes(case.native.model.read_bytes() + b'changed')
    stale = service.status(row['id'], binding=BINDING, current=lambda: True)
    assert stale['result']['historical'] is True and stale['result']['chat_ready'] is False
    assert 'model' not in stale['result']
    assert case.native.info.process.poll() is None


def test_cancel_during_indivisible_runtime_leaves_runtime_and_stops_future_stages(native_preparation, monkeypatch):
    case = native_preparation
    entered, release = threading.Event(), threading.Event()
    service = preparation.service(case.ui)

    def runtime(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return Path('/fixture/runtime')

    monkeypatch.setattr(preparation.llama, 'ensure_server', runtime)
    row = service.start(case.request, binding=BINDING, current=lambda: True)
    assert entered.wait(5)
    try:
        cancelled = service.cancel(row['id'], binding=BINDING, current=lambda: True)
        assert cancelled['cancel_requested'] is True and cancelled['can_cancel'] is False
    finally:
        release.set()
    assert finish(case.ui, row['id'])['state'] == 'cancelled'
    assert case.starts == [] and case.native.info.process.poll() is None
    assert service._controls == {}


def test_changed_queued_installed_selection_refuses_before_runtime_side_effect(native_preparation, monkeypatch):
    case = native_preparation
    service = preparation.service(case.ui)
    installs = []
    monkeypatch.setattr(preparation.llama, 'ensure_server', lambda *args, **kwargs: installs.append('runtime'))
    with lock.held(case.ui.root / 'setup-install.lock'):
        row = service.start(case.request, binding=BINDING, current=lambda: True)
        case.native.model.write_bytes(case.native.model.read_bytes() + b'changed while queued')
    final = finish(case.ui, row['id'])
    assert final['state'] == 'interrupted' and installs == [] and case.starts == []
    assert case.native.info.process.poll() is None


@pytest.fixture
def curated_preparation(tmp_path, monkeypatch):
    from poolhouse import hub
    from poolhouse.fleet.catalogue import Suggestion
    from tests.net_site import gguf_bytes

    ui = ui_for(tmp_path / 'setup')
    ui.root.mkdir()
    store = tmp_path / 'managed'
    ui.models = Models(store=store, roots=[store], sources=lambda: 'internet')
    target = store / 'chosen.gguf'
    body_bytes = gguf_bytes(extra=1 << 20)
    blob = tmp_path / hashlib.sha256(body_bytes).hexdigest()
    blob.write_bytes(body_bytes)
    pick = Suggestion('Fixture model', 'hf:maker/model/chosen.gguf', .1, 'Fixture')
    monkeypatch.setattr(preparation, 'SUGGESTED', [pick])
    monkeypatch.setattr(preparation, 'selected_model', lambda *args: (None, pick))
    monkeypatch.setattr(hub, 'pull', lambda *args, **kwargs: blob)
    monkeypatch.setattr(preparation.llama, 'ensure_server', lambda *args, **kwargs: Path('/fixture/runtime'))

    def library():
        if not target.exists():
            return []
        info = target.stat()
        return [{'path': str(target), 'name': target.name, 'servable': True, 'capabilities': {'chat': True},
                 'size_bytes': info.st_size, 'mtime': info.st_mtime, 'context_length': 2048, 'files': []}]

    monkeypatch.setattr(ui.models, 'library', library)
    starts = []

    def start(selected, **kwargs):
        starts.append(selected)
        raise RuntimeError('Stop this fixture before any server request')

    ui.start_serving = start
    return SimpleNamespace(ui=ui, target=target, body=body_bytes, pick=pick, starts=starts,
                           request={'idempotency_key': 'b' * 32, 'model_id': pick.file, 'download': True})


@pytest.mark.parametrize('same_source', [False, True])
def test_concurrent_curated_filename_requires_actual_pinned_bytes(curated_preparation, monkeypatch, same_source):
    case = curated_preparation
    entered, release = threading.Event(), threading.Event()

    def runtime(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return Path('/fixture/runtime')

    monkeypatch.setattr(preparation.llama, 'ensure_server', runtime)
    row = preparation.service(case.ui).start(case.request, binding=BINDING, current=lambda: True)
    assert entered.wait(5)
    try:
        case.target.parent.mkdir()
        foreign = case.body if same_source else case.body + b'other-source'
        case.target.write_bytes(foreign)
        inode = case.target.stat().st_ino
    finally:
        release.set()
    assert finish(case.ui, row['id'])['state'] == 'interrupted'
    assert len(case.starts) == (1 if same_source else 0)
    if same_source:
        assert case.starts[0].verified_reference.source == case.pick.ref
        assert case.starts[0].verified_reference.sha256 == hashlib.sha256(case.body).hexdigest()
    assert case.target.stat().st_ino == inode and case.target.read_bytes() == foreign


def test_curated_target_replaced_after_verified_return_cannot_start(curated_preparation, monkeypatch):
    case = curated_preparation
    lookup = case.ui.models.library_model
    foreign = case.body + b'changed-after-verified-return'

    def replace_after_lookup(path):
        selected = lookup(path)
        case.target.unlink()
        case.target.write_bytes(foreign)
        return selected

    monkeypatch.setattr(case.ui.models, 'library_model', replace_after_lookup)
    row = preparation.service(case.ui).start(case.request, binding=BINDING, current=lambda: True)
    assert finish(case.ui, row['id'])['state'] == 'interrupted'
    assert case.starts == [] and case.target.read_bytes() == foreign


def test_unknown_tool_request_retains_current_chat_proof_without_resending(native_preparation, monkeypatch):
    case = native_preparation
    service = preparation.service(case.ui)
    jobs = preparation.setup_jobs.jobs(case.ui)
    probe = jobs._probe

    def lost_after_intent(row, phase, fingerprint, completion, current):
        result = probe(row, phase, fingerprint, completion, current)
        if phase == 'tool-call' and completion is None:
            raise OSError('isolated unknown tool request boundary')
        return result

    monkeypatch.setattr(jobs, '_probe', lost_after_intent)
    row = service.start(case.request, binding=BINDING, current=lambda: True)
    stored = finish(case.ui, row['id'])
    assert stored['state'] == 'interrupted' and stored['probes']['tool-call']['status'] == 'intent'
    renewed = {**BINDING, 'principal': 'renewed'}
    result = service.status(row['id'], binding=renewed, current=lambda: True)
    assert result['result']['chat_ready'] is True and result['result']['tools_ready'] is False
    assert result['result']['historical'] is False and result['can_cancel'] is False
    assert service.start(case.request, binding=BINDING, current=lambda: True)['id'] == row['id']
    assert case.starts == [str(case.native.model)]
    assert case.native.info.log_path.read_text().count('POST') == 1


def test_fresh_status_rechecks_current_after_actual_native_observation(native_preparation, monkeypatch):
    case = native_preparation
    service = preparation.service(case.ui)
    row = service.start(case.request, binding=BINDING, current=lambda: True)
    assert finish(case.ui, row['id'])['state'] == 'done'
    active = [True]
    observe = preparation.setup_probe.checked_startup

    def revoked(target):
        result = observe(target)
        active[0] = False
        return result

    monkeypatch.setattr(preparation.setup_probe, 'checked_startup', revoked)
    with pytest.raises(PermissionError):
        service.status(row['id'], binding=BINDING, current=lambda: active[0])
    assert len(case.starts) == 1


def test_runtime_current_revocation_stops_transfer_and_serve(native_preparation, monkeypatch):
    case = native_preparation
    service = preparation.service(case.ui)
    active = [True]
    entered, release = threading.Event(), threading.Event()

    def runtime(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        active[0] = False
        return Path('/fixture/completed-runtime')

    monkeypatch.setattr(preparation.llama, 'ensure_server', runtime)
    row = service.start(case.request, binding=BINDING, current=lambda: active[0])
    assert entered.wait(5)
    release.set()
    assert finish(case.ui, row['id'])['state'] == 'interrupted'
    assert case.starts == [] and case.native.info.process.poll() is None
    assert service._controls == {}


def test_proven_resident_skips_installer_transfer_and_start_without_replaying(native_preparation, monkeypatch):
    case = native_preparation
    case.ui.serving = SimpleNamespace(live=lambda **kwargs: [
        SimpleNamespace(port=case.native.info.port, context=2048)])
    monkeypatch.setattr(preparation.llama, 'ensure_server', lambda *args, **kwargs: pytest.fail('installed unnecessary runtime'))
    monkeypatch.setattr(case.ui.models, 'ensure', lambda *args, **kwargs: pytest.fail('transferred resident model'))
    case.ui.start_serving = lambda *args, **kwargs: pytest.fail('started already receipted server')
    service = preparation.service(case.ui)
    row = service.start(case.request, binding=BINDING, current=lambda: True)
    assert finish(case.ui, row['id'])['state'] == 'done'
    assert service.status(row['id'], binding=BINDING, current=lambda: True)['result']['tools_ready'] is True
    assert service.start(case.request, binding=BINDING, current=lambda: True)['id'] == row['id']
    assert case.native.info.log_path.read_text().splitlines().count('POST') == 3
    assert case.native.info.process.poll() is None


def test_unreceipted_live_metadata_cannot_skip_normal_runtime_path(native_preparation, monkeypatch):
    case = native_preparation
    case.ui.serving = SimpleNamespace(live=lambda **kwargs: [
        SimpleNamespace(port=case.native.info.port, context=2048)])
    monkeypatch.setattr(preparation.setup_probe, 'checked_startup', lambda _: None)
    installs = []
    monkeypatch.setattr(preparation.llama, 'ensure_server', lambda *args, **kwargs: installs.append('runtime'))
    row = preparation.service(case.ui).start(case.request, binding=BINDING, current=lambda: True)
    assert finish(case.ui, row['id'])['state'] == 'done'
    assert installs == ['runtime'] and len(case.starts) == 1
    assert case.native.info.log_path.read_text().splitlines().count('POST') == 0


@pytest.mark.parametrize('probe_executable', [True], indirect=True)
def test_current_native_health_does_not_replace_refused_chat_receipt(native_preparation):
    case = native_preparation
    service = preparation.service(case.ui)
    row = service.start(case.request, binding=BINDING, current=lambda: True)
    stored = finish(case.ui, row['id'])
    assert stored['state'] == 'done' and stored['probes']['chat']['status'] == 'refused'
    result = service.status(row['id'], binding=BINDING, current=lambda: True)
    assert result['result']['chat_ready'] is False and result['result']['tools_ready'] is False
    assert result['result']['historical'] is True and 'model' not in result['result']
    assert case.native.info.log_path.read_text().splitlines().count('POST') == 1


def test_registered_project_is_revalidated_before_job_and_serving(native_preparation):
    case = native_preparation
    project_id, resolved = 'd' * 32, []

    def resolve(identifier, *, current):
        assert identifier == project_id and current() is True
        resolved.append(identifier)
        return SimpleNamespace(id=identifier)

    case.ui.projects = SimpleNamespace(resolve_coding_project=resolve)
    row = preparation.service(case.ui).start({**case.request, 'project_id': project_id},
                                             binding=BINDING, current=lambda: True)
    assert finish(case.ui, row['id'])['state'] == 'done'
    assert resolved == [project_id] * 3


def test_revoked_queued_project_refuses_before_runtime_or_serve(native_preparation, monkeypatch):
    case = native_preparation
    active, installs = [True], []

    def resolve(identifier, *, current):
        assert current() is True
        if not active[0]:
            raise ValueError('registered project is unavailable')
        return SimpleNamespace(id=identifier)

    case.ui.projects = SimpleNamespace(resolve_coding_project=resolve)
    monkeypatch.setattr(preparation.llama, 'ensure_server', lambda *args, **kwargs: installs.append('runtime'))
    with lock.held(case.ui.root / 'setup-install.lock'):
        row = preparation.service(case.ui).start({**case.request, 'project_id': 'd' * 32},
                                                 binding=BINDING, current=lambda: True)
        active[0] = False
    assert finish(case.ui, row['id'])['state'] == 'interrupted'
    assert installs == [] and case.starts == []


def test_revocation_during_empty_resident_discovery_prevents_runtime_install(native_preparation, monkeypatch):
    case = native_preparation
    entered, release = threading.Event(), threading.Event()
    active, installs = [True], []

    def live(**kwargs):
        entered.set()
        assert release.wait(5)
        active[0] = False
        return []

    case.ui.serving = SimpleNamespace(live=live)
    monkeypatch.setattr(preparation.llama, 'ensure_server', lambda *args, **kwargs: installs.append('runtime'))
    row = preparation.service(case.ui).start(case.request, binding=BINDING, current=lambda: active[0])
    assert entered.wait(5)
    release.set()
    assert finish(case.ui, row['id'])['state'] == 'interrupted'
    assert installs == [] and case.starts == []
