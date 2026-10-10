"""The enforcement mode of a board: open by default, strict restores the delegated-authority checks."""

import json

import pytest
from task_kit import accepted, board as _board_fixture, proposed

from poolhouse.workspace import enforcement_check

pytest_plugins = ["node_kit"]
board = _board_fixture
pytestmark = pytest.mark.redteam


def run(board, who, *argv):
    done = board.node.cli(*argv, '--json', who=who)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_check_lists_what_strict_would_strand_without_changing_the_mode(board):
    seat = board.node.session(board.lead)
    board.tasks(board.worker).create({**board.spec, 'source_key': 'peer-claimable'})
    retiring = board.node.member('retiring', parent=board.lead)
    board.tasks(board.lead).create({**board.spec, 'source_key': 'designated', 'assignees': [retiring.name]})
    board.node.session(retiring).retire()
    report = enforcement_check.check(seat)
    kinds = {row['kind'] for row in report['stranded']}
    assert kinds == {'unassigned', 'designated'}
    assert report['mode'] == 'open' and not report['clear']
    assert board.tasks(board.lead).mode()['mode'] == 'open'


def test_accepted_review_by_a_peer_is_reported(board):
    board.tasks(board.lead).scope(board.task['id'], {'state': 'reserved'})
    proposed(board)
    peer = board.node.member('peer', parent=board.other)
    board.tasks(peer).review(board.task['id'], accepted())
    assert board.tasks(board.lead).get(board.task['id'])['state'] == 'accepted'
    stranded = enforcement_check.check(board.node.session(board.lead))['stranded']
    assert [row['kind'] for row in stranded if row['task'] == board.task['id']] == ['review']


def test_cli_promote_demote_and_check(board):
    shown = run(board, board.lead, 'enforcement', 'check')
    assert shown['mode'] == 'open' and shown['clear']
    promoted = run(board, board.lead, 'enforcement', 'promote')
    assert promoted['mode'] == 'strict' and promoted['from'] == 'open' and promoted['preflight']['clear']
    assert run(board, board.lead, 'enforcement')['mode'] == 'strict'
    demoted = run(board, board.lead, 'enforcement', 'demote')
    assert demoted['mode'] == 'open' and demoted['from'] == 'strict'


def test_a_helper_child_cannot_set_the_mode(board):
    refused = board.node.cli('enforcement', 'promote', who=board.worker)
    assert refused.returncode == 3
    assert board.tasks(board.lead).mode()['mode'] == 'open'
