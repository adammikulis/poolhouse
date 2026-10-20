"""Current transfer ownership and actual file identity, without model compute."""
import hashlib
import http.server
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import threaded_server

from poolhouse import hub
from poolhouse.fleet import models as owning
from poolhouse.fleet.models import Downloads, Model, Models, ModelTransfer
from poolhouse.fleet.weights import ModelError
from poolhouse.hub import hfstore
from tests.net_site import gguf_bytes


@pytest.fixture
def store(tmp_path):
    return Models([tmp_path / 'models'], tmp_path / 'models', sources=lambda: 'internet')


def model(path, body=b'a' * 4096):
    path.write_bytes(body)
    info = path.stat()
    return Model(path.name, path, info.st_size, info.st_mtime)


def cached(tmp_path, monkeypatch):
    body = gguf_bytes(extra=1 << 20)
    path = tmp_path / hashlib.sha256(body).hexdigest()
    path.write_bytes(body)
    monkeypatch.setattr(hub, 'pull', lambda *args, **kwargs: path)
    return body, path


def test_digest_cache_detects_same_size_rewrite_with_restored_mtime(store, tmp_path):
    selected = model(tmp_path / 'm.gguf')
    first = store.digest(selected)
    before = selected.path.stat()
    selected.path.write_bytes(b'b' * selected.size)
    os.utime(selected.path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert store.digest(selected) == hashlib.sha256(b'b' * selected.size).hexdigest() != first


def test_cached_digest_still_requires_current_before_open(store, tmp_path):
    selected = model(tmp_path / 'm.gguf')
    store.digest(selected)
    denied = PermissionError('current caller revoked')

    def current():
        raise denied

    with pytest.raises(PermissionError) as caught:
        store.digest(selected, check=current)
    assert caught.value is denied


def test_digest_replacement_during_read_is_not_cached(store, tmp_path):
    selected = model(tmp_path / 'm.gguf')
    checks = []

    def current():
        checks.append(1)
        if len(checks) == 2:
            selected.path.unlink()
            selected.path.write_bytes(b'a' * selected.size)

    with pytest.raises(ModelError, match='changed'):
        store.digest(selected, check=current)
    assert store._digests == {}


def test_digest_fifo_is_refused_without_blocking(store, tmp_path):
    path = tmp_path / 'm.gguf'
    os.mkfifo(path)
    with pytest.raises(ModelError, match='regular'):
        store.digest(Model(path.name, path, 0, 0))


def test_internet_publication_never_adopts_concurrent_equal_bytes(store, tmp_path, monkeypatch):
    body, _ = cached(tmp_path, monkeypatch)
    original = hfstore.Placement.publish
    target = store.store / 'm.gguf'
    replacement = []

    def publish(self, **kwargs):
        result = original(self, **kwargs)
        target.unlink()
        target.write_bytes(body)
        replacement.append(target.stat().st_ino)
        return result

    monkeypatch.setattr(hfstore.Placement, 'publish', publish)
    with pytest.raises(PermissionError, match='target changed'):
        store.ensure('m.gguf', source='hf:maker/model/m.gguf')
    assert target.stat().st_ino == replacement[0] and target.read_bytes() == body


def test_revoke_during_model_metadata_removes_owned_target_only(store, tmp_path, monkeypatch):
    cached(tmp_path, monkeypatch)
    allowed = [True]
    monkeypatch.setattr(owning.model_components, 'remember', lambda *args: allowed.__setitem__(0, False))
    with pytest.raises(PermissionError):
        store.ensure('m.gguf', source='hf:maker/model/m.gguf',
                     transfer=ModelTransfer(current=lambda: allowed[0]))
    assert not (store.store / 'm.gguf').exists()


def test_cancelled_waiter_does_not_cancel_shared_actual_transfer(store, tmp_path, monkeypatch):
    _, path = cached(tmp_path, monkeypatch)
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    allowed, checks = [True], []

    def pull(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return path

    def current():
        checks.append(1)
        if len(checks) >= 3:
            waiting.set()
        return allowed[0]

    monkeypatch.setattr(hub, 'pull', pull)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(store.ensure, 'm.gguf', source='hf:maker/model/m.gguf')
        try:
            assert entered.wait(5)
            second = workers.submit(store.ensure, 'm.gguf', source='hf:maker/model/m.gguf',
                                    transfer=ModelTransfer(current=current))
            assert waiting.wait(5)
            allowed[0] = False
            with pytest.raises(PermissionError):
                second.result(5)
            assert not first.done()
        finally:
            release.set()
        assert first.result(5).path == store.store / 'm.gguf'


def test_downloads_concurrent_exact_start_attaches_one_owned_row(store, monkeypatch):
    downloads = Downloads(store)
    original = owning.Getting
    release, running = threading.Event(), threading.Event()
    executions, owned = [], []

    def getting(**kwargs):
        time.sleep(0.03)  # Expose the old gap between lookup and insertion.
        return original(**kwargs)

    def run(row, key, autodownload):
        owned.append(threading.current_thread())
        executions.append(row.id)
        running.set()
        assert release.wait(5)

    monkeypatch.setattr(owning, 'Getting', getting)
    monkeypatch.setattr(downloads, '_run', run)
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            rows = list(workers.map(lambda _: downloads.start('m.gguf', source='hf:maker/model/m.gguf'), range(4)))
        assert running.wait(5)
        assert len({row.id for row in rows}) == len(executions) == len(downloads.getting) == 1
    finally:
        release.set()
        for thread in owned:
            thread.join(5)
            assert not thread.is_alive()


def test_download_start_does_not_adopt_changed_source_or_mutated_components(store, monkeypatch):
    downloads = Downloads(store)
    monkeypatch.setattr(downloads, '_run', lambda *args: None)
    components = [{'name': 'draft.gguf'}]
    row = downloads.start('m.gguf', source='hf:maker/model/m.gguf', components=components)
    components[0]['name'] = 'changed.gguf'
    assert row.components == [{'name': 'draft.gguf'}]
    with pytest.raises(ModelError, match='different transfer'):
        downloads.start('m.gguf', source='hf:maker/other/m.gguf', components=row.components)


def test_exact_installed_companion_is_reused_without_substring_fallback(store, monkeypatch):
    store.store.mkdir()
    body = gguf_bytes(extra=1 << 20)
    selected = model(store.store / 'mmproj-m.gguf', body)
    monkeypatch.setattr(hub, 'pull', lambda *args, **kwargs: pytest.fail('downloaded existing exact companion'))
    assert store.ensure('mmproj-m.gguf', source='hf:maker/model/mmproj-m.gguf').path == selected.path
    with pytest.raises(ModelError, match='exact model weights filename'):
        store.ensure('mmproj', source='hf:maker/model/mmproj-m.gguf')


@pytest.mark.parametrize('before', [True, False])
def test_weights_metadata_current_denial_stops_first_or_redirect_opening(monkeypatch, loopback_net, before):
    from poolhouse.fleet import weights

    allowed, seen = [not before], []
    denied = PermissionError('metadata no longer authorized')

    def current():
        if not allowed[0]:
            raise denied

    class Metadata(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append(self.path)
            allowed[0] = False
            self.send_response(302)
            self.send_header('Location', '/final')
            self.send_header('Content-Length', '0')
            self.end_headers()

    with threaded_server(Metadata) as base:
        monkeypatch.setattr(weights, 'LISTING', base + '/api/models')
        with pytest.raises(PermissionError) as caught:
            weights.reference('hf:maker/model', check=current)
    assert caught.value is denied
    assert seen == ([] if before else ['/api/models/maker/model'])


def test_preparation_reporter_composes_with_exact_true_transfer_check(tmp_path):
    from poolhouse.fleet.setup_jobs import Jobs
    from tests.test_fleet_setup_preparation_jobs import finished, request

    jobs = Jobs(tmp_path)

    def operation(reporter):
        transfer = ModelTransfer(current=reporter.check)
        assert transfer.check() is True
        transfer.progress(1, 1)
        return {'observed_current': True}

    row = jobs.prepare(request(), operation, current=lambda: True)
    worker = jobs._worker
    done = finished(jobs, row['id'])
    if worker is not None:
        worker.join(5)
        assert not worker.is_alive()
    assert done['state'] == 'done' and done['result'] == {'observed_current': True}
