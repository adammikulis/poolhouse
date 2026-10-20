"""Only retained native launch evidence can identify loaded model bytes."""
import json
import os
import select
import shutil
import struct
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import leased

from poolhouse.serve import broker_wire, startup
from poolhouse.serve.backend import ServerInfo, ServerSpec
from poolhouse.serve.broker import Broker


def test_startup_lookup_never_starts_a_broker(monkeypatch):
    calls = []
    def call(op, **options):
        calls.append((op, options))
        return {'receipt': None}
    monkeypatch.setattr(broker_wire, 'call', call)
    assert broker_wire.startup(32109) is None
    assert calls == [('startup', {'port': 32109, 'start': False})]


pytest_plugins = ['serve_node', 'native_server_kit']


def arguments(spec, binary):
    return [str(binary), '--port', str(spec.port), '-m', str(spec.model), '-c', str(spec.context), '-np', str(spec.parallel),
            '--jinja', *(['--chat-template-file', str(spec.chat_template_file)] if spec.chat_template_file else [])]


@pytest.fixture(autouse=True)
def metadata_verifier(monkeypatch):
    monkeypatch.setattr(startup, 'verify_libraries', lambda *_: None)


@pytest.fixture
def native(tmp_path, native_executable):
    from poolhouse.serve.ports import free_port
    binary = tmp_path / 'runtime'
    shutil.copy2(native_executable, binary)
    model = tmp_path / 'weights.gguf'
    model.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    template = tmp_path / 'template.jinja'
    template.write_text('fixed template')
    library = tmp_path / 'libengine.dylib'
    library.write_bytes(b'fixed library')
    spec = ServerSpec(model=model, port=free_port(), context=2048, chat_template_file=str(template))
    argv = [*arguments(spec, binary), '--api-key', 'private-value']
    process = subprocess.Popen(argv, cwd=tmp_path, stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 3)[0]
        port = int(process.stdout.readline())
        assert port == spec.port
        evidence = startup.capture(spec, binary, argv)
        assert evidence is not None
        proof = evidence.bind(process, port, 'llama.cpp')
        assert proof is not None
        info = ServerInfo(f'http://127.0.0.1:{port}', port, process.pid, 'llama.cpp',
                          process=process, startup=proof)
        yield info, spec, binary, library, template
    finally:
        process.terminate()
        process.wait(timeout=3)
        process.stdout.close()


def test_retained_actual_process_and_listener_prove_exact_inputs(native):
    info, spec, binary, _, _ = native
    receipt = startup.retained(info)
    assert receipt and receipt['port'] == info.port and receipt['pid'] == info.pid
    assert receipt['settings']['context'] == 2048
    assert receipt['artifacts']['model'][0]['path'] == str(spec.model)
    assert receipt['runtime']['binary']['path'] == str(binary)
    assert len(receipt['runtime']['parser']) == 64
    assert 'private-value' not in json.dumps(receipt)
    assert startup.retained(info)['id'] == receipt['id']
    holder = SimpleNamespace(_cond=threading.Condition(),
                             servers={info.port: SimpleNamespace(info=info, ours=True, unmanaged=False)})
    assert Broker.startup(holder, info.port) == receipt
    assert Broker.startup(holder, info.port + 1) is None


@pytest.mark.parametrize('component', ['model', 'template', 'library', 'binary', 'parser', 'addition'])
def test_changed_component_never_inherits_loaded_readiness(native, monkeypatch, component):
    info, spec, binary, library, template = native
    if component == 'parser':
        monkeypatch.setattr(startup.chat.Client, 'chat', lambda *_: None)
    elif component == 'addition':
        (binary.parent / 'libextra.dylib').write_bytes(b'new library')
    else:
        path = {'model': Path(spec.model), 'template': template, 'library': library, 'binary': binary}[component]
        path.write_bytes(path.read_bytes() + b'changed')
    assert startup.retained(info) is None
    assert info.process.poll() is None


@pytest.mark.parametrize('change', ['adopted', 'pid', 'port', 'process', 'proof', 'birth', 'command', 'socket'])
def test_no_adoption_or_forged_process_evidence(native, change):
    info = native[0]
    if change in ('birth', 'command', 'socket'):
        proof = replace(info.startup, **{'birth': {'started': 0}, 'command': {'cmdline': 'forged'},
                                        'socket': {'port': info.port + 1}}[change])
        info = replace(info, startup=proof)
    else:
        info = replace(info, **{'adopted': {'adopted': True}, 'pid': {'pid': os.getpid()},
                               'port': {'port': info.port + 1}, 'process': {'process': None},
                               'proof': {'startup': {'id': 'forged'}}}[change])
    assert startup.retained(info) is None


def test_dead_process_or_missing_retained_owner_never_reuses_registry(native):
    info = native[0]
    informational = broker_wire.info_dict(info)
    assert 'startup' not in informational and 'process' not in informational
    restored = ServerInfo(**broker_wire.info_fields({**informational, 'startup': {'id': 'forged'}}))
    assert startup.retained(restored) is None
    info.process.terminate()
    info.process.wait(timeout=3)
    assert startup.retained(info) is None


def test_capture_changed_before_health_produces_no_receipt(native):
    info, spec, binary, _, _ = native
    evidence = startup.capture(spec, binary, arguments(spec, binary))
    Path(spec.model).write_bytes(b'changed')
    assert evidence.bind(info.process, info.port, info.backend) is None


def test_missing_shards_never_produce_partial_artifact_identity(tmp_path):
    model = tmp_path / 'model-00001-of-00002.gguf'
    model.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    binary = tmp_path / 'runtime'
    binary.write_bytes(b'runtime')
    assert startup.capture(ServerSpec(model=model), binary, ['runtime']) is None


@pytest.mark.usefixtures('serve_leases')
def test_native_broker_launch_retains_receipt_after_actual_health(tmp_path, monkeypatch, native_executable):
    from poolhouse.serve.backend import LlamaServerBackend
    from poolhouse.serve.build_platform import verify_libraries
    monkeypatch.setattr(startup, 'verify_libraries', verify_libraries)
    binary = tmp_path / 'fixture-server'
    shutil.copy2(native_executable, binary)
    model = tmp_path / 'native.gguf'
    model.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    info = leased(LlamaServerBackend(binary=binary), ServerSpec(model=model, context=2048),
                  check_flags=False, preflight=False, warmup_request=False)
    try:
        receipt = startup.retained(info)
        assert receipt and receipt['artifacts']['model'][0]['path'] == str(model)
        assert receipt['runtime']['binary']['path'] == str(binary)
        assert receipt['settings']['context'] == 2048
    finally:
        info.process.terminate()
        info.process.wait(timeout=3)


def test_unverified_runtime_dependency_or_plugin_cannot_produce_receipt(native, monkeypatch):
    info, spec, binary, _, _ = native
    def invalid(*_):
        raise ValueError('external library dependency')
    monkeypatch.setattr(startup, 'verify_libraries', invalid)
    assert startup.capture(spec, binary, arguments(spec, binary)) is None
    assert startup.capture(spec, binary, arguments(spec, binary), environment={'GGML_BACKEND_PATH': '/unverified'}) is None
    assert info.process.poll() is None


def test_parser_configuration_change_invalidates_receipt(native, monkeypatch):
    info = native[0]
    monkeypatch.setattr(startup.families, 'KNOWN', ())
    assert startup.retained(info) is None


@pytest.mark.parametrize('change', ['runtime', 'model', 'context', 'template', 'jinja'])
def test_effective_argv_must_match_captured_components(native, change):
    _, spec, binary, _, template = native
    argv = arguments(spec, binary)
    if change == 'runtime':
        argv[0] = sys.executable
    elif change == 'model':
        argv[argv.index('-m') + 1] = str(template)
    elif change == 'context':
        argv += ['--ctx-size', '65536']
    elif change == 'template':
        argv += ['--chat-template-file', str(template)]
    else:
        argv += ['--no-jinja']
    assert startup.capture(spec, binary, argv) is None


def test_model_fifo_is_refused_without_blocking_metadata_open(tmp_path):
    path = tmp_path / 'model-fifo'
    os.mkfifo(path)
    code = """import sys
sys.path.insert(0, sys.argv[1])
from poolhouse.serve.startup import _models
try:
    _models('model', sys.argv[2])
except ValueError as exc:
    assert 'regular file' in str(exc)
else:
    raise AssertionError('FIFO accepted as model bytes')
"""
    done = subprocess.run([sys.executable, '-B', '-I', '-c', code,
                           str(Path(startup.__file__).parents[2]), str(path)], cwd=tmp_path,
                          capture_output=True, text=True, timeout=3)
    assert done.returncode == 0, done.stdout + done.stderr


def test_pre_spawn_recheck_and_complete_component_inventory(native):
    info, spec, binary, _, template = native
    draft = binary.parent / 'draft.gguf'
    draft.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    projector = binary.parent / 'projector.gguf'
    projector.write_bytes(b'GGUF' + struct.pack('<IQQ', 3, 0, 0))
    lookup = binary.parent / 'lookup.cache'
    lookup.write_bytes(b'fixed lookup')
    spec = replace(spec, draft=str(draft), mmproj=str(projector), lookup_static=str(lookup))
    argv = [*arguments(spec, binary), '-md', str(draft), '--mmproj', str(projector),
            '--lookup-cache-static', str(lookup)]
    evidence = startup.capture(spec, binary, argv)
    assert evidence is not None and startup.current(evidence) is evidence
    assert {item.role for item in evidence.files} >= {'model', 'draft', 'projector', 'template', 'lookup'}
    assert next(item for item in evidence.files if item.role == 'template').path == str(template)
    assert evidence.bind(info.process, info.port, info.backend).said() is None
    draft.write_bytes(b'changed')
    assert startup.current(evidence) is None
    assert evidence.bind(info.process, info.port, info.backend) is None


def test_effective_runtime_is_checked_by_metadata_owner(native, monkeypatch):
    _, spec, binary, library, _ = native
    seen = []
    monkeypatch.setattr(startup, 'verify_libraries', lambda root, names, env: seen.append((root, names, env)))
    assert startup.capture(spec, binary, arguments(spec, binary), environment={'PATH': '/usr/bin'})
    assert seen == [(binary.parent, {binary.name, library.name}, {'PATH': '/usr/bin'})]


def test_readonly_lookup_ignores_forged_registry_and_unmanaged_owner(native):
    info = native[0]
    holder = SimpleNamespace(_cond=threading.Condition(),
                             servers={info.port: SimpleNamespace(info=info, ours=False, unmanaged=True)})
    assert Broker.startup(holder, info.port) is None
    holder.servers[info.port] = SimpleNamespace(info=None, ours=True, unmanaged=False)
    assert Broker.startup(holder, info.port) is None



def test_unknown_executable_has_no_receipt_and_never_probes_metadata(native, monkeypatch):
    _, spec, binary, _, _ = native
    binary.write_bytes(b'#!/bin/sh\nexit 0\n')
    def unexpected(*_):
        raise AssertionError('unknown binary must not run a metadata probe')
    monkeypatch.setattr(startup, 'verify_libraries', unexpected)
    assert startup.capture(spec, binary, arguments(spec, binary)) is None



def test_invalid_grant_cannot_enter_backend_preparation(tmp_path, monkeypatch):
    from poolhouse.serve import grant, llamacpp_provider
    def unexpected(*_):
        raise AssertionError('preparation must not run without actual admission')
    monkeypatch.setattr(llamacpp_provider.PrismBackend, 'preparing', unexpected)
    with pytest.raises(grant.NoGrant):
        llamacpp_provider.PrismBackend().start(ServerSpec(model=tmp_path / 'unread'), lease=None)



def test_parser_digest_is_code_owned_and_independent_of_install_prefix(monkeypatch):
    before = startup.parser_identity()
    original = startup.families.split_inline
    monkeypatch.setattr(original, '__code__', original.__code__.replace(co_filename='/other/immutable/prefix.py'))
    assert startup.parser_identity() == before
    monkeypatch.setattr(original, '__defaults__', ('changed',))
    assert startup.parser_identity() != before
