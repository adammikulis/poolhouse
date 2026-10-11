"""Device provenance follows actual actors without adding permission or account authority."""
import pytest

from poolhouse.workspace import device_metadata, onboard


def device(system='macOS'):
    return {'device_id': 'a' * 64, 'hostname': 'workstation', 'os': system,
            'verification': 'local-observed', 'source': 'local-runtime'}


def test_local_device_observation_is_cached_and_provider_failure_stays_unknown(monkeypatch):
    calls = []
    monkeypatch.setattr(device_metadata, 'device_id', lambda: calls.append(True) or 'a' * 64)
    monkeypatch.setattr(device_metadata.platform, 'system', lambda: 'Darwin')
    device_metadata.current.cache_clear()
    try:
        assert device_metadata.current()['os'] == 'macOS'
        assert device_metadata.current()['device_id'] == 'a' * 64 and calls == [True]
        device_metadata.current.cache_clear()
        def unavailable():
            raise RuntimeError('no native machine identity')
        monkeypatch.setattr(device_metadata, 'device_id', unavailable)
        assert device_metadata.current()['device_id'] is None
        assert device_metadata.current()['verification'] == 'unknown'
    finally:
        device_metadata.current.cache_clear()


@pytest.mark.parametrize('value', [None, [], {'device_id': 'invented'},
                                  {**device(), 'hostname': 'bad\nname'},
                                  {**device(), 'verification': 'joined'}])
def test_malformed_or_unproved_device_metadata_is_rejected(value):
    with pytest.raises(ValueError):
        device_metadata.normalize(value)


@pytest.mark.parametrize('value', [None, [], {**device(), 'hostname': 'bad\nname'}])
def test_a_malformed_device_report_is_rejected(value):
    with pytest.raises(ValueError):
        device_metadata.reported(value)


def test_a_report_with_an_invented_identity_or_a_claimed_proof_is_kept_as_the_weakest_provenance():
    assert device_metadata.reported({'device_id': 'invented', 'hostname': 'h'})['device_id'] is None
    assert device_metadata.reported({**device(), 'verification': 'joined'})['verification'] == 'agent-reported'


def test_an_unknown_profile_is_nonempty_and_invents_no_device():
    shown = device_metadata.normalize({})
    assert shown['label'] == 'Unknown device' and shown['verification'] == 'unknown' and shown['source'] == 'unknown'
    assert shown['hostname'] == '' and shown['device_id'] is None and shown['member_id'] is None


def test_subagent_brief_names_actual_runtime_device_without_prompt_history(monkeypatch):
    monkeypatch.setattr(device_metadata, 'current', lambda: device_metadata.normalize(device()))
    text = onboard.brief('helper', 'lead')
    assert 'macOS · workstation (local-observed)' in text
    assert 'provenance grants no permissions' in text


def test_a_parents_device_is_inherited_with_its_own_confidence_named():
    shown = device_metadata.inherited(device(), 'parent')
    assert shown['hostname'] == 'workstation' and shown['verification'] == 'inherited'
    assert shown['parent_verification'] == 'local-observed' and shown['source'] == 'parent-registry'
    assert shown['inherited_from'] == 'parent'
    with pytest.raises(ValueError, match='registered parent'):
        device_metadata.normalize({**device(), 'verification': 'inherited', 'source': 'parent-registry'})


@pytest.mark.redteam
def test_an_agent_device_report_cannot_claim_joined_authority_or_a_stable_identity():
    forged = {**device(), 'machine_id': 'b' * 16, 'member_id': 'c' * 64, 'member_verification': 'joined',
              'verification': 'joined', 'source': 'fleet-join-code', 'inherited_from': 'unrelated',
              'parent_verification': 'joined'}
    shown = device_metadata.reported(forged)
    assert shown['device_id'] is None and shown['machine_id'] is None and shown['member_id'] is None
    assert shown['verification'] == 'agent-reported' and shown['source'] == 'agent-report'
    assert shown['member_verification'] == 'unknown'
    assert shown['inherited_from'] == '' and shown['parent_verification'] == 'unknown'
    assert shown['hostname'] == 'workstation' and shown['os'] == 'macOS'


def test_only_the_daemons_own_member_fingerprint_makes_a_report_a_joined_member():
    shown = device_metadata.reported(device(), member='d' * 64)
    assert shown['member_id'] == 'd' * 64 and shown['member_verification'] == 'joined'
    assert shown['verification'] == 'agent-reported' and shown['device_id'] is None


def test_runtime_commit_stays_and_the_installed_revision_duplicate_is_gone():
    shown = device_metadata.normalize({**device(), 'runtime_commit': 'a' * 40, 'machine_id': 'b' * 16})
    assert shown['runtime_commit'] == 'a' * 40 and shown['machine_id'] == 'b' * 16
    assert 'installed_revision' not in shown
    assert {'inherited_from', 'parent_verification', 'member_verification'} <= set(shown)


@pytest.mark.parametrize('field,value', [('machine_id', 'invalid'), ('runtime_commit', 'abc'),
                                         ('observed_at', float('nan')), ('observed_at', True),
                                         ('architecture', 'bad\nvalue'), ('member_verification', 'joined')])
def test_extended_profile_fields_are_bounded(field, value):
    with pytest.raises(ValueError):
        device_metadata.normalize({**device(), field: value})


def test_autofill_names_wsl_and_fleet_machine_identity(monkeypatch):
    monkeypatch.setattr(device_metadata, 'device_id', lambda: 'a' * 64)
    monkeypatch.setattr(device_metadata, 'machine_id', lambda: 'b' * 16)
    monkeypatch.setattr(device_metadata.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(device_metadata.platform, 'release', lambda: '6.6-microsoft-standard-WSL2')
    monkeypatch.setattr(device_metadata.platform, 'machine', lambda: 'x86_64')
    device_metadata.current.cache_clear()
    try:
        profile = device_metadata.current()
        assert profile['os'] == 'Linux (WSL)'
        assert profile['machine_id'] == 'b' * 16
        assert profile['architecture'] == 'x86_64'
        assert profile['observed_at'] > 0
    finally:
        device_metadata.current.cache_clear()
