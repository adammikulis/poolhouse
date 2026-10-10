"""The page's list of pools to join: what a daemon with the UI mounted answers."""

from __future__ import annotations

import socket

import pytest

from poolhouse.fleet.discovery import Advertiser, Beacon, mint_pool
from tests.test_fleet_ui import Serving, primary_ip


@pytest.fixture
def serving(tmp_path):
    s = Serving(tmp_path)
    try:
        yield s
    finally:
        s.close()


def test_first_run_lists_the_pools_the_network_offers(serving, tmp_path, monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        udp = s.getsockname()[1]
    monkeypatch.setenv("POOLHOUSE_DISCOVERY_PORT", str(udp))
    key = mint_pool("lab", tmp_path / "other.key").key
    with Advertiser(Beacon(name="larch", port=9), key, port=udp, pool="lab", interval_s=5.0):
        status, body, _ = serving.call("/ui/setup/pools")
    assert status == 200
    assert body["found"] == [{"name": "lab", "machines": ["larch"], "mine": False, "method": "passphrase"}]


def test_a_machine_in_a_pool_asks_for_the_list_behind_its_sign_in(serving):
    mint_pool("home", serving.keyfile)
    assert serving.call("/ui/pools/found")[0] == 401


def test_first_run_refuses_the_list_from_another_machine(serving):
    status, body, _ = serving.call("/ui/setup/pools", host=primary_ip())
    assert status == 403 and "ssh" in body["error"]
