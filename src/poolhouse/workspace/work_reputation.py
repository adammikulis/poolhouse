"""Read-only model-family standings from authenticated reviews and historical evidence."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

from poolhouse.board import client, person
from poolhouse.board.names import valid_id
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.reputation import economy
from poolhouse.reputation.work import WorkLedger
from poolhouse.workspace import family_accounts, work_dimensions


@contextmanager
def _store(ledger):
    held = ledger or WorkLedger()
    try:
        yield held
    finally:
        if ledger is None:
            held.sealed.close()


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


def brief_on(s: Session) -> str:
    """The same summary for a session of a board."""
    try:
        return _brief(standings_on(s))
    except (OSError, RuntimeError, ValueError):
        return 'Verified-work reputation is unavailable; no credit balance or work rating can be inferred.'


def fleet_route(request) -> bool:
    """The person's read-only verified-work standings behind Fleet authorization, from the person's session on the project's board."""
    if request.path != '/ui/work-reputation/standings':
        return False
    if request.method != 'GET':
        request.send(405, {'error': 'reputation is read-only'})
        return True
    try:
        request.send(200, standings_on(person.session(Path.cwd()), agent=request.asked('agent', ''),
                                       offset=int(request.asked('offset', '0'))))
    except client.Denied:
        request.send(403, {'error': 'the person session is unavailable'})
    except ValueError:
        request.send(400, {'error': 'Invalid reputation agent or evidence offset.'})
    except (client.NodeError, OSError, RuntimeError):
        request.send(503, {'error': 'Verified-work reputation is unavailable or locked.'})
    return True
