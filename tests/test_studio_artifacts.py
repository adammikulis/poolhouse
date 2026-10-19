"""Manifest allowlists and current authority on Studio artifact downloads."""

import hashlib
import io
import json
import os
from types import SimpleNamespace

import pytest

from poolhouse.fleet.studio_artifacts import artifact_files, open_artifact, stream_artifact


@pytest.fixture
def exported(tmp_path):
    ident = "a" * 32
    node = {"id": "export", "type": "export", "version": 1}
    folder = tmp_path / "studio" / ident / hashlib.sha256(b"export").hexdigest()[:24] / "export"
    folder.mkdir(parents=True)
    (folder / "export.json").write_text(json.dumps({"files": ["model.safetensors", "state.json"]}))
    (folder / "model.safetensors").write_bytes(b"weights")
    (folder / "state.json").write_text('{}')
    (folder / "private.txt").write_text('excluded')
    run = {"id": ident, "graph": {"nodes": [node]}, "nodes": {"export": {
        "state": "done", "outputs": {"artifact": {"kind": "artifact", "path": str(folder)}}}}}
    return tmp_path, run, folder


def test_export_lists_only_declared_files_and_streams_attachment(exported):
    root, run, _ = exported
    files = artifact_files(run, root, lambda node, outputs: True)
    assert {f['name'] for f in files} == {"export.json", "model.safetensors", "state.json"}
    assert all(f['href'].startswith('/ui/studio/runs/' + run['id']) for f in files)
    opened, name = open_artifact(run, root, {'node':'export', 'port':'artifact', 'name':'model.safetensors'}, lambda node, outputs: True)
    headers = {}
    response = []
    handler = SimpleNamespace(command='GET', wfile=io.BytesIO(), send_response=response.append,
                              send_header=lambda name, value: headers.update({name: value}), end_headers=lambda: None)
    stream_artifact(handler, opened, name)
    assert response == [200]
    assert handler.wfile.getvalue() == b'weights'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert headers['Content-Disposition'].startswith('attachment;')
    assert opened.closed


def test_revoked_authorization_blocks_download_after_listing(exported):
    root, run, _ = exported
    artifact_files(run, root, lambda node, outputs: True)
    with pytest.raises(PermissionError):
        open_artifact(run, root, {'node':'export', 'port':'artifact', 'name':'model.safetensors'}, lambda node, outputs: False)


@pytest.mark.parametrize('name', ['../private.txt', '/etc/passwd', 'private.txt'])
def test_unlisted_and_traversal_names_are_refused(exported, name):
    root, run, _ = exported
    with pytest.raises(KeyError):
        open_artifact(run, root, {'node':'export', 'port':'artifact', 'name':name}, lambda node, outputs: True)


@pytest.mark.parametrize('link', ['symbolic', 'hard'])
def test_linked_artifact_files_are_refused(exported, link):
    root, run, folder = exported
    outside = root / 'outside'
    outside.write_text('private')
    target = folder / 'model.safetensors'
    target.unlink()
    if link == 'symbolic':
        target.symlink_to(outside)
    else:
        os.link(outside, target)
    with pytest.raises((ValueError, OSError)):
        artifact_files(run, root, lambda node, outputs: True)


def test_forged_manifest_file_and_external_output_are_refused(exported):
    root, run, folder = exported
    (folder / 'export.json').write_text(json.dumps({'files': ['../private.txt']}))
    with pytest.raises(ValueError, match='manifest'):
        artifact_files(run, root, lambda node, outputs: True)
    run['nodes']['export']['outputs']['artifact']['path'] = str(root)
    with pytest.raises(ValueError, match='belong'):
        artifact_files(run, root, lambda node, outputs: True)


def test_arbitrary_structured_json_and_dataset_paths_are_not_artifacts(exported):
    root, run, folder = exported
    run['graph']['nodes'][0].update(type='generate')
    run['nodes']['export']['outputs'] = {'response': {'kind': 'artifact', 'path': str(folder)}, 'text': ''}
    assert artifact_files(run, root, lambda node, outputs: True) == []


def test_cached_outputs_do_not_enumerate_other_runs_paths(exported):
    root, run, _ = exported
    run['nodes']['export']['reused_from'] = 'b' * 32
    run['nodes']['export']['outputs']['artifact']['path'] = str(root / 'other-run')
    assert artifact_files(run, root, lambda node, outputs: True) == []


def test_node_output_directory_symlink_is_refused(exported):
    root, run, folder = exported
    moved = root / 'moved'
    folder.rename(moved)
    folder.symlink_to(moved, target_is_directory=True)
    with pytest.raises(OSError):
        artifact_files(run, root, lambda node, outputs: True)


def test_report_and_gym_policy_are_owned_fixed_files(exported):
    root, run, folder = exported
    node = run['graph']['nodes'][0]
    node['type'] = 'report'
    report = folder / 'report.json'
    report.write_text('{"result":true}')
    run['nodes']['export']['outputs']['artifact']['path'] = str(report)
    assert [f['name'] for f in artifact_files(run, root, lambda node, outputs: True)] == ['report.json']
    node['type'] = 'gym.train'
    (folder / 'policy.zip').write_bytes(b'policy')
    (folder / 'policy-manifest.json').write_text('{}')
    run['nodes']['export']['outputs'] = {'model': {'kind': 'model', 'path': str(folder / 'policy.zip')}}
    assert {f['name'] for f in artifact_files(run, root, lambda node, outputs: True)} == {'policy.zip', 'policy-manifest.json'}
