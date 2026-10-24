"""Production Swift inputs and SDK changes invalidate app artifacts, never Node-only callers."""
from __future__ import annotations

import json
import subprocess

import pytest
import testnode
import testswift


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    app = root / 'app'
    (app / 'poolhouse-node').mkdir(parents=True)
    (app / 'Cargo.toml').write_text('[workspace]\n')
    (app / 'Cargo.lock').write_text('# fixture\n')
    (app / 'poolhouse-node/lib.rs').write_text('// fixture\n')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    monkeypatch.setattr(testnode, 'version', lambda *_args: 'rustc fixture')
    return app, tmp_path / 'cache'


def test_only_production_swift_inputs_are_staged_and_keyed(workspace):
    app, cache = workspace
    files = ['native-service/Inspection.swift', 'native-service/Bridge.swift',
             'native-service/generated/PassiveServiceData.swift', 'native-service/generated/PassiveServiceData.h']
    for name in [*files, 'native-service/README.md', 'native-service/InspectionVerification.swift']:
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('// initial\n')
    first = testnode.shared_build(app, root=cache)
    for name in files:
        assert (first.staged / name).read_text() == '// initial\n'
    for name in ['native-service/README.md', 'native-service/InspectionVerification.swift']:
        assert not (first.staged / name).exists()
        (app / name).write_text('// ignored diagnostic input\n')
    assert testnode.shared_build(app, root=cache) == first
    for name in files:
        before = testnode.shared_build(app, root=cache)
        (app / name).write_text('// changed production input\n')
        after = testnode.shared_build(app, root=cache)
        assert after.staged != before.staged and after.target == before.target


def test_app_sdk_salt_changes_workspace_not_shared_rust_target_and_node_does_not_probe(workspace, monkeypatch):
    app, cache = workspace
    def unexpected(_app):
        raise AssertionError('Node-only cache must not probe Swift')
    monkeypatch.setattr(testswift, 'identity', unexpected)
    node = testnode.shared_build(app, root=cache)
    one = testnode.shared_build(app, root=cache, source_key_extra='sdk-one')
    two = testnode.shared_build(app, root=cache, source_key_extra='sdk-two')
    assert len({node.staged, one.staged, two.staged}) == 3
    assert node.target == one.target == two.target


def test_swift_metadata_is_targeted_sanitized_and_cannot_fall_back_to_unknown(workspace, monkeypatch):
    app, _ = workspace
    (app / 'native-service').mkdir()
    (app / 'native-service/Bridge.swift').write_text('// fixture\n')
    (app / 'src-tauri').mkdir()
    (app / 'src-tauri/tauri.conf.json').write_text(json.dumps({'bundle': {'macOS': {'minimumSystemVersion': '12.0'}}}))
    monkeypatch.setattr(testswift.sys, 'platform', 'darwin')
    monkeypatch.setattr(testswift.platform, 'machine', lambda: 'arm64')
    monkeypatch.setenv('DYLD_INSERT_LIBRARIES', '/untrusted')
    monkeypatch.setenv('SDKROOT', '/untrusted')
    sdk = ['26A1']
    def metadata(command, **kwargs):
        assert 'DYLD_INSERT_LIBRARIES' not in kwargs['env'] and 'SDKROOT' not in kwargs['env']
        assert kwargs['timeout'] == testnode.PROBE_TIMEOUT_S
        if command[0] == '/usr/bin/swiftc':
            assert command[1:3] == ['-target', 'arm64-apple-macosx12.0']
            value = json.dumps({'compilerVersion': 'fixture Swift', 'target': {'triple': command[2]}})
        else:
            value = '/fixed/public-sdk' if command[-1] == '--show-sdk-path' else sdk[0]
        return subprocess.CompletedProcess(command, 0, value, '')
    monkeypatch.setattr(testswift.subprocess, 'run', metadata)
    first = testswift.identity(app)
    sdk[0] = '26A2'
    assert testswift.identity(app) != first
    sdk[0] = ''
    with pytest.raises(testnode.BuildError, match='unavailable'):
        testswift.identity(app)

    sdk[0] = 'unknown'
    with pytest.raises(testnode.BuildError, match='invalid'):
        testswift.identity(app)
