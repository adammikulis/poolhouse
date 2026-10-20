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


@pytest.mark.parametrize("version, accepted", [("0.3.0-dev.1", True), ("0.3.0.dev2", False), ("invalid", False), ("", False)])
def test_wheel_version_uses_semantic_equality_and_refuses_malformed_metadata(built, version, accepted):
    import io

    selected, _ = built
    manifest, assets = holder.artifacts(selected)
    name = "poolhouse-0.3.0.dev1-py3-none-any.whl"
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("poolhouse-0.3.0.dev1.dist-info/METADATA", "Name: poolhouse\nVersion: " + version + "\n")
        archive.writestr("poolhouse/fleet/built-from", selected.commit)
    assets[name] = holder.Asset(name, body.getvalue(), hashlib.sha256(body.getvalue()).hexdigest())
    if accepted:
        holder._contents(assets, manifest)
    else:
        with pytest.raises(net.Failed, match="version or source provenance"):
            holder._contents(assets, manifest)


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
    ('', True, 'gpt-6.1-sol', 'chatgpt-lead'), ('', False, 'gpt-6.1-sol', 'chatgpt-impostor'),
])
def test_holder_refuses_helper_ended_and_mismatched_session(tmp_path, monkeypatch, identity_case):
    parent, retired, model, name = identity_case
    monkeypatch.setattr(holder.real_machine, 'require', lambda action: None)
    _selected_holder(tmp_path, monkeypatch)
    who = types.SimpleNamespace(parent=parent, retired=retired, model=model, name=name)
    monkeypatch.setattr(holder, 'connect', lambda: types.SimpleNamespace(whoami=lambda: who))
    monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', 'chatgpt-lead')
    monkeypatch.setattr(holder.authority, 'require', lambda *a, **k: pytest.fail('unexpected gate use'))
    with pytest.raises(net.Failed, match='authenticated live lead'):
        holder.authorize('fixed publication')


def _selected_holder(tmp_path, monkeypatch):
    from poolhouse import home, runtime

    prefix = tmp_path / 'runtimes/build'
    package = prefix / 'poolhouse'
    (package / 'fleet').mkdir(parents=True)
    (package / 'fleet/built-from').write_text('a' * 40)
    selected = types.SimpleNamespace(prefix=prefix, commit='a' * 40)
    monkeypatch.setattr(runtime, 'selected', lambda: selected)
    monkeypatch.setattr(home, 'home', lambda: tmp_path)
    monkeypatch.setattr(holder.sys, 'prefix', str(prefix))
    monkeypatch.setattr(holder, '__file__', str(package / 'workspace/github_release.py'))
    return selected, package


@pytest.mark.parametrize('case', ['unselected', 'prefix', 'module', 'stamp', 'corrupt'])
def test_holder_refuses_unselected_or_mismatched_runtime_before_credentials(tmp_path, monkeypatch, case):
    from poolhouse import runtime

    _, package = _selected_holder(tmp_path, monkeypatch)
    monkeypatch.setattr(holder.real_machine, 'require', lambda action: None)
    monkeypatch.setattr(holder, 'connect', lambda: pytest.fail('unexpected session access'))
    monkeypatch.setattr(holder.credentials, '_open', lambda *a: pytest.fail('unexpected credential access'))
    monkeypatch.setattr(holder.github_app, 'mint', lambda *a, **k: pytest.fail('unexpected mint'))
    if case == 'unselected':
        monkeypatch.setattr(runtime, 'selected', lambda: None)
    elif case == 'prefix':
        monkeypatch.setattr(holder.sys, 'prefix', str(tmp_path / 'prepared'))
    elif case == 'module':
        monkeypatch.setattr(holder, '__file__', str(tmp_path / 'prepared/poolhouse/workspace/github_release.py'))
    elif case == 'stamp':
        (package / 'fleet/built-from').write_text('b' * 40)
    else:
        monkeypatch.setattr(runtime, 'selected', lambda: (_ for _ in ()).throw(ValueError('invalid selection')))
    with pytest.raises(net.Failed, match='selected installed credential holder'):
        holder.authorize('fixed publication')


def test_selected_holder_reaches_authenticated_lead_gate(tmp_path, monkeypatch):
    _selected_holder(tmp_path, monkeypatch)
    monkeypatch.setattr(holder.real_machine, 'require', lambda action: None)
    who = types.SimpleNamespace(parent='', retired=False, model='gpt-6.1-sol', name='chatgpt-lead')
    monkeypatch.setattr(holder, 'connect', lambda: types.SimpleNamespace(whoami=lambda: who))
    monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', who.name)
    seen = []
    monkeypatch.setattr(holder.authority, 'require', lambda *args: seen.append(args))
    assert holder.authorize('fixed publication') is who
    assert seen == [('github.release', 'fixed publication')]


def test_credential_open_is_exact_item_without_generic_lookup(monkeypatch):
    seen = []
    monkeypatch.setattr(holder.credentials, '_open', lambda name: seen.append(name))
    monkeypatch.setattr(holder.credentials, 'get', lambda *a: pytest.fail('generic credential lookup'))
    with pytest.raises(net.Failed, match='initial setup'):
        holder.identity()
    assert seen == ['POOLHOUSE_AGENT_APP']


def test_all_existing_asset_conflicts_refuse_before_missing_upload(publication):
    selected, _, state, _ = publication
    holder.publish_release(selected.checkout)
    state['uploads'].clear()
    state['release']['assets'] = state['release']['assets'][-1:]
    state['release']['assets'][0]['digest'] = 'sha256:' + '0' * 64
    with pytest.raises(net.Failed, match='pinned digest'):
        holder.publish_release(selected.checkout)
    assert state['uploads'] == []


def test_wrong_release_url_and_malformed_assets_refuse_before_upload(publication):
    selected, _, state, _ = publication
    holder.publish_release(selected.checkout)
    state['uploads'].clear()
    state['release']['assets'] = ['malformed']
    with pytest.raises(net.Failed, match='inventory'):
        holder.publish_release(selected.checkout)
    state['release']['assets'] = []
    state['release']['html_url'] = 'https://host.invalid/release'
    with pytest.raises(net.Failed, match='public release URL'):
        holder.publish_release(selected.checkout)
    assert state['uploads'] == []


def test_source_reads_ignore_forged_replacement_commit(built):
    selected, _ = built
    (selected.checkout / 'version.txt').write_text('forged')
    git(selected.checkout, 'add', 'version.txt')
    git(selected.checkout, '-c', 'user.name=Codex', '-c', 'user.email=codex@agents.invalid',
        'commit', '-qm', 'chore: forged fixture')
    forged = git(selected.checkout, 'rev-parse', 'HEAD')
    git(selected.checkout, 'replace', selected.commit, forged)
    assert holder.local(selected.checkout, 'show', f'{selected.commit}:version.txt') == VERSION
    assert holder.environment(selected.checkout)['GIT_NO_REPLACE_OBJECTS'] == '1'


def test_installed_hooks_are_exact_regular_blob_bytes(built, tmp_path, monkeypatch):
    selected, _ = built
    hooks = selected.checkout / 'scripts/hooks'
    hooks.mkdir(parents=True)
    body = b'#!/bin/sh\n# trailing whitespace  \nexit 0\n\n'
    (hooks / 'pre-push').write_bytes(body)
    (hooks / 'pushed').write_bytes(b'#!/usr/bin/env python3\n')
    (hooks / 'no-data-files').write_bytes(b'#!/usr/bin/env python3\n')
    git(selected.checkout, 'add', 'scripts/hooks/pre-push', 'scripts/hooks/pushed', 'scripts/hooks/no-data-files')
    git(selected.checkout, '-c', 'user.name=Codex', '-c', 'user.email=codex@agents.invalid', 'commit', '-qm', 'chore: hook fixture')
    built_commit = git(selected.checkout, 'rev-parse', 'HEAD')
    package = tmp_path / 'installed/poolhouse'
    (package / 'fleet').mkdir(parents=True)
    (package / 'fleet/built-from').write_text(built_commit)
    monkeypatch.setattr(holder, '__file__', str(package / 'workspace/github_release.py'))
    destination = holder._hooks(selected, tmp_path / 'view')
    assert (destination / 'pre-push').read_bytes() == body


@pytest.mark.parametrize('reply', [None, [], {}, {'object': None}, {'object': []},
                                    {'object': {}}, {'object': {'type': 'tag', 'sha': 'a' * 40}},
                                    {'object': {'type': 'commit', 'sha': 'invalid'}}])
def test_malformed_commit_references_refuse_with_constant_error(reply):
    with pytest.raises(net.Failed, match='invalid commit reference'):
        holder._commit_ref(reply)


@pytest.fixture
def push_case(built, monkeypatch):
    old, _ = built
    (old.checkout / 'new.py').write_text('VALUE = 1\n')
    git(old.checkout, 'add', 'new.py')
    git(old.checkout, '-c', 'user.name=Codex', '-c', 'user.email=codex@agents.invalid', 'commit', '-qm', 'fix: fixture')
    selected = holder.source(old.checkout, REPO)
    monkeypatch.setattr(holder, 'authorize', lambda action: None)
    monkeypatch.setattr(holder, 'identity', lambda: github_app.Identity(REPO, 7, 'client', 'app', 'not-a-key', 9))
    monkeypatch.setattr(holder.authority, 'require', lambda *a, **k: None)
    state = {'mint': 0, 'push': 0, 'gets': 0, 'hook_status': 0, 'race': False}

    def hooks(selected, directory):
        path = directory / 'trusted-hooks'
        path.mkdir()
        (path / 'pre-push').write_text('#!/bin/sh\n[ -z "$GIT_CONFIG_COUNT" ] || exit 99\nexit '
                                       + str(state['hook_status']) + '\n')
        return path

    def request(method, path, **kwargs):
        state['gets'] += 1
        commit = old.commit
        if state['gets'] == 2 and state['race']:
            commit = selected.commit
        if state['push']:
            commit = selected.commit
        return {'object': {'type': 'commit', 'sha': commit}}

    def mint(*args):
        state['mint'] += 1
        return {'token': 'fixture-token'}

    def push(plan, directory, env):
        assert holder.local(directory, 'rev-parse', 'HEAD') == selected.commit
        assert plan.commit == selected.commit and plan.repo == REPO and plan.branch == '0.3dev'
        assert 'fixture-token' not in repr(env)
        state['push'] += 1

    monkeypatch.setattr(holder, '_hooks', hooks)
    monkeypatch.setattr(net, 'request', request)
    monkeypatch.setattr(net, 'push', push)
    monkeypatch.setattr(github_app, 'mint', mint)
    return selected, state


def test_hook_refusal_prevents_mint_and_push(push_case):
    selected, state = push_case
    state['hook_status'] = 1
    with pytest.raises(net.Failed, match='pre-push checks refused'):
        holder.push_development(selected.checkout)
    assert state['mint'] == state['push'] == 0


def test_remote_race_prevents_mint_and_push(push_case):
    selected, state = push_case
    state['race'] = True
    with pytest.raises(net.Failed, match='remote development tip changed'):
        holder.push_development(selected.checkout)
    assert state['mint'] == state['push'] == 0


def test_matching_checked_push_has_one_private_mint(push_case):
    selected, state = push_case
    assert holder.push_development(selected.checkout) == {'sha': selected.commit}
    assert state['mint'] == state['push'] == 1
