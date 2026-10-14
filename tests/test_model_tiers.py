"""The tier table decides which main sessions may coordinate: the shipped table, a node's own sessions and the agents it lists."""
import copy
import json
from fnmatch import fnmatchcase

import pytest

from poolhouse.board.client import Denied
from poolhouse.board.session import Agent
from poolhouse.workspace import landing
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
QWEN_LOWEST = ('qwen3.5-0.8b', 'qwen3.5-2b', 'qwen3.5-4b', 'Qwen3.5-0.8B-Base', 'Qwen3.5-2B-Base')
QWEN_MODELS = ('Qwen3.8-27B', 'Qwen3.8-Flash-Next', 'Qwen3.8-Flash-Next-GSQ-RCO')
QWEN_SHORT = ('qwen3.8-27b', 'qwen3.8-flash-next', 'qwen3.8-flash-next-gsq-rco')
EXPECTED.update(dict.fromkeys(QWEN_LOWEST, ('qwen', 0)))
EXPECTED.update(dict.fromkeys((*QWEN_MODELS, *QWEN_SHORT), ('qwen', 1)))
DYNAMIC = ('Q8_0', 'Q6_K', 'Q5_K_M', 'Q4_K_XL', 'Q4_K_M', 'Q4_1', 'Q3_K_S', 'Q2_K', 'IQ4_XS', 'IQ4_NL', 'IQ3_S', 'IQ3_XXS',
           'IQ2_XS', 'IQ2_M', 'IQ1_S', 'IQ1_M')
PLAIN = ('BF16', 'F16', 'F32', 'TQ1_0', 'MXFP4', 'MXFP4_MOE', 'NVFP4')
QUANT_IDS = [f'{m}-{t}.gguf' for m in QWEN_MODELS for t in DYNAMIC + PLAIN]
QUANT_IDS += [f'{m}-UD-{t}.gguf' for m in QWEN_MODELS for t in DYNAMIC]
QUANT_IDS += [f'{m}-{t}-00001-of-00003.gguf' for m in QWEN_MODELS for t in (*DYNAMIC, 'BF16', 'F16', 'MXFP4_MOE')]
QUANT_IDS += [f'{m}-MLX-{n}bit' for m in QWEN_MODELS for n in range(2, 9)] + [f'{m}-MLX-bf16' for m in QWEN_MODELS]
LOOKALIKES = (
    'Qwen3.8-27B-evil', 'Qwen3.8-27B-evil.gguf', 'Qwen3.8-27B-Qevil.gguf', 'Qwen3.8-27B-Q4_evil.gguf', 'Qwen3.8-27B-VL-Q4_K_M.gguf',
    'Qwen3.8-27B-Q4_K_M.gguf.exe', 'Qwen3.8-27B-Q4_K_M.gguf ', 'Qwen3.8-27B-DFlash2-b32', 'Qwen3.8-27B-MTP-bf16',
    'Qwen3.8-27B-MLX-9bit', 'Qwen3.8-27B-MLX-4bit-DWQ', 'ThinkingCap-Qwen3.8-27B-MLX-4bit-DWQ', 'Qwen3.8-270B-Q4_K_M.gguf',
    'QWEN3.8-27B', 'qwen3.8-27b-evil', 'qwen3.8-27b-q4_k_m', 'Qwen3.8-Flash-Next-evil', 'Qwen3.8-Flash-Next-NVFP4-DFlash',
    'Qwen3.8-Flash-Next-GSQ-evil', 'Qwen3.8-Flash-Next-GSQ-RCO-Coder', 'Qwen3.6-35B-A3B-Q4_K_M.gguf', 'Qwen3-VL-8B-Instruct',
    'Qwen3.8', 'Qwen', 'Qwen*', 'qwen3.5-35b-a3b', 'Qwen3.8-27B-UD-BF16.gguf',
)


def agent(name, model='', state=VERIFIED, parent=''):
    """An agent record as the node lists it, with the model state the test needs (the node itself verifies none yet)."""
    return Agent(name, parent, 'claude', model, state, 'claude-code', False, 0)


class Stub:
    """A session that names ``who`` and records the calls that reach the node."""

    def __init__(self, who):
        self.who, self.sent = who, []

    def whoami(self):
        return self.who

    def claim(self, kind, key):
        self.sent.append(("claim", {"kind": kind, "key": key}))

    def call(self, name, **fields):
        self.sent.append((name, fields))
        return {}


def row(who):
    """What `agents` lists for ``who``: its parent and how it is presented."""
    return {'parent': who.parent, **describe(who)}


@pytest.mark.parametrize(('model', 'place'), EXPECTED.items())
def test_every_listed_id_lands_in_its_tier(model, place):
    tier = tier_of(model, VERIFIED)
    assert (tier.family, tier.index) == place
    assert tier.lowest == (place[1] == 0)
    assert tier.coordinator_eligible == (place[1] > 0)


@pytest.mark.parametrize('model', QUANT_IDS)
def test_every_quantisation_of_the_two_big_qwens_is_above_the_lowest_tier(model):
    tier = tier_of(model, VERIFIED)
    assert (tier.family, tier.index, tier.lowest, tier.coordinator_eligible) == ('qwen', 1, False, True)


def test_the_quantisations_named_by_the_owner_are_all_listed():
    for token in ('Q8_0', 'Q4_K_XL', 'IQ4_XS', 'IQ3_S', 'IQ1_M'):
        assert tier_of(f'Qwen3.8-27B-{token}.gguf', VERIFIED).index == 1
        assert tier_of(f'Qwen3.8-Flash-Next-GSQ-RCO-{token}-00001-of-00002.gguf', VERIFIED).index == 1
    assert tier_of('Qwen3.8-27B-MLX-4bit', VERIFIED).index == 1
    assert tier_of('Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00001-of-00002.gguf', VERIFIED).reason == ''
    assert tier_of('Qwen3.8-Flash-Next-GSQ-RCO-IQ2_XS-00001-of-00002.gguf', VERIFIED).reason == ''


@pytest.mark.parametrize('model', LOOKALIKES)
def test_lookalike_qwen_ids_are_not_listed(model):
    assert tier_of(model, VERIFIED).reason == NOT_LISTED


@pytest.mark.parametrize('model', QUANT_IDS + list(LOOKALIKES) + list(QWEN_LOWEST) + list(EXPECTED))
def test_no_example_id_matches_two_tiers(model):
    owners = {(f, i) for f, i, _, globs in load_table(SHIPPED) if any(fnmatchcase(model, g) for g in globs)}
    assert len(owners) <= 1


@pytest.mark.parametrize('model', QWEN_LOWEST)
def test_small_qwens_are_the_lowest_tier_and_landing_refuses_them(model):
    shown = describe(agent('small', model))
    assert not shown['coordinator_eligible'] and shown['coordinator_reason'] == LOWEST
    assert landing.eligible(agent('small', model)) == LOWEST
    stub = Stub(agent('small', model))
    with pytest.raises(Denied):
        landing.request(stub, {'branch': 'feat/x', 'sha': 'a' * 40})
    with pytest.raises(Denied):
        landing.review(stub, 'req-1', 'a' * 40, 'accept')
    assert stub.sent == []


@pytest.mark.parametrize('model', ['qwen3.8-27b', 'Qwen3.8-27B-UD-Q4_K_XL.gguf', 'qwen3.8-flash-next-gsq-rco'])
def test_a_big_qwen_may_request_and_review_landing(model):
    stub = Stub(agent('big', model, CLAIMED))
    assert landing.eligible(stub.who) == ''
    landing.request(stub, {'branch': 'feat/x', 'sha': 'a' * 40})
    landing.review(stub, 'req-1', 'a' * 40, 'accept')
    assert [name for name, _ in stub.sent] == ['claim', 'land_request', 'land_review']
    assert stub.sent[0][1] == {'kind': 'branch', 'key': 'feat/x'}


def test_a_denied_branch_claim_prevents_a_landing_request(monkeypatch):
    stub = Stub(agent('big', 'qwen3.8-27b', CLAIMED))

    def refuse(kind, key):
        assert (kind, key) == ('branch', 'feat/x')
        raise Denied('denied', 'branch held')

    monkeypatch.setattr(stub, 'claim', refuse)
    with pytest.raises(Denied, match='branch held'):
        landing.request(stub, {'branch': 'feat/x', 'sha': 'a' * 40})
    assert stub.sent == []


def test_a_listed_qwen_that_is_not_verified_counts_as_an_unverified_claude_id_does():
    for model in ('qwen3.8-27b', 'Qwen3.8-27B-UD-Q4_K_XL.gguf', 'claude-opus-5-5'):
        for state in (CLAIMED, INHERITED):
            assert tier_of(model, state).reason == NOT_VERIFIED and not tier_of(model, state).coordinator_eligible
            assert describe(agent('unsure', model, state))['coordinator_reason'] == NOT_VERIFIED
        assert tier_of(model, VERIFIED).coordinator_eligible
        assert landing.eligible(agent('unsure', model, CLAIMED)) == ''
    assert tier_of('qwen3.5-2b', CLAIMED).reason == NOT_VERIFIED


def test_the_qwen_family_cannot_be_claimed_by_another_family_or_a_wider_glob():
    for taken in ('qwen3.8-27b', 'Qwen3.8-27B', 'Qwen3.8-27B-Q4_K_M.gguf', 'qwen3.5-2b', 'Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00001-of-00002.gguf'):
        table = mutated(lambda t, taken=taken: t['families'][1]['tiers'][1]['id_globs'].append(taken))
        with pytest.raises(TierTableError):
            load_table(table)
    table = mutated(lambda t: t['families'][2]['tiers'][0]['id_globs'].append('gpt-6-luna'))
    with pytest.raises(TierTableError):
        load_table(table)


def test_the_shipped_table_lists_exactly_the_ids_the_tests_name():
    literal = {g for f in SHIPPED['families'] for t in f['tiers'] for g in t['id_globs'] if not set('*?[') & set(g)}
    assert literal <= set(EXPECTED) | set(QUANT_IDS)
    assert load_table(SHIPPED)


@pytest.mark.parametrize('model', ['claude-haiku-5-5', 'gpt-6-luna', 'anthropic.claude-haiku-5-5', 'qwen3.5-2b'])
def test_lowest_tier_main_sessions_are_not_eligible(model):
    shown = describe(agent('lowest', model))
    assert not shown['coordinator_eligible']
    assert shown['coordinator_reason'] == LOWEST


@pytest.mark.parametrize('model', ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-1',
                                   'gpt-6-sol', 'gpt-6-astra', 'qwen3.8-27b', 'Qwen3.8-27B-UD-Q4_K_XL.gguf'])
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
    'qwen without a lowest tier': lambda t: t['families'][2]['tiers'][0].update(lowest=False),
    'qwen with two lowest tiers': lambda t: t['families'][2]['tiers'][1].update(lowest=True),
    'qwen lowest tier not first': lambda t: t['families'][2]['tiers'].reverse(),
    'qwen quantisation glob listed twice': lambda t: t['families'][2]['tiers'][1]['id_globs'].append('qwen3.8-27b'),
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
