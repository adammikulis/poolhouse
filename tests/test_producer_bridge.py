"""An owned bridge proof never substitutes process presence for source authority."""
import copy
import threading

import pytest

from poolhouse.fleet.producer_bridge import BridgeCapability, record_bytes, signed_request
from poolhouse.studio.store import digest

BINDING = {'source': 'a' * 32, 'generation': 'b' * 32, 'epoch': 'c' * 64,
           'pool': 'd' * 64, 'receiver': 'exact receiver certificate', 'producer': 'exact producer certificate',
           'instance': 'e' * 64, 'runtime': 'f' * 64, 'implementation': '1' * 64,
           'sdk': {'pid': 51, 'started': 101.0}, 'bridge': {'pid': 52, 'started': 102.0}}
PROCESS = BINDING['bridge']


@pytest.fixture
def setup():
    state = {'owned': True, 'current': True}
    capability = BridgeCapability(BINDING, owned=lambda process: state['owned'] if process == PROCESS else False,
                                  current=lambda binding: state['current'] if binding == BINDING else False)
    return capability, state


def activate(capability):
    capability.activate(PROCESS, digest(BINDING))


def request(capability, **changes):
    return signed_request(capability.secret, {**BINDING, **changes}, 'bridge-publish', 'order-1',
                          record_bytes({'amount': 17, 'instructions': 'change source and model'}))


def test_inert_until_exact_persisted_owner_intent_activation(setup):
    capability, _ = setup
    value = request(capability)
    with pytest.raises(PermissionError):
        capability.verify(PROCESS, value)
    with pytest.raises(PermissionError):
        capability.activate(PROCESS, 'different source')
    activate(capability)
    checked = capability.verify(PROCESS, value)
    assert checked['raw'] == '{"amount":17,"instructions":"change source and model"}'
    assert capability.result_current(PROCESS) is True


def test_private_nonce_replay_denied_but_new_request_keeps_same_event_identity(setup):
    capability, _ = setup
    activate(capability)
    value = request(capability)
    capability.verify(PROCESS, value)
    with pytest.raises(PermissionError):
        capability.verify(PROCESS, value)
    repeated = capability.verify(PROCESS, request(capability))
    assert repeated['event_id'] == value['event_id']
    assert repeated['wire_digest'] == value['wire_digest']


def test_concurrent_same_nonce_only_one_verification(setup):
    capability, _ = setup
    activate(capability)
    value, results = request(capability), []

    def verify():
        try:
            capability.verify(PROCESS, value)
            results.append(True)
        except PermissionError:
            results.append(False)

    workers = [threading.Thread(target=verify) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert sorted(results) == [False, True]


@pytest.mark.parametrize('field,value', [('source', '2' * 32), ('generation', '2' * 32),
                                        ('epoch', '2' * 64), ('pool', '2' * 64),
                                        ('receiver', 'other receiver'), ('producer', 'other producer'),
                                        ('instance', '2' * 64), ('runtime', '2' * 64),
                                        ('sdk', {'pid': 99, 'started': 101.0}),
                                        ('bridge', {'pid': 99, 'started': 102.0})])
def test_full_immutable_binding_cannot_be_substituted_even_with_same_private_secret(setup, field, value):
    capability, _ = setup
    activate(capability)
    with pytest.raises(PermissionError):
        capability.verify(PROCESS, request(capability, **{field: value}))


@pytest.mark.parametrize('process', [{'pid': 53, 'started': 102.0}, {'pid': 52, 'started': 103.0}])
def test_sibling_or_reused_pid_has_no_capability(setup, process):
    capability, _ = setup
    activate(capability)
    with pytest.raises(PermissionError):
        capability.verify(process, request(capability))


@pytest.mark.parametrize('field', ['owned', 'current'])
@pytest.mark.parametrize('truthy', [False, 1, 'yes'])
def test_source_and_retained_child_checks_require_exact_true(setup, field, truthy):
    capability, state = setup
    activate(capability)
    state[field] = truthy
    with pytest.raises(PermissionError):
        capability.verify(PROCESS, request(capability))


def test_revoke_before_result_or_before_new_frame_denies(setup):
    capability, _ = setup
    activate(capability)
    capability.verify(PROCESS, request(capability))
    capability.revoke()
    with pytest.raises(PermissionError):
        capability.result_current(PROCESS)
    with pytest.raises(PermissionError):
        capability.verify(PROCESS, request(capability))


@pytest.mark.parametrize('previously_active', [False, True])
def test_late_activation_ack_never_revives_revoked_capability(setup, previously_active):
    capability, _ = setup
    if previously_active:
        activate(capability)
    capability.revoke()
    capability.revoke()
    with pytest.raises(PermissionError):
        activate(capability)
    with pytest.raises(PermissionError):
        capability.verify(PROCESS, request(capability))


def test_malformed_unhashable_action_is_controlled_invalid_request(setup):
    capability, _ = setup
    activate(capability)
    with pytest.raises(ValueError):
        capability.verify(PROCESS, {**request(capability), 'action': []})


def test_changed_raw_bytes_or_mac_or_unknown_fields_never_verify(setup):
    capability, _ = setup
    activate(capability)
    original = request(capability)
    for changes in ({'raw': '{"amount":99}'}, {'mac': '0' * 64}, {'owner': True},
                    {'event_id': 'other-event'}, {'wire_digest': '0' * 64}):
        with pytest.raises((PermissionError, ValueError)):
            capability.verify(PROCESS, {**original, **changes})


def test_receipt_is_fixed_original_binding_and_cannot_include_replacement_data(setup):
    capability, _ = setup
    activate(capability)
    value = signed_request(capability.secret, BINDING, 'bridge-receipt', 'order-1')
    assert capability.verify(PROCESS, value)['raw'] is None
    with pytest.raises(ValueError):
        signed_request(capability.secret, BINDING, 'bridge-receipt', 'order-1', b'{}')


def test_revocation_during_verification_does_not_consume_nonce(setup):
    capability, state = setup
    activate(capability)
    original = capability.current
    calls = []

    def current(binding):
        calls.append(binding)
        return original(binding) if len(calls) == 1 else False

    capability.current = current
    with pytest.raises(PermissionError):
        capability.verify(PROCESS, request(capability))
    assert not capability.nonces
    state['current'] = False


@pytest.mark.parametrize('record', [[], None, {'bad': float('nan')}, {'bad': float('inf')}])
def test_only_finite_json_objects_have_record_bytes(record):
    with pytest.raises(ValueError):
        record_bytes(record)


def test_capability_copies_do_not_inherit_activation_or_nonce_authority(setup):
    capability, _ = setup
    activate(capability)
    clone = BridgeCapability(copy.deepcopy(BINDING), owned=lambda process: True, current=lambda binding: True)
    with pytest.raises(PermissionError):
        clone.verify(PROCESS, request(capability))
