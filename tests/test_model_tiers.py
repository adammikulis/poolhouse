"""The tier table decides which main sessions may coordinate: the shipped table, a node's own sessions and the agents it lists."""
import copy
import json

import pytest

from poolhouse.board.session import Agent
from poolhouse.workspace.agent_display import SPAWN_NOTICE, describe, vacancy_notice
from poolhouse.workspace.model_tiers import (
    LOWEST,
    NOT_LISTED,
    NOT_VERIFIED,
    TABLE,
    TierTableError,
    load_table,
    tier_of,
)
from poolhouse.workspace.modelid import CLAIMED, INHERITED, VERIFIED

pytest_plugins = ["node_kit"]
SHIPPED = json.loads(TABLE.read_text(encoding='utf-8'))
EXPECTED = {
    'claude-haiku-5-5': ('anthropic', 0), 'anthropic.claude-haiku-5-5': ('anthropic', 0),
    'claude-haiku-4-5': ('anthropic', 0), 'claude-haiku-4-5-' + '2025' + '1001': ('anthropic', 0),
    'claude-sonnet-5': ('anthropic', 1), 'claude-sonnet-5-5': ('anthropic', 1),
    'anthropic.claude-sonnet-5-5': ('anthropic', 1), 'claude-sonnet-4-6': ('anthropic', 1),
    'claude-opus-5': ('anthropic', 2), 'claude-opus-5-5': ('anthropic', 2),
    'anthropic.claude-opus-5-5': ('anthropic', 2), 'claude-opus-4-5': ('anthropic', 2),
    'claude-opus-4-6': ('anthropic', 2), 'claude-opus-4-7': ('anthropic', 2), 'claude-opus-4-8': ('anthropic', 2),
    'claude-fable-5': ('anthropic', 3), 'claude-fable-5-1': ('anthropic', 3),
    'anthropic.claude-fable-5-1': ('anthropic', 3),
    'gpt-6-luna': ('openai', 0), 'gpt-6-sol': ('openai', 1), 'gpt-6.1-sol': ('openai', 1),
    'gpt-6-astra': ('openai', 2),
}


def agent(name, model='', state=VERIFIED, parent=''):
    """An agent record as the node lists it, with the model state the test needs (the node itself verifies none yet)."""
    return Agent(name, parent, 'claude', model, state, 'claude-code', False, 0)


def row(who):
    """What `agents` lists for ``who``: its parent and how it is presented."""
    return {'parent': who.parent, **describe(who)}


@pytest.mark.parametrize(('model', 'place'), EXPECTED.items())
def test_every_listed_id_lands_in_its_tier(model, place):
    tier = tier_of(model, VERIFIED)
    assert (tier.family, tier.index) == place
    assert tier.lowest == (place[1] == 0)
    assert tier.coordinator_eligible == (place[1] > 0)


def test_the_shipped_table_lists_exactly_the_ids_the_tests_name():
    literal = {g for f in SHIPPED['families'] for t in f['tiers'] for g in t['id_globs'] if '*' not in g}
    assert literal <= set(EXPECTED)
    assert load_table(SHIPPED)


@pytest.mark.parametrize('model', ['claude-haiku-5-5', 'gpt-6-luna', 'anthropic.claude-haiku-5-5'])
def test_lowest_tier_main_sessions_are_not_eligible(model):
    shown = describe(agent('lowest', model))
    assert not shown['coordinator_eligible']
    assert shown['coordinator_reason'] == LOWEST


@pytest.mark.parametrize('model', ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-1',
                                   'gpt-6-sol', 'gpt-6-astra'])
def test_higher_tier_verified_main_sessions_are_eligible(model):
    shown = describe(agent('higher', model))
    assert shown['coordinator_eligible']
    assert shown['coordinator_reason'] == ''


def test_unknown_and_unverified_models_are_not_eligible():
    nomodel, stranger, claimed = agent('nomodel'), agent('stranger', 'frontier-9000'), agent('claimed', 'claude-opus-5-5', CLAIMED)
    assert describe(nomodel)['coordinator_reason'] == NOT_LISTED
    assert describe(stranger)['coordinator_reason'] == NOT_LISTED
    assert describe(claimed)['coordinator_reason'] == NOT_VERIFIED
    assert not any(describe(who)['coordinator_eligible'] for who in (nomodel, stranger, claimed))
    assert tier_of('claude-opus-5-5', CLAIMED).reason == NOT_VERIFIED
    assert tier_of('claude-opus-5-5', INHERITED).reason == NOT_VERIFIED


def test_a_session_of_a_node_that_claims_a_listed_model_stays_ineligible(workspace_node):
    node = workspace_node
    lead = node.member('main-1')
    done = node.cli('whoami', '--model', 'claude-opus-5-5', '--json', who=lead)
    assert json.loads(done.stdout)['model_state'] == 'claimed'
    listed = {a['id']: a for a in json.loads(node.cli('agents', '--json', who=lead).stdout)}
    assert listed[lead.name]['session_kind'] == 'main' and not listed[lead.name]['coordinator_eligible']
    assert listed[lead.name]['coordinator_reason'] == NOT_VERIFIED
    shown = json.loads(node.cli('status', '--json', who=lead).stdout)
    assert {r['id']: r['coordinator_reason'] for r in shown['registered']}[lead.name] == NOT_VERIFIED
    assert shown['coordinator_notice'] == ''


def test_names_never_set_the_tier():
    shown = describe(agent('sonnet-lead', 'claude-haiku-5-5'))
    assert not shown['coordinator_eligible'] and shown['coordinator_reason'] == LOWEST
    assert tier_of('Claude Sonnet 5.5', VERIFIED).reason == NOT_LISTED
    assert tier_of('claude-sonnet-5-5 ', VERIFIED).reason == NOT_LISTED
    assert tier_of('CLAUDE-SONNET-5-5', VERIFIED).reason == NOT_LISTED
    assert tier_of('claude-sonnet-5-5-haiku', VERIFIED).reason == NOT_LISTED


def test_a_subagent_never_becomes_eligible_by_model():
    parent, child = agent('parent', 'claude-sonnet-5-5'), agent('worker', 'claude-fable-5-1', parent='parent')
    grandchild = agent('helper', 'claude-fable-5-1', parent='worker')
    for who in (child, grandchild):
        shown = describe(who)
        assert shown['session_kind'] == 'subagent' and not shown['coordinator_eligible']
        assert shown['coordinator_reason'] == 'not a main session' and shown['spawned_by'] == who.parent
    assert describe(parent)['coordinator_eligible']


def test_only_lowest_tier_mains_are_told_to_spawn_a_coordinator():
    haiku, luna = agent('haiku', 'claude-haiku-5-5'), agent('luna', 'gpt-6-luna')
    assert vacancy_notice([row(haiku), row(luna)]) == SPAWN_NOTICE
    assert 'Sonnet 5.5' in SPAWN_NOTICE
    assert vacancy_notice([row(haiku), row(luna), row(agent('sonnet', 'claude-sonnet-5-5'))]) == ''
    child = agent('kid', 'claude-sonnet-5-5', parent='haiku')
    assert vacancy_notice([row(haiku), row(child)]) == SPAWN_NOTICE, 'a subagent is no coordinator'


def test_unknown_only_mains_get_no_spawn_notice():
    assert vacancy_notice([row(agent('stranger', 'frontier-9000')), row(agent('claimed', 'claude-haiku-5-5', CLAIMED))]) == ''


def mutated(change):
    table = copy.deepcopy(SHIPPED)
    change(table)
    return table


def _first(table):
    return table['families'][0]['tiers']


MALFORMED = {
    'unknown top field': lambda t: t.update(extra=1),
    'unknown family field': lambda t: t['families'][0].update(extra=1),
    'unknown tier field': lambda t: _first(t)[0].update(coordinator_eligible=True),
    'no version': lambda t: t.pop('version'),
    'no families': lambda t: t.update(families=[]),
    'duplicate glob across tiers': lambda t: _first(t)[1]['id_globs'].append('claude-haiku-5-5'),
    'duplicate glob across families': lambda t: t['families'][1]['tiers'][1]['id_globs'].append('claude-fable-5'),
    'literal matched by another tier glob': lambda t: _first(t)[2]['id_globs'].append('claude-haiku-4-5-x'),
    'no lowest tier': lambda t: _first(t)[0].update(lowest=False),
    'two lowest tiers': lambda t: _first(t)[1].update(lowest=True),
    'lowest tier not first': lambda t: _first(t).reverse(),
    'empty glob list': lambda t: _first(t)[1].update(id_globs=[]),
    'empty glob': lambda t: _first(t)[1]['id_globs'].append(''),
    'non-boolean lowest': lambda t: _first(t)[1].update(lowest='no'),
    'duplicate family': lambda t: t['families'][1].update(family='anthropic'),
    'family without tiers': lambda t: t['families'][1].update(tiers=[]),
}


@pytest.mark.parametrize('name', MALFORMED)
def test_a_malformed_table_is_refused(name):
    with pytest.raises(TierTableError):
        load_table(mutated(MALFORMED[name]))
    with pytest.raises(TierTableError):
        tier_of('claude-sonnet-5-5', VERIFIED, mutated(MALFORMED[name]))


def test_a_table_that_is_not_an_object_is_refused():
    with pytest.raises(TierTableError, match='not an object'):
        load_table([])


def test_a_substitute_table_classifies_by_its_own_ids():
    table = {'version': 1, 'families': [{'family': 'acme', 'tiers': [
        {'lowest': True, 'id_globs': ['acme-small-*']}, {'lowest': False, 'id_globs': ['acme-big']}]}]}
    assert tier_of('acme-small-2', VERIFIED, table).lowest
    assert tier_of('acme-big', VERIFIED, table).coordinator_eligible
    assert tier_of('claude-sonnet-5-5', VERIFIED, table).reason == NOT_LISTED
