"""Actual temporary-node grant effects under existing charter authority."""
import pytest
from native_cargo import native_node_units  # noqa: F401 (fixture)

CHARTER = ('grants_authority', 'grants_authority_pool', 'grants_authority_phones', 'grants_authority_admit', 'grants_authority_people', 'grants_authority_decide', 'grants_authority_rebind',
           'grants_authority_portable', 'grants_authority_tick', 'grants_authority_loading', 'grants_authority_removal', 'grants_authority_distrust',
           'grants_authority_roles', 'grants_authority_cost')
GRANTING = ('grants', 'grants_enrol', 'grants_foreign', 'grants_hostile', 'grants_keys', 'grants_sites', 'approvals', 'approvals_fatigue', 'person', 'phone_endpoint')
GOVERNING = ('gov_acts', 'gov_charter', 'gov_describe', 'gov_expiry', 'gov_first', 'gov_floods', 'gov_found', 'gov_net', 'gov_strength', 'gov_removal', 'governance',
             'crash', 'processes', 'beacon', 'builds', 'builds_relayed', 'landtrust', 'netsync', 'netsync_pins', 'pool', 'reach', 'refused', 'removal',
             'removal_channels', 'reverse', 'shard', 'tasks_foreign')


@pytest.mark.slow
@pytest.mark.timeout(600)
@pytest.mark.parametrize('target', CHARTER + GRANTING + GOVERNING)
def test_native_targets_that_grant_or_decide_in_a_pool(native_node_units, target):  # noqa: F811
    native_node_units.run_targets((target,))
