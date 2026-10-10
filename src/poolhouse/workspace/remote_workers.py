"""Dev device worker launches and Board message loops."""

import hashlib
import json
import logging
import os
import shlex
import sys
import time
from dataclasses import asdict
from pathlib import Path

from poolhouse import home, jobs, private_path
from poolhouse.command import flag, option
from poolhouse.files import read_json, writing
from poolhouse.fleet.discovery import memberships
from poolhouse.fleet.remote import Member
from poolhouse.graph.store import GraphStore
from poolhouse.log import say
from poolhouse.windows_private import restrict
from poolhouse.workspace import (
    automatic_connection,
    localagent as la,
    localcli,
    localeffort,
    localloop,
    localmodel,
    localprofile,
    localstart,
    tokens,
)
from poolhouse.workspace.chain import held
from poolhouse.workspace.identity import AGENT, Denied, valid_id
from poolhouse.workspace.project_connection import BoardWorkspace, selected
from poolhouse.workspace.remote import RemoteWorkspace
from poolhouse.workspace.service import Workspace

FIELDS = {'agent_token', 'pool', 'pool_id', 'name', 'model', 'effort', 'max_effort',
          'ctx', 'max_output_tokens', 'task_caps'}
OPTIONS = [option('json'), flag('--device', default='', help='discovered target device name'),
           flag('--project', default='.', help='local shared project checkout'),
           flag('--agent', default='', help='your Dev agent identity'),
           flag('--name', default='local-qwen', help='worker name on the target device'),
           flag('--task', default='', help='post an initial task to the worker on the shared Board'),
           flag('--model', default=localmodel.AUTO, help='downloaded model ID or auto'),
           flag('--effort', default='off'), flag('--max-effort', default='medium'),
           flag('--ctx', default='', help='context tokens, for example 32k'),
           flag('--max-output-tokens', type=localcli._limit, default=None),
           flag('--max-rounds', type=localcli._limit, default=None),
           flag('--max-tool-calls', type=localcli._limit, default=None),
           flag('--max-model-calls', type=localcli._limit, default=None),
           flag('--max-task-seconds', type=localcli._seconds, default=None)]


def _save_connection(path, value):
    why = private_path.problem(path)
    if why not in {"", "missing"}:
        raise Denied(f"worker connection storage: {why}")
    with writing(path) as temporary:
        if os.name == "nt":
            restrict(temporary)
        else:
            temporary.chmod(0o600)
        temporary.write_text(json.dumps(value), encoding="utf-8")


def credential(remote, name, model, harness="poolhouse-agent"):
    """Return a saved live Dev capability or enroll this device's named agent."""
    if not valid_id(name):
        raise Denied('worker enrollment requires a valid agent identity')
    remote._prepare_storage()
    path = remote.base / 'worker-identities.db'
    lock = remote.base / 'worker-identities.lock'
    remote._safe_storage(path)
    remote._safe_storage(lock)
    with held(lock), GraphStore(path) as graph:
        path.chmod(0o600)
        record = next((row['attrs'] for row in graph.nodes('worker-identity')
                       if row['attrs']['requested'] == name), None)
        alias = record['identity'] if record else name
        if not isinstance(alias, str) or not valid_id(alias):
            raise Denied('the worker identity record is invalid')
        remote._safe_storage(tokens.directory(remote.base) / alias.replace('/', '~'))
        try:
            token = tokens.load(remote.base, alias)
        except Denied:
            if private_path.problem(tokens.directory(remote.base) / alias.replace('/', '~')) != 'missing':
                raise
            token = ''
        if token:
            if (not record or record.get('pool_id') != remote.pool_id
                    or record.get('pool') != remote.pool):
                remote.renew(alias)
            who = remote.call('whoami', token)
            if who.get('project', {}).get('pool_id') != remote.pool_id:
                raise Denied('worker launch requires an existing Dev project capability')
            graph.upsert_node({'id': 'worker:' + name, 'kind': 'worker-identity', 'label': name,
                               'attrs': {'requested': name, 'identity': alias, 'pool_id': remote.pool_id, 'pool': remote.pool}})
            return token
        if record is not None:
            raise Denied('the saved Dev worker capability is unavailable')
        result = remote.enroll(name, model=model, harness=harness)
        graph.upsert_node({'id': 'worker:' + name, 'kind': 'worker-identity', 'label': name,
                           'attrs': {'requested': name, 'identity': result['id'], 'pool_id': remote.pool_id, 'pool': remote.pool}})
        return tokens.load(remote.base, result['id'])


def _remote(projects, project_id, pool_key, pool):
    project = projects.get(project_id)
    if not project.board_host:
        raise Denied('the target project has no Board')
    remote = RemoteWorkspace(project.board_host, project_id, pool_key=pool_key, pool=pool)
    return project, remote


def _settings(body, admission):
    pool, pool_id = admission
    if type(body) is not dict or set(body) - FIELDS:
        raise ValueError('worker launch accepts model settings and a project agent capability')
    for key in FIELDS - {'ctx', 'max_output_tokens', 'task_caps'}:
        if key in body and type(body[key]) is not str:
            raise ValueError(f'{key} must be a string')
    for key in ('ctx',):
        if key in body and type(body[key]) is not int:
            raise ValueError(f'{key} must be an integer')
    if body.get('pool') != pool or body.get('pool_id') != pool_id:
        raise Denied('worker launch requires this authenticated pool')
    context = body.get('ctx', 0)
    if context != 0 and not 2048 <= context <= 4 * 1024 * 1024:
        raise ValueError('context must be zero or between 2048 and 4M tokens')
    localstart._output_tokens(body.get('max_output_tokens'))
    localloop.checked_caps(body.get('task_caps', {}))
    localeffort.valid(body.get('effort', 'off'), allow_auto=True)
    localeffort.valid(body.get('max_effort', 'medium'))
    return la.check_name(body.get('name') or 'local-qwen')


def _effective_limits(agent):
    return {"context": agent.ctx, "max_output_tokens": agent.max_output_tokens, "task_caps": asdict(localloop.caps_of(agent))}


def start(projects, project_id, body, *, admission, pool_key=None):
    """Launch a project-bound local model on an authenticated Dev device."""
    pool, pool_id = admission
    try:
        if len(json.dumps(body).encode()) > 32 * 1024:
            return 413, {'error': 'worker launch exceeds the size limit'}
        name = _settings(body, admission)
        project, remote = _remote(projects, project_id, pool_key, pool)
        caller = remote.call('whoami', body.get('agent_token', ''))
        scope = caller.get('project', {})
        if (caller.get('role') != AGENT or scope.get('key') != project_id
                or scope.get('pool_id') != pool_id or scope.get('pool') != pool):
            raise Denied('worker launch requires this Dev project capability')
        ws = Workspace()
        connection = la.folder(ws) / f'{name}.remote.json'
        with held(la.folder(ws) / 'remote-start.lock'):
            prior = read_json(connection, {})
            agent = la.load(ws, name)
            if agent and la.alive(agent):
                if (prior.get('project_id') != project_id or prior.get('host') != remote.host
                        or prior.get('requested_by') != caller['id']):
                    raise Denied('the running worker belongs to another project or caller')
                if body.get('model', localmodel.AUTO) not in (localmodel.AUTO, agent.model):
                    raise ValueError('stop the running worker before changing its model')
                credential(remote, prior['identity'], localmodel.model_identity(agent.model_name))
                _save_connection(connection, {**prior, 'pool_id': pool_id, 'pool': pool})
                return 200, {'name': name, 'identity': prior['identity'], 'model': agent.model_name,
                             'pid': agent.pid, 'project_id': project_id, 'state': 'running', 'already': True,
                             'requested_by': caller['id'],
                             'requested_context': prior.get('requested_context', 'unknown'),
                             'effective_limits': _effective_limits(agent)}
            chosen = localmodel.choose(body.get('model') or localmodel.AUTO,
                                       selection=localmodel.Selection(context=body.get('ctx', 0)))
            if not chosen.ok:
                raise localstart.Unavailable(chosen.problem, chosen.hint)
            lan_name = 'lan-' + projects.machine[:12] + '-' + name[:20]
            lan_token = credential(remote, lan_name, localmodel.model_identity(chosen.name))
            lan_identity = remote.call('whoami', lan_token)['id']
            record = {'host': remote.host, 'project_id': project_id, 'pool': pool,
                      'pool_key': str(pool_key) if pool_key else '',
                      'identity': lan_identity, 'requested_by': caller['id'],
                      'name': name, 'pool_id': pool_id,
                      'requested_context': body.get('ctx', 0) or 'auto',
                      'device_cert': remote.device_cert}
            root = str(Path(project.root).resolve(strict=True))
            ask = localstart.Ask(model=chosen.ref, name=name, project=root,
                                 orders_from=(caller['id'],), effort=body.get('effort', 'off'),
                                 max_effort=body.get('max_effort', 'medium'), ctx=body.get('ctx', 0),
                                 max_output_tokens=body.get('max_output_tokens'), task_caps=body.get('task_caps', {}))
            def spawn(module, argv, **kwargs):
                _save_connection(connection, record)
                return jobs.detach('poolhouse.workspace.remote_workers', [str(ws.base), name], **kwargs)
            got = localstart.start(ws, ask, pick=chosen, spawn=spawn,
                                   authority=localstart.Authority(parent_token=localstart.launch_parent(ws, root)))
            ws.audit('remote-worker.start', caller['id'], agent=name, project_id=project_id,
                     lan_identity=lan_identity)
            return 200, {'name': got.name, 'identity': lan_identity, 'pid': got.pid,
                         'model': got.model, 'project_id': project_id, 'state': 'starting', 'already': got.already,
                         'requested_by': caller['id'], 'requested_context': record['requested_context'],
                         'effective_limits': _effective_limits(la.load(ws, got.name))}
    except Denied as error:
        return 403, {'error': str(error)}
    except localstart.Unavailable as error:
        return 409, {'error': error.problem, 'hint': error.hint}
    except (OSError, ValueError, TypeError, KeyError) as error:
        return 400, {'error': str(error)}


class WorkerRemote:
    """Refresh an existing worker's Dev transport when its installed pool key changes."""

    def __init__(self, remote, record, path=None):
        self.current, self.record, self.path = remote, dict(record), path
        self.refresh()

    def refresh(self):
        old = self.current
        rows = memberships(Path(old.pool_key) if old.pool_key else None)
        if not rows or rows[0].mode != 'dev':
            raise Denied('the running worker requires its active pool')
        current_id = hashlib.sha256(rows[0].key).hexdigest()
        if current_id != old.pool_id or rows[0].group != old.pool:
            fresh = RemoteWorkspace(old.host, old.project_id,
                                    pool_key=Path(old.pool_key) if old.pool_key else None,
                                    pool=rows[0].group)
            if fresh.device_cert != old.device_cert:
                raise Denied('the running worker cannot switch its authenticated Board authority')
        else:
            fresh = old
        if (current_id == self.record.get('pool_id') and rows[0].group == self.record.get('pool')
                and fresh is old):
            return
        identity = self.record['identity']
        fresh.renew(identity)
        fresh._prepare_storage()
        alias_path, lock = fresh.base / 'worker-identities.db', fresh.base / 'worker-identities.lock'
        fresh._safe_storage(alias_path)
        fresh._safe_storage(lock)
        with held(lock), GraphStore(alias_path) as graph:
            alias_path.chmod(0o600)
            for node in graph.nodes('worker-identity'):
                attrs = node['attrs']
                if attrs.get('identity') == identity:
                    graph.upsert_node({**node, 'attrs': {**attrs, 'pool_id': current_id, 'pool': rows[0].group}})
        if self.path is not None:
            fresh._safe_storage(self.path)
            _save_connection(self.path, {**self.record, 'pool_id': current_id, 'pool': rows[0].group})
        self.current = fresh
        self.record.update(pool_id=current_id, pool=rows[0].group)

    def call(self, operation, token, *args, **kwargs):
        self.refresh()
        return self.current.call(operation, token, *args, **kwargs)


class BoardWorker(BoardWorkspace):
    """Keep runtime files local while using the selected agent capability."""

    def __init__(self, local, record, name=""):
        pool_key = Path(record['pool_key']) if record['pool_key'] else None
        rows = memberships(pool_key)
        if not rows or rows[0].mode != 'dev':
            raise Denied('the worker requires its active pool')
        remote = RemoteWorkspace(record['host'], record['project_id'], pool_key=pool_key,
                                 pool=rows[0].group)
        if remote.device_cert != record.get('device_cert'):
            raise Denied('the worker cannot switch its saved authenticated Board authority')
        token = tokens.load(remote.base, record['identity'])
        local_name = name or record.get('name', '')
        path = la.folder(local) / f'{la.check_name(local_name)}.remote.json' if local_name else None
        super().__init__(WorkerRemote(remote, record, path), token)
        self.base, self.local = local.base, local
        self.worker_identity = record['identity']

    def worker_token(self, agent):
        return self.token

    def role_of(self, name):
        return next((row['role'] for row in self.registered() if row['id'] == name
                     and (not row['expires'] or row['expires'] > time.time())), None)

    def info(self, name):
        return next((row for row in self.registered() if row['id'] == name), {})

    def remote_reputation(self, token, **kwargs):
        return self.remote.call('work.reputation', token, **kwargs)

    def audit(self, event, who, **kwargs):
        return self.local.audit(event, who, **kwargs)


def run(args):
    """Discover one Dev target and launch its model on the shared board."""
    if any(getattr(args, key, None) is None for key in (
            "max_output_tokens", "max_rounds", "max_tool_calls", "max_model_calls", "max_task_seconds")):
        say("Task limits include None: tasks could run indefinitely until you stop them.")
    root = Path(args.project).resolve()
    connection = selected(root) or automatic_connection.discover(root)
    if not connection:
        raise Denied('no board was discovered for this checkout')
    pool_key = Path(connection['pool_key']) if connection.get('pool_key') else None
    rows = memberships(pool_key)
    members = rows[:1] if rows and rows[0].mode == 'dev' else []
    if not members:
        raise Denied('remote worker launch requires the active pool')
    remote = RemoteWorkspace(connection['host'], connection['project_id'],
                             pool=members[0].group, pool_key=pool_key)
    daemons = Member.discover(key=members[0].key, group=members[0].group)
    targets = [daemon for daemon in daemons if daemon.beacon and daemon.beacon.machine != home.machine_id()
               and (not args.device or daemon.name == args.device or daemon.beacon.machine == args.device)]
    if len(targets) != 1:
        raise Denied('select one discovered remote device with --device NAME; found: ' + ', '.join(p.name for p in targets))
    authorities = [daemon for daemon in daemons if daemon.beacon and daemon.beacon.cert == remote.device_cert]
    if len(authorities) != 1:
        raise Denied('the Board is not discoverable or is ambiguous')
    if args.agent:
        token = tokens.load(remote.base, args.agent)
        remote.renew(args.agent)
    else:
        token = credential(remote, 'lan-launch-' + home.device_id()[:12], '', harness='poolhouse-workspace')
    target = RemoteWorkspace(targets[0].base_url, remote.project_id, pool=remote.pool,
                             pool_key=Path(remote.pool_key) if remote.pool_key else None)
    result = target._request('worker', {'agent_token': token, 'pool': remote.pool,
                                      'pool_id': remote.pool_id, 'name': args.name, 'model': args.model,
                                      'effort': args.effort, 'max_effort': args.max_effort,
                                      'ctx': localprofile.parse_ctx(args.ctx),
                                      'max_output_tokens': args.max_output_tokens,
                                      'task_caps': {'rounds': getattr(args, 'max_rounds', None),
                                                    'calls': getattr(args, 'max_tool_calls', None),
                                                    'steps': getattr(args, 'max_model_calls', None),
                                                    'seconds': getattr(args, 'max_task_seconds', None)}})
    if args.task:
        sent = remote.call('send', token, result['identity'], 'task', args.task)
        result['task_seq'] = sent['seq']
    return result


def main_cli(args):
    reply = run(args)
    say(json.dumps(reply, indent=1) if args.json else
        f"{reply['identity']} is {reply['state']} on {args.device or 'the discovered remote device'} with {reply['model']}")
    if not args.json:
        say(f"Caller: {reply['requested_by']}")
        say(f"Requested context: {reply['requested_context']}")
        say("Effective limits: " + json.dumps(reply['effective_limits'], sort_keys=True))
        if 'task_seq' in reply:
            project = shlex.quote(str(Path(args.project).resolve()))
            say(f"Task: {reply['task_seq']}; read with cd {project} && poolhouse-workspace thread {reply['task_seq']} --agent {reply['requested_by']}")
    return 0


def run_detached(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    os.environ.update(POOLHOUSE_AGENT='1', POOLHOUSE_NONINTERACTIVE='1')
    logging.basicConfig(level=logging.INFO)
    local, name = Workspace(Path(args[0])), la.check_name(args[1])
    record = read_json(la.folder(local) / f'{name}.remote.json', {})
    agent = la.load(local, name)
    if agent is None or not agent.project:
        raise Denied('the remote worker requires its saved registered project')
    os.chdir(Path(agent.project).resolve(strict=True))
    return localloop.run(BoardWorker(local, record, name), name, localloop.Settings(signals=True))


if __name__ == '__main__':
    raise SystemExit(run_detached())
