"""Connection projection carries selection metadata, never runtime or signing capabilities."""
from poolhouse.fleet.studio_source_view import public_source


def test_connection_view_excludes_every_private_binding_layer():
    row = {'id': 'source', 'revision': 3, 'provider': 'software-producer', 'generation': 'private-generation',
           'binding': {'secret': 'private'}, 'selection_epoch': 'private-epoch',
           'connection': {'state': 'interrupted', 'reason': 'Check the original receipt.', 'can_check': True,
               'can_connect': 'truthy', 'secret': 'private', 'capability': 'private',
               'producer': {'id': 'offer', 'label': 'Orders', 'device_label': 'Device', 'software_label': 'Software',
                            'process': {'pid': 15}, 'certificate': 'private', 'token': 'private'},
               'pending': {'idempotency_key': 'opaque-command', 'binding': {'secret': 'private'}}}}
    view = public_source(row)
    assert view == {'id': 'source', 'revision': 3, 'provider': 'software-producer', 'connection': {
        'state': 'interrupted', 'reason': 'Check the original receipt.', 'can_check': True, 'can_connect': False,
        'can_rotate': False, 'can_revoke': False,
        'producer': {'id': 'offer', 'label': 'Orders', 'device_label': 'Device', 'software_label': 'Software'},
        'pending': {'idempotency_key': 'opaque-command'}}}


def test_plain_sources_have_no_invented_connection_or_pending_authority():
    assert public_source({'id': 'sample', 'kind': 'examples', 'revision': 1, 'record': {'private': 1}}) == {
        'id': 'sample', 'kind': 'examples', 'revision': 1}
