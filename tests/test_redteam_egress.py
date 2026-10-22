"""The guard that keeps red-team traffic on this machine."""

from __future__ import annotations

import socket

import pytest

from poolhouse.http import Options, request_bytes
from poolhouse.redteam.egress import EgressRefused, local_only
from poolhouse.testing.canned import CANNED_HEADER, CANNED_HEADER_VALUE, canned_server


def test_a_connection_to_an_address_off_this_machine_is_refused_before_it_is_sent():
    with local_only(), pytest.raises(EgressRefused), socket.socket() as sock:
        sock.connect(("192.0.2.1", 9))


def test_a_name_that_is_not_an_address_is_refused_rather_than_resolved():
    with local_only(), pytest.raises(EgressRefused), socket.socket() as sock:
        sock.connect(("example.com", 80))


def test_connect_ex_is_guarded_as_well():
    with local_only(), socket.socket() as sock, pytest.raises(EgressRefused):
        sock.connect_ex(("198.51.100.7", 80))


def test_a_server_on_loopback_is_still_reachable_inside_the_block():
    with canned_server() as server, local_only():
        answered = request_bytes(f"{server.base_url}/health", options=Options(timeout=5))
        assert answered.status == 200
        assert answered.headers.get(CANNED_HEADER) == CANNED_HEADER_VALUE, "the server says it is canned"


def test_the_socket_methods_are_the_originals_again_afterwards():
    before = (socket.socket.connect, socket.socket.connect_ex)
    with local_only():
        assert socket.socket.connect is not before[0]
    assert (socket.socket.connect, socket.socket.connect_ex) == before


def test_a_block_that_raises_still_restores_the_socket_methods():
    before = socket.socket.connect
    with pytest.raises(RuntimeError), local_only():
        raise RuntimeError("boom")
    assert socket.socket.connect is before
