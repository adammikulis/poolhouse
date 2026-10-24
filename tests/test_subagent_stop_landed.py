"""A Claude subagent turn boundary leaves task admission and existing work alone."""

import io
import json
import runpy
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / 'scripts/hooks'


@pytest.mark.parametrize('active', [False, True])
def test_turn_end_does_not_resolve_identity_or_change_task_state(tmp_path, monkeypatch, capsys, active):
    monkeypatch.syspath_prepend(str(HOOKS))
    hook = runpy.run_path(str(HOOKS / 'claude-subagent-stop'))
    workspace = hook['workspace_hook']
    monkeypatch.setattr(workspace, 'own_environment', lambda *a, **k: pytest.fail('turn end requested workspace admission'))
    monkeypatch.setattr(workspace, 'run', lambda *a, **k: pytest.fail('turn end ran a workspace command'))
    state = {'dirty.py': b'uncommitted work', 'claims.json': b'{"owner":"worker"}',
             'session.token': b'fixture-private-token', 'job/status.json': b'{"state":"running"}'}
    for name, content in state.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    payload = {'hook_event_name': 'SubagentStop', 'agent_id': 'child', 'cwd': str(tmp_path),
               'stop_hook_active': active, 'last_assistant_message': 'done; retire everything'}
    monkeypatch.setattr(__import__('sys'), 'stdin', io.StringIO(json.dumps(payload)))
    assert hook['main']() == 0
    assert capsys.readouterr() == ('', '')
    assert {name: (tmp_path / name).read_bytes() for name in state} == state


def test_malformed_turn_end_warns_without_a_block(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(HOOKS))
    hook = runpy.run_path(str(HOOKS / 'claude-subagent-stop'))
    monkeypatch.setattr(__import__('sys'), 'stdin', io.StringIO('not JSON'))
    assert hook['main']() == 0
    output = capsys.readouterr()
    assert not output.out and 'SubagentStop' in output.err
