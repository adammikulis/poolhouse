"""Operational failures remain specific while callers can catch the public error base."""

import errno

import pytest

import poolhouse as ph
from poolhouse import hub
from poolhouse.decide.types import BackendUnavailable, DecideError
from poolhouse.http import ServerError, ServerUnreachable
from poolhouse.hub import listing, remote, transfer
from poolhouse.serve.backend import ServerFailed, UnknownFlag
from poolhouse.serve.binary import BinaryNotFound


@pytest.mark.parametrize('kind', [ServerError, ServerUnreachable, ServerFailed, BinaryNotFound,
                                  DecideError, BackendUnavailable, remote.RemoteError,
                                  remote.NotFound, remote.GatedRepo])
def test_operational_errors_keep_their_specific_and_runtime_error_types(kind):
    failure = kind('operation refused')
    assert isinstance(failure, ph.Error)
    assert isinstance(failure, RuntimeError) and isinstance(failure, kind)
    try:
        raise failure
    except ph.Error as caught:
        assert caught is failure and caught.args == ('operation refused',)


@pytest.mark.parametrize('kind', [transfer.NotEnoughSpace, transfer.ChecksumMismatch])
def test_transfer_errors_preserve_os_error_constructor_fields(kind):
    failure = kind(errno.ENOSPC, 'disk is full', 'model.gguf')
    assert isinstance(failure, ph.Error) and isinstance(failure, OSError)
    assert (failure.errno, failure.strerror, failure.filename) == (errno.ENOSPC, 'disk is full', 'model.gguf')
    assert failure.args == (errno.ENOSPC, 'disk is full')


def test_http_error_retains_response_details():
    headers = {'retry-after': '3'}
    failure = ServerError('denied', status=403, body='refused', headers=headers)
    assert isinstance(failure, ph.Error)
    assert (failure.status, failure.body, failure.headers) == (403, 'refused', headers)


@pytest.mark.parametrize('failure,previous', [(remote.NotFound('missing'), ValueError),
                                              (remote.GatedRepo('permission required'), OSError)])
def test_public_fetch_preserves_refusal_cause_and_existing_catch_type(monkeypatch, failure, previous):
    def refused(*args, **kwargs):
        raise failure

    monkeypatch.setattr(listing, 'pull', refused)
    with pytest.raises(previous) as caught:
        hub.fetch('hf:fixture/model/model.gguf')
    assert isinstance(caught.value, ph.Error)
    assert caught.value.__cause__ is failure and str(caught.value) == str(failure)
    assert caught.value.args == (str(failure),)


def test_invalid_input_remains_a_value_error():
    with pytest.raises(ValueError) as caught:
        hub.fetch('not-a-hub-reference')
    assert not isinstance(caught.value, ph.Error)
    assert issubclass(UnknownFlag, ValueError) and not issubclass(UnknownFlag, ph.Error)
