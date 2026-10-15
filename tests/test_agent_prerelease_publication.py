"""App publication admission, pinned build provenance and idempotent release behavior."""

import hashlib
import json
import plistlib
import subprocess
import types
import zipfile

import pytest

from poolhouse.net import github_release as net
from poolhouse.workspace import github_app, github_release as holder

VERSION = '0.3.0-dev.1'
REPO = 'demo/project'


def git(directory, *args):
    done = subprocess.run(['git', '-C', str(directory), *args], env=holder.environment(directory),
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def built(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init', '--quiet', '--initial-branch=0.3dev')
    git(root, 'remote', 'add', 'origin', f'https://github.com/{REPO}.git')
    (root / 'version.txt').write_text(VERSION)
    git(root, 'add', 'version.txt')
    git(root, '-c', 'user.name=Codex', '-c', 'user.email=codex@agents.invalid', 'commit', '-qm', 'chore: fixture')
    commit = git(root, 'rev-parse', 'HEAD')
    output = root / 'dist/0.3dev1'
    output.mkdir(parents=True)
    wheel = output / 'poolhouse-0.3.0.dev1-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('poolhouse-0.3.0.dev1.dist-info/METADATA', 'Name: poolhouse\nVersion: 0.3.0.dev1\n')
        archive.writestr('poolhouse/fleet/built-from', commit)
    app = output / 'Poolhouse-0.3dev1-macos-arm64.zip'
    with zipfile.ZipFile(app, 'w') as archive:
        archive.writestr('Poolhouse.app/Contents/Info.plist', plistlib.dumps({'CFBundleShortVersionString': VERSION}))
    manifest = {'schema': 1, 'version': VERSION, 'python_version': '0.3.0.dev1', 'source_commit': commit,
                'tag': 'v' + VERSION, 'assets': {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                                                       'bytes': p.stat().st_size} for p in (wheel, app)}}
    info = output / 'build-info.json'
    info.write_text(json.dumps(manifest))
    rows = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}' for p in (wheel, app, info)]
    (output / 'SHA256SUMS').write_text('\n'.join(rows) + '\n')
    return holder.source(root, REPO), output


@pytest.fixture
def publication(built, monkeypatch):
    selected, output = built
    monkeypatch.setattr(holder, 'authorize', lambda action: None)
    monkeypatch.setattr(holder, 'identity', lambda: github_app.Identity(REPO, 7, 'client', 'app', 'not-a-key', 9))
    monkeypatch.setattr(holder.authority, 'require', lambda *a, **k: None)
    scopes = []
    monkeypatch.setattr(github_app, 'mint', lambda who, permissions, only: scopes.append((permissions, only)) or {'token': 'fixture-token'})
    state = {'release': None, 'uploads': [], 'creates': 0, 'tag': selected.commit}

    def request(method, path, **kwargs):
        if '/git/ref/heads/' in path:
            return {'object': {'type': 'commit', 'sha': selected.commit}}
        if '/git/ref/tags/' in path:
            if state['release'] is None:
                raise net.Gone('missing')
            return {'object': {'type': 'commit', 'sha': state['tag']}}
        if method == 'GET':
            if state['release'] is None:
                raise net.Gone('missing')
            return state['release']
        state['creates'] += 1
        state['release'] = {**kwargs['payload'], 'id': 7, 'assets': [],
                            'html_url': f'https://github.com/{REPO}/releases/tag/v{VERSION}'}
        return state['release']

    def upload(repo, release_id, asset, token):
        name, body = asset
        state['uploads'].append(name)
        row = {'name': name, 'size': len(body), 'digest': 'sha256:' + hashlib.sha256(body).hexdigest(),
               'browser_download_url': f'https://github.com/{repo}/releases/download/v{VERSION}/{name}'}
        state['release']['assets'].append(row)
        return row

    monkeypatch.setattr(net, 'request', request)
    monkeypatch.setattr(net, 'upload', upload)
    return selected, output, state, scopes


def test_pinned_build_verifies_wheel_app_source_and_checksums(built):
    selected, _ = built
    manifest, assets = holder.artifacts(selected)
    assert manifest['source_commit'] == selected.commit
    assert len(assets) == 4
    assert 'body=' not in repr(next(iter(assets.values())))


def test_publication_is_exact_and_idempotent(publication):
    selected, _, state, scopes = publication
    first = holder.publish_release(selected.checkout)
    assert len(first['assets']) == 4
    assert holder.publish_release(selected.checkout) == first
    assert state['creates'] == 1 and len(state['uploads']) == 4
    assert all(scope == ({'contents': 'write', 'workflows': 'write', 'metadata': 'read'}, REPO)
               for scope in scopes)


@pytest.mark.parametrize('field,value', [('prerelease', False), ('draft', True), ('name', 'wrong'),
                                        ('target_commitish', 'b' * 40), ('tag_name', 'v0.1.0')])
def test_existing_release_mismatch_never_uploads(publication, field, value):
    selected, _, state, _ = publication
    holder.publish_release(selected.checkout)
    state['uploads'].clear()
    state['release'][field] = value
    with pytest.raises(net.Failed, match='does not match'):
        holder.publish_release(selected.checkout)
    assert state['uploads'] == []


def test_existing_tag_mismatch_never_changes_release(publication):
    selected, _, state, _ = publication
    holder.publish_release(selected.checkout)
    state['uploads'].clear()
    state['tag'] = 'b' * 40
    with pytest.raises(net.Failed, match='tag points'):
        holder.publish_release(selected.checkout)
    assert state['creates'] == 1 and state['uploads'] == []


def test_changed_or_symlinked_artifact_refuses_before_api(built, monkeypatch):
    selected, output = built
    monkeypatch.setattr(net, 'request', lambda *a, **k: pytest.fail('unexpected API call'))
    wheel = next(output.glob('*.whl'))
    wheel.write_bytes(b'changed')
    with pytest.raises(net.Failed, match='provenance'):
        holder.artifacts(selected)
    wheel.unlink()
    wheel.symlink_to(output / 'build-info.json')
    with pytest.raises(OSError):
        holder.artifacts(selected)


def test_clean_git_view_ignores_hostile_source_configuration(built, tmp_path):
    selected, _ = built
    git(selected.checkout, 'config', 'credential.helper', '!echo hostile')
    git(selected.checkout, 'config', 'url.https://host.invalid/.insteadOf', 'https://github.com/')
    view = tmp_path / 'view'
    view.mkdir()
    holder._view(selected, view)
    assert holder.local(view, 'rev-parse', 'HEAD') == selected.commit
    config = holder.local(view, 'config', '--local', '--list')
    assert 'credential' not in config and 'host.invalid' not in config
    assert holder.local(view, 'show', f'{selected.commit}:version.txt') == VERSION


@pytest.mark.parametrize('identity_case', [
    ('chatgpt-parent', False, 'gpt-6.1-sol', 'chatgpt-lead'),
    ('', True, 'gpt-6.1-sol', 'chatgpt-lead'), ('', False, 'unlisted-model', 'chatgpt-lead'),
    ('', False, 'gpt-6.1-sol', 'chatgpt-impostor'),
])
def test_holder_refuses_helper_ended_unknown_and_mismatched_session(tmp_path, monkeypatch, identity_case):
    parent, retired, model, name = identity_case
    monkeypatch.setattr(holder.real_machine, 'require', lambda action: None)
    monkeypatch.setattr(holder.home, 'home', lambda: tmp_path)
    monkeypatch.setattr(holder, '__file__', str(tmp_path / 'runtimes/build/poolhouse/workspace/github_release.py'))
    who = types.SimpleNamespace(parent=parent, retired=retired, model=model, name=name)
    monkeypatch.setattr(holder, 'connect', lambda: types.SimpleNamespace(whoami=lambda: who))
    monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', 'chatgpt-lead')
    monkeypatch.setattr(holder.authority, 'require', lambda *a, **k: pytest.fail('unexpected gate use'))
    with pytest.raises(net.Failed, match='authenticated live lead'):
        holder.authorize('fixed publication')


def test_credential_open_is_exact_item_without_generic_lookup(monkeypatch):
    seen = []
    monkeypatch.setattr(holder.credentials, '_open', lambda name: seen.append(name))
    monkeypatch.setattr(holder.credentials, 'get', lambda *a: pytest.fail('generic credential lookup'))
    with pytest.raises(net.Failed, match='initial setup'):
        holder.identity()
    assert seen == ['POOLHOUSE_AGENT_APP']
