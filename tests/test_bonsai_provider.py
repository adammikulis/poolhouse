"""Prism-only tensor formats select the fixed verified runtime before model launch."""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import struct
import subprocess
import tarfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import leased

from poolhouse.serve import grant, llamacpp_provider as provider, provider_archive
from poolhouse.serve.backend import LlamaServerBackend, ServerFailed, ServerSpec
from poolhouse.serve.binary import BinaryNotFound
from poolhouse.serve.manager import ServerManager


def weights(path, kind=142):
    name = b'output.weight'
    path.write_bytes(b'GGUF' + struct.pack('<IQQQ', 3, 1, 0, len(name)) + name
                     + struct.pack('<IQIQ', 1, 32, kind, 0) + bytes(1024))
    return path


def test_actual_pq2_type_never_selects_stock_runtime(tmp_path):
    model = weights(tmp_path / 'weights')
    stock = LlamaServerBackend(binary=tmp_path / 'stock-llama-server')
    chosen = ServerManager(stock, state_file=tmp_path / 'servers.json').backend_for(ServerSpec(model=model))
    assert getattr(chosen, 'provider', '') == 'prism', 'PQ2 tensor type142 needs its verified Prism runtime'


pytest_plugins = ['serve_node']
pytestmark = pytest.mark.usefixtures('serve_leases')


def archive(path, entries):
    with tarfile.open(path, 'w:gz') as output:
        for name, content, target in entries:
            info = tarfile.TarInfo('fixture/' + name)
            info.mode = 0o755 if name == 'llama-server' else 0o644
            if target is not None:
                info.type, info.linkname = tarfile.SYMTYPE, target
                output.addfile(info)
            else:
                info.size = len(content)
                output.addfile(info, io.BytesIO(content))
    return path


@pytest.fixture
def release(tmp_path, monkeypatch):
    compressed = archive(tmp_path / 'input.tar.gz', [
        ('llama-server', b'fixture server', None), ('LICENSE', b'fixture licence', None),
        ('libcore.1.dylib', b'fixture library', None), ('libcore.0.dylib', b'', 'libcore.1.dylib'),
        ('libcore.dylib', b'', 'libcore.0.dylib')])
    monkeypatch.setattr(provider, 'PREFIX', 'fixture')
    monkeypatch.setattr(provider, 'ARCHIVE_SHA256', hashlib.sha256(compressed.read_bytes()).hexdigest())
    root = tmp_path / 'providers' / 'fixed'
    monkeypatch.setattr(provider, 'directory', lambda: root)
    monkeypatch.setattr(provider, 'verify_libraries', lambda *_: None)
    downloaded = []
    def download(url, target, want):
        assert grant.granted()
        assert url == provider.URL and want.sha256 == provider.ARCHIVE_SHA256 and want.require_digest
        target.parent.mkdir(parents=True)
        shutil.copyfile(compressed, target)
        target.with_name(target.name + '.fetch.json').write_text('{}')
        downloaded.append(url)
    monkeypatch.setattr(provider.net, 'download', download)
    return compressed, root, downloaded


@pytest.mark.parametrize('kind', [142, 143])
def test_only_format_metadata_selects_provider_and_lookup_never_installs(tmp_path, release, kind):
    _, root, downloaded = release
    model = weights(tmp_path / 'ordinary-name', kind)
    manager = ServerManager(state_file=tmp_path / 'servers.json')
    selected = manager.backend_for(ServerSpec(model=model))
    assert selected.provider == 'prism'
    assert manager.backend_for(ServerSpec(model=model)) is selected
    with pytest.raises(BinaryNotFound):
        assert selected.binary
    assert not root.exists() and downloaded == []
    stock_model = weights(tmp_path / 'Bonsai-PQ2.gguf', 2)
    assert manager.backend_for(ServerSpec(model=stock_model)) is manager.backend


class Captured(RuntimeError):
    pass


def prepare(tmp_path, monkeypatch, release, model=None):
    model = model or weights(tmp_path / 'model')
    def existing_start(self, spec, *, lease, **options):
        grant.require(lease, provider.Lease)
        assert self.provider == 'prism' and self.binary == release[1] / 'llama-server'
        raise Captured('existing admitted launch reached')
    monkeypatch.setattr(LlamaServerBackend, 'start', existing_start)
    with pytest.raises(Captured, match='existing admitted launch'):
        leased(LlamaServerBackend(binary=tmp_path / 'unselected-stock'), ServerSpec(model=model),
               preflight=False, check_flags=False, warmup_request=False)
    return model


def test_broker_prepares_once_and_materializes_owned_regular_library_aliases(tmp_path, monkeypatch, release):
    model = prepare(tmp_path, monkeypatch, release)
    prepare(tmp_path, monkeypatch, release, model)
    _, root, downloaded = release
    assert len(downloaded) == 1
    for name in ('libcore.dylib', 'libcore.0.dylib', 'libcore.1.dylib'):
        assert (root / name).read_bytes() == b'fixture library'
        assert not (root / name).is_symlink() and (root / name).stat().st_nlink == 1
    assert not list(root.glob('*.fetch.json'))


@pytest.mark.parametrize('change', ['library', 'extra', 'link', 'archive'])
def test_reuse_refuses_changed_code_and_never_downloads_over_it(tmp_path, monkeypatch, release, change):
    prepare(tmp_path, monkeypatch, release)
    _, root, downloaded = release
    if change == 'library':
        (root / 'libcore.dylib').write_bytes(b'changed')
    elif change == 'extra':
        (root / 'plugin.dylib').write_bytes(b'injected')
    elif change == 'link':
        (root / 'libcore.dylib').unlink()
        (root / 'libcore.dylib').symlink_to(root / 'libcore.1.dylib')
    else:
        (root / 'release.tar.gz').write_bytes(b'changed')
    with pytest.raises(ServerFailed):
        provider.existing()
    assert len(downloaded) == 1


def test_preparation_without_actual_broker_grant_never_writes(tmp_path, release):
    with pytest.raises(grant.NoGrant):
        provider.PrismBackend().start(ServerSpec(model=weights(tmp_path / 'model')), lease=None)
    assert not release[1].parent.exists()


def test_changed_model_during_preparation_never_reaches_launch(tmp_path, monkeypatch, release):
    model = weights(tmp_path / 'model')
    original = provider.net.download
    def changing(*args):
        original(*args)
        model.write_bytes(model.read_bytes() + b'changed')
    monkeypatch.setattr(provider.net, 'download', changing)
    with pytest.raises(ServerFailed, match='model changed'):
        prepare(tmp_path, monkeypatch, release, model)


@pytest.mark.parametrize('entries', [
    [('llama-server', b'a', None), ('llama-server', b'b', None)],
    [('llama-server', b'a', None), ('../escape', b'b', None)],
    [('llama-server', b'a', None), ('liba.dylib', b'', '../outside')],
    [('llama-server', b'a', None), ('liba.dylib', b'', 'libb.dylib'), ('libb.dylib', b'', 'liba.dylib')],
    [('llama-server', b'a', None), ('liba.dylib', b'', 'libmissing.dylib')],
])
def test_malformed_archive_cannot_supply_executable_or_library_paths(tmp_path, entries):
    path = archive(tmp_path / 'bad.tar.gz', entries)
    with pytest.raises(ValueError):
        provider_archive.contents(path, hashlib.sha256(path.read_bytes()).hexdigest(), 'fixture')


def test_fixed_provider_strips_dynamic_loader_injection(monkeypatch, release):
    for name in ('DYLD_INSERT_LIBRARIES', 'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH',
                 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'LD_LIBRARY_PATH_ORIG'):
        monkeypatch.setenv(name, '/untrusted/library')
    assert not any(name.startswith(('DYLD_', 'LD_')) for name in provider.environment())


def test_official_archive_dynamic_closure_is_inertly_validated(tmp_path, monkeypatch):
    source = os.environ.get('POOLHOUSE_PRISM_ARCHIVE')
    if not source:
        pytest.skip('provide the independently pinned release archive for artifact inspection')
    payload = tmp_path / 'provider'
    payload.mkdir(mode=0o700)
    compressed = payload / 'release.tar.gz'
    shutil.copyfile(source, compressed)
    from poolhouse.net.sniff import sniff
    verdict = sniff(compressed, 'archive', library_links=True)
    assert verdict.ok, verdict.problems
    entries = provider_archive.contents(compressed, provider.ARCHIVE_SHA256, provider.PREFIX)
    for name, entry in entries.items():
        target = payload / name
        target.write_bytes(entry.data)
        target.chmod(0o700 if entry.executable else 0o600)
    assert provider_archive.verify(payload, entries) == payload / 'llama-server'
    provider.verify_libraries(payload, entries, provider.environment())
    isolated = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(tmp_path),
                'TMPDIR': str(tmp_path), 'POOLHOUSE_HOME': str(tmp_path / 'state'),
                'POOLHOUSE_AUTHORITY_FLOOR': 'person', 'LANG': 'en_US.UTF-8'}
    information = subprocess.run([str(payload / 'llama-server'), '--help'], cwd=tmp_path,
                                 env=isolated, capture_output=True, text=True, timeout=20)
    assert information.returncode == 0, information.stdout + information.stderr
    assert '--model' in information.stdout + information.stderr


def test_binary_information_probes_strip_loader_overrides_and_malicious_extras(tmp_path, monkeypatch):
    from poolhouse.serve import backend
    from poolhouse.serve.binary import child_env
    touched = []
    monkeypatch.setenv('DYLD_INSERT_LIBRARIES', '/hostile/injected.dylib')
    monkeypatch.setenv('LD_PRELOAD', '/hostile/injected.so')
    def information(*args, **kwargs):
        touched.append(kwargs['env'])
        assert not any(key.startswith(('DYLD_', 'LD_')) for key in kwargs['env'])
        return type('Result', (), {'returncode': 0, 'stdout': 'fixture help', 'stderr': ''})()
    monkeypatch.setattr(backend.subprocess, 'run', information)
    binary = tmp_path / 'probe'
    binary.write_bytes(b'fixture')
    backend.help_of(binary)
    backend.devices_of(binary)
    assert len(touched) == 2
    extra = {'DYLD_INSERT_LIBRARIES': '/hostile/extra', 'LD_PRELOAD': '/hostile/extra'}
    assert not any(key.startswith(('DYLD_', 'LD_')) for key in child_env(binary, extra))
    assert os.environ['LD_PRELOAD'] == '/hostile/injected.so'


def test_provider_rejects_plugin_environment_but_preserves_fixed_slot_debug(monkeypatch, release):
    monkeypatch.setenv('GGML_BACKEND_PATH', '/hostile/plugin')
    monkeypatch.setenv('LLAMA_ARG_MODEL_URL', 'https://unselected.invalid/model')
    result = provider.environment({'LLAMA_SERVER_SLOTS_DEBUG': '1', 'GGML_BACKEND_PATH': '/hostile/extra'})
    assert result['LLAMA_SERVER_SLOTS_DEBUG'] == '1'
    assert not any(key.startswith(('GGML_', 'DYLD_', 'LD_')) for key in result)
    assert 'LLAMA_ARG_MODEL_URL' not in result


def test_archive_parser_consumes_only_the_bytes_whose_digest_was_checked(tmp_path, monkeypatch):
    path = archive(tmp_path / 'input.tar.gz', [('llama-server', b'original', None)])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    opening = provider_archive.tarfile.open
    def replaced(*args, **kwargs):
        path.write_bytes(b'unverified replacement')
        return opening(*args, **kwargs)
    monkeypatch.setattr(provider_archive.tarfile, 'open', replaced)
    assert provider_archive.contents(path, digest, 'fixture')['llama-server'].data == b'original'
    with pytest.raises(ValueError, match='pinned release'):
        provider_archive.contents(path, digest, 'fixture')


def test_prism_selected_model_still_applies_strict_iq_policy(tmp_path, monkeypatch):
    from poolhouse.serve import quant_guard
    from poolhouse.serve.manager import Starting
    model = tmp_path / 'mixed'
    table = b''
    for name, count, kind, offset in [(b'output.weight', 256, 142, 0),
                                       (b'blk.0.weight', 25600, 23, 128)]:
        table += struct.pack('<Q', len(name)) + name + struct.pack('<IQIQ', 1, count, kind, offset)
    header = b'GGUF' + struct.pack('<IQQ', 3, 2, 0) + table
    model.write_bytes(header + bytes((-len(header)) % 32) + bytes(128 + 13600))
    monkeypatch.setattr(quant_guard, 'is_apple_silicon', lambda: True)
    manager = ServerManager(state_file=tmp_path / 'servers.json')
    assert manager.backend_for(ServerSpec(model=model)).provider == 'prism'
    with pytest.raises(quant_guard.BlockedQuant, match='IQ4_XS'):
        manager._quant_guard(ServerSpec(model=model), Starting(iq='block'))


def test_model_changed_during_format_scan_never_fills_format_cache(tmp_path, monkeypatch):
    model = weights(tmp_path / 'changing')
    original = provider.scan_gguf
    def changing(path, **options):
        result = original(path, **options)
        path.write_bytes(path.read_bytes() + b'changed')
        return result
    provider._requires.cache_clear()
    monkeypatch.setattr(provider, 'scan_gguf', changing)
    with pytest.raises(ServerFailed, match='model changed'):
        provider.required(model)
    assert provider._requires.cache_info().currsize == 0


def test_provider_refuses_an_alias_in_any_managed_ancestor(tmp_path, monkeypatch, release):
    prepare(tmp_path, monkeypatch, release)
    root = release[1]
    parent = root.parent
    moved = tmp_path / 'moved'
    parent.rename(moved)
    parent.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ServerFailed, match='untrusted directory or alias'):
        provider.existing()


def test_concurrent_admitted_preparation_downloads_one_fixed_dependency(tmp_path, monkeypatch, release):
    model = weights(tmp_path / 'model')
    def existing_start(self, spec, *, lease, **options):
        grant.require(lease, provider.Lease)
        assert self.binary == release[1] / 'llama-server'
        raise Captured('existing admitted launch reached')
    monkeypatch.setattr(LlamaServerBackend, 'start', existing_start)
    def request(_):
        with pytest.raises(Captured):
            leased(LlamaServerBackend(), ServerSpec(model=model), preflight=False,
                   check_flags=False, warmup_request=False)
    with ThreadPoolExecutor(max_workers=2) as threads:
        list(threads.map(request, range(2)))
    assert len(release[2]) == 1


def test_archive_rejects_nonlibrary_aliases_even_when_not_installed(tmp_path):
    path = archive(tmp_path / 'input.tar.gz', [('llama-server', b'fixed', None),
                                             ('other-tool', b'', '../outside')])
    with pytest.raises(ValueError, match='unexpected executable alias'):
        provider_archive.contents(path, hashlib.sha256(path.read_bytes()).hexdigest(), 'fixture')
