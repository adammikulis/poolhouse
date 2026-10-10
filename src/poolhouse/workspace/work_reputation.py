"""Read-only model-family standings from authenticated reviews and historical evidence."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, NamedTuple

from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.files import read_json
from poolhouse.reputation import economy
from poolhouse.reputation.work import WorkLedger
from poolhouse.workspace import (
    coordination,
    device_accounts,
    family_accounts,
    tokens,
    work_dimensions,
)
from poolhouse.workspace.identity import HUMAN, Denied, valid_id, valid_name
from poolhouse.workspace.service import Workspace


@contextmanager
def _store(ledger):
    held = ledger or WorkLedger()
    try:
        yield held
    finally:
        if ledger is None:
            held.sealed.close()


def scope(ws) -> str:
    """The workspace identity used by verified-work evidence."""
    return coordination.workspace_id(ws)


class Roster(NamedTuple):
    """Whose standing is asked in which ledger scope, who is on the team, the local-agent names saved for them and the
    model-family accounts each is bound to."""

    scope: str
    who: str
    names: list[str]
    aliases: dict[str, list[str]]
    bound: dict[str, list[dict[str, Any]]]


def bindings(s: Session) -> dict[str, list[dict[str, Any]]]:
    """The model-family account each worker of the board last ran a task on, newest first."""
    found: dict[str, list[dict[str, Any]]] = {}
    for task in sorted(Tasks(s).list()['tasks'], key=lambda t: -(t.get('lease') or {}).get('at', 0)):
        lease = task.get('lease')
        account = family_accounts.binding(s.board, lease['resource']) if lease else None
        if account and account not in found.setdefault(task['worker'], []):
            found[task['worker']].append({**account, 'last_bound': lease['at']})
    return found


def legacy_roster(ws, who: str) -> Roster:
    """The agents of an old-style workspace (no person, no device account) as ``who`` sees them."""
    aliases: dict[str, list[str]] = {}
    for path in (ws.base / 'local-agents').glob('*.json'):
        name = path.stem
        if not valid_name(name) or name.endswith('.status'):
            continue
        saved = read_json(path, None)
        if isinstance(saved, dict) and saved.get('version') == 1 and saved.get('name') == name:
            identity = saved.get('identity') or name
            if isinstance(identity, str) and valid_id(identity):
                aliases.setdefault(identity, []).append(name)
    names = [name for name in ws.registry.ids() if ws.registry.info(name)['role'] != HUMAN
             and (device_accounts.account_for(ws, name) or {}).get('base_id') != name]
    return Roster(scope(ws), who, names, aliases, {name: family_accounts.current_accounts(ws, name) for name in names})


def standings_of(roster: Roster, *, agent: str = "", offset: int = 0, ledger: WorkLedger | None = None) -> dict[str, Any]:
    """The completion standing of ``roster.who`` and of its team in the ledger scope the roster names."""
    if type(agent) is not str or (agent and not valid_id(agent)):
        raise ValueError('agent is a registered workspace identity')
    if type(offset) is not int or not 0 <= offset <= 5000:
        raise ValueError('evidence offset must be between zero and 5000')
    with _store(ledger) as held:
        team = held.standings(roster.scope)
    for item in team:
        item['aliases'] = roster.aliases.get(item['agent'], [])
    known = {item['agent'] for item in team}
    for name in roster.names:
        if name not in known:
            team.append({'agent': name, 'verified_tasks': 0, 'evidence': [], 'aliases': roster.aliases.get(name, []),
                         **economy.summary([])})
    everything = work_dimensions.views(list(team), agent=agent, offset=offset)
    team = _accounts(team, roster.bound)
    own_id = next((item['agent'] for item in team if roster.who in item['members']), roster.who)
    own = next((item for item in team if item['agent'] == own_id),
               {'agent': own_id, 'verified_tasks': 0, 'evidence': [], **economy.summary([])})
    for item in team:
        item['evidence_held'] = max(0, len(item['evidence']) - offset - 20)
        item['evidence'] = item['evidence'][offset:offset + 20]
    return {'dimensions': everything, 'own': own,
            'team': [item for item in team if not agent or item['agent'] == agent or agent in item['members']],
            'offset': offset, 'authority': 'none',
            'economy_mode': 'free',
            'metric': 'Completion credits and reviewed quality bonuses; runs are free.'}


def standings_on(s: Session, *, agent: str = "", offset: int = 0, ledger: WorkLedger | None = None) -> dict[str, Any]:
    """The session's own standing and its board's team evidence."""
    roster = Roster(s.board, s.name, [a.name for a in s.agents()], {}, bindings(s))
    return standings_of(roster, agent=agent, offset=offset, ledger=ledger)


def standings(ws, token: str, *, agent: str = "", offset: int = 0, ledger: WorkLedger | None = None) -> dict[str, Any]:
    """The authenticated caller's completion standing and its workspace team's evidence."""
    if reader := getattr(type(ws), "remote_reputation", None):
        return reader(ws, token, agent=agent, offset=offset)
    who = ws.auth(token)
    ws._may(who, 'read')
    return standings_of(legacy_roster(ws, who.id), agent=agent, offset=offset, ledger=ledger)


def _account_group(groups, identity, account):
    key = account['account'] if account else identity
    return groups.setdefault(key, {'agent': key, 'label': account['label'] if account else identity,
        'family_id': account['family_id'] if account else None,
        'account_type': 'model_family' if account else 'unassigned', 'members': set(),
        'devices': set(), 'aliases': set(), 'evidence': [], 'contributions': [], 'enrolled': bool(account)})


def _accounts(team, bound):
    groups = {}
    for row in team:
        items = [('evidence', item) for item in row['evidence']] + [('contributions', item) for item in row.get('contributions', [])]
        accounts = bound.get(row['agent'], [])
        for account in accounts or ([None] if not items else []):
            group = _account_group(groups, row['agent'], account)
            group['members'].add(row['agent'])
            group['aliases'].update(row.get('aliases', []))
        for kind, item in items:
            group = _account_group(groups, row['agent'], item.get('family_account'))
            group['members'].add(row['agent'])
            group['aliases'].update(row.get('aliases', []))
            group[kind].append(item)
            if device := item.get('provenance', {}).get('device_id'):
                group['devices'].add(device)
    result = []
    for group in groups.values():
        evidence = sorted(group['evidence'], key=lambda item: -item['verified_at'])
        result.append({**group, 'devices': sorted(group['devices']), 'members': sorted(group['members']),
                       'aliases': sorted(group['aliases']), 'evidence': evidence, 'verified_tasks': len(evidence),
                       **economy.summary(evidence, group['contributions'])})
    return sorted(result, key=lambda item: item['agent'])


def _brief(standing: dict[str, Any]) -> str:
    rows = [f"{item['agent']}: {item['verified_tasks']} verified tasks; "
            f"{item['economy']['balance']} credits; reliability "
            f"{item['work_reputation']['reliability']} ({item['work_reputation']['reliability_samples']} reliability reviews)"
            for item in standing['team'][:20]]
    return ('Verified-work reputation (recorded evidence, no authority):\n'
            f"Your verified completions: {standing['own']['verified_tasks']}\n"
            f"Your credits: {standing['own']['economy']['balance']}; runs are free, no charging.\n" + '\n'.join(rows))


def brief(ws, token: str) -> str:
    """A bounded own/team reputation summary for an agent's task context."""
    try:
        return _brief(standings(ws, token))
    except (OSError, RuntimeError, ValueError):
        return 'Verified-work reputation is unavailable; no credit balance or work rating can be inferred.'


def brief_on(s: Session) -> str:
    """The same summary for a session of a board."""
    try:
        return _brief(standings_on(s))
    except (OSError, RuntimeError, ValueError):
        return 'Verified-work reputation is unavailable; no credit balance or work rating can be inferred.'


def fleet_route(request) -> bool:
    """The person's read-only verified-work standings behind Fleet authorization."""
    if request.path != '/ui/work-reputation/standings':
        return False
    if request.method != 'GET':
        request.send(405, {'error': 'reputation is read-only'})
        return True
    try:
        ws = Workspace()
        token = tokens.read_file(tokens.directory(ws.base) / tokens.OWNER_FILE)
        if ws.auth(token).role != HUMAN:
            raise Denied('the page needs its person owner')
        request.send(200, standings(ws, token, agent=request.asked('agent', ''),
                                     offset=int(request.asked('offset', '0'))))
    except Denied:
        request.send(403, {'error': 'the workspace person owner is unavailable'})
    except ValueError:
        request.send(400, {'error': 'Invalid reputation agent or evidence offset.'})
    except (OSError, RuntimeError):
        request.send(503, {'error': 'Verified-work reputation is unavailable or locked.'})
    return True
