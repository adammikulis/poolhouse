"""What a board's enforcement mode leaves alone and what it records, on a real node.

tests/test_board_task_enforcement.py holds the preflight, the commands and the refusal of a helper; app/poolhouse-node/tests/tasks.rs
holds who may claim and who may review in each mode.
"""

import pytest
from task_kit import accepted, board as _board_fixture, proposed

from poolhouse.board import session as sessions
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied

pytest_plugins = ["node_kit"]
board = _board_fixture
pytestmark = pytest.mark.redteam

PROPOSAL = {'artifacts': {'replay.json': 'a' * 64}, 'checks': [{'name': 'Worker claims tests', 'passed': True}],
            'summary': 'proposed', 'provenance': {'commit': 'abc123', 'environment': 'native-python',
                                                  'model': 'qwen', 'runtime': 'Agents SDK'}}


@pytest.fixture
def granted(board):
    """The team of tests/task_kit.py with the lead holding the grant a person gave it for the task authority."""
    board.node.allow(board.lead, 'task_authority')
    return board


def mode(kit, new):
    return kit.tasks(kit.lead).set_mode(new)


def audits(kit):
    return [e for e in kit.node.session(kit.lead).read_all(kind='audit') if e.fields.get('event') == 'enforcement.set']


def test_promotion_keeps_in_flight_work_running(granted):
    granted.tasks(granted.worker).claim(granted.task['id'], granted.allocation['allocation_id'])
    assert mode(granted, 'strict')['mode'] == 'strict'
    granted.tasks(granted.worker).heartbeat(granted.task['id'])
    granted.tasks(granted.worker).submit(granted.task['id'], PROPOSAL)
    assert granted.tasks(granted.lead).get(granted.task['id'])['state'] == 'review'


def test_an_accepted_review_stays_accepted_and_keeps_its_reviewer_after_promotion(granted):
    granted.tasks(granted.lead).scope(granted.task['id'], {'state': 'reserved'})
    proposed(granted)
    member = granted.node.member('member', parent=granted.other)
    granted.tasks(member).review(granted.task['id'], accepted())
    mode(granted, 'strict')
    kept = granted.tasks(granted.lead).get(granted.task['id'])
    assert kept['state'] == 'accepted' and kept['review']['verifier'] == member.name


def test_lead_promotes_and_demotes_and_every_change_is_recorded_on_the_board(granted):
    first = mode(granted, 'strict')
    assert first['from'] == 'open' and granted.tasks(granted.lead).mode()['mode'] == 'strict'
    mode(granted, 'open')
    assert [(e.sender, e.fields['subject'], e.fields['detail']) for e in audits(granted)] == [
        (granted.lead.name, granted.node.board, f'{granted.node.board} open>strict'),
        (granted.lead.name, granted.node.board, f'{granted.node.board} strict>open')]
    assert granted.tasks(granted.lead).mode()['mode'] == 'open'


def test_a_helper_child_sets_nothing_and_leaves_no_record(granted):
    with pytest.raises(Denied, match='lead or a person'):
        granted.tasks(granted.worker).set_mode('strict')
    assert granted.tasks(granted.lead).mode()['mode'] == 'open'
    assert audits(granted) == []


def test_the_modes_are_per_board(granted):
    elsewhere = sessions.register(granted.node.client, 'beta', sessions.Native('claude-sonnet-5-5', 'claude-code', 'beta-lead'))
    beta = Tasks(sessions.Session(granted.node.client, 'beta', elsewhere.token, elsewhere.name))
    mode(granted, 'strict')
    assert granted.tasks(granted.lead).mode()['mode'] == 'strict'
    assert beta.mode()['mode'] == 'open'
