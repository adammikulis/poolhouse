"""The Windows launcher's LAN relay to a WSL daemon, and the requests it refuses."""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from poolhouse.fleet import wsl, wsl_network
from poolhouse.fleet.onboard import lan


@pytest.fixture
def bridge(monkeypatch):
    target_host = lan.primary_ip()
    if not target_host or target_host.startswith("127."):
        pytest.skip("the relay test needs a locally assigned LAN IPv4 address")
    with socket.socket() as reserved:
        reserved.bind((str(ipaddress.ip_address(target_host)), 0))
        target = reserved.getsockname()
    with wsl_network.NetworkBridge("127.0.0.1", target) as service:
        monkeypatch.setenv(wsl_network.ENV, service.config)
        yield service


def test_a_bridge_without_a_lan_address_refuses_to_listen_on_every_interface():
    with pytest.raises(ValueError):
        wsl_network.NetworkBridge("", ("127.0.0.1", 9)).start()


@pytest.mark.lan_network
def test_the_registered_address_is_the_one_the_relay_listens_on(bridge):
    config = json.loads(bridge.config)
    assert config["address"][0] == bridge.host and config["address"][1] == bridge.target[1]
    assert set(config) == {"address", "token"}


@pytest.mark.lan_network
def test_lan_relay_preserves_stream_half_close_and_releases_ports(bridge):
    payload = (b"request\x00\xff" * 20000)
    with socket.socket() as upstream:
        upstream.bind(bridge.target)
        upstream.listen()

        def respond():
            connection, _ = upstream.accept()
            with connection:
                data = bytearray()
                while chunk := connection.recv(65536):
                    data.extend(chunk)
                connection.sendall(data[::-1])

        worker = threading.Thread(target=respond)
        worker.start()
        with socket.create_connection((bridge.host, bridge.target[1]), timeout=3) as client:
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            received = bytearray()
            while chunk := client.recv(65536):
                received.extend(chunk)
        worker.join(3)
        assert not worker.is_alive()
        assert received == payload[::-1]
    bridge.close()
    with socket.socket() as released:
        released.bind((bridge.host, bridge.target[1]))


def test_wsl_launcher_cleans_bridge_when_process_creation_fails(monkeypatch):
    bridge = MagicMock()
    bridge.config = "authenticated socket configuration"
    local_ui = MagicMock()
    monkeypatch.setattr(wsl.wsl_ui, "LocalUIBridge", lambda *_args: local_ui)
    monkeypatch.setattr(wsl, "_bridge", lambda *_args: bridge)
    monkeypatch.setattr(wsl, "command", lambda *args: list(args))
    monkeypatch.setattr(wsl, "_read", lambda *_args: "/linux/python")
    monkeypatch.setattr(wsl.windows_firewall, "ensure", lambda: True)
    monkeypatch.setattr(wsl.subprocess, "run", MagicMock(return_value=SimpleNamespace(returncode=0)))
    monkeypatch.delenv("POOLHOUSE_HOME", raising=False)
    monkeypatch.delenv("POOLHOUSE_CACHE", raising=False)

    def fail(_argv, **_kwargs):
        raise OSError("child creation failed")

    monkeypatch.setattr(wsl, "launch", fail)
    with pytest.raises(OSError, match="child creation failed"):
        wsl.start([], executable="linux-python")
    bridge.close.assert_called_once()
    local_ui.start.assert_called_once()
    local_ui.close.assert_called_once()


def test_wsl_offline_runtime_does_not_require_lan_listener(monkeypatch):
    monkeypatch.setattr(wsl, "primary_ip", lambda: "")
    read = MagicMock()
    monkeypatch.setattr(wsl, "_read", read)
    assert wsl._bridge("linux-python", []) is None
    read.assert_not_called()


def test_the_launcher_carries_only_the_daemons_port(monkeypatch):
    monkeypatch.setattr(wsl, "primary_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(wsl, "_read", lambda *_args: "127.0.0.2")
    captured = []

    def factory(*args, **kwargs):
        captured.append((args, kwargs))
        return MagicMock()

    monkeypatch.setattr(wsl_network, "NetworkBridge", factory)
    wsl._bridge("linux-python", [])
    assert captured == [(("127.0.0.1", ("127.0.0.2", 8770)), {})]


def test_lan_forward_refuses_public_target(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(socket, "create_connection", connect)
    client = MagicMock()
    wsl_network._forward(client, ("8.8.8.8", 80), threading.Event())
    connect.assert_not_called()


@pytest.mark.parametrize("route, owned, expected", [
    ("default via 172.19.32.1 dev eth0", True, "172.19.32.1"),
    ("default via 192.168.7.1 dev eth0", False, ""),
    ("default via 192.168.7.2 dev eth0", True, ""),
    ("default via 127.0.0.1 dev lo", True, ""),
    ("default via 8.8.8.8 dev eth0", True, ""),
    ("default via 0.0.0.0 dev eth0", True, ""),
    ("default dev eth0", True, ""),
])
def test_wsl_gateway_requires_windows_owned_private_address(monkeypatch, route, owned, expected):
    monkeypatch.setattr(wsl, "_read", lambda *args: route)
    probe = MagicMock()
    probe.__enter__.return_value = probe
    if not owned:
        probe.bind.side_effect = OSError("address belongs to router")
    monkeypatch.setattr(wsl.socket, "socket", lambda *_args: probe)
    assert wsl._gateway("192.168.7.2") == expected
    if expected:
        probe.bind.assert_called_once_with((expected, 0))


def test_gateway_relay_preserves_raw_transport_and_nonloopback_scope(monkeypatch):
    service = wsl_network.NetworkBridge("127.0.0.1", ("127.0.0.2", 12345))
    service.gateway = "127.0.0.3"
    listeners = []

    def listen(port, handler, **kwargs):
        listeners.append((port, handler, kwargs))
        return SimpleNamespace(getsockname=lambda: ("127.0.0.1", 23456))

    monkeypatch.setattr(service, "_listen", listen)
    raw = MagicMock()
    monkeypatch.setattr(wsl_network, "_forward", raw)
    service.start()
    assert [port for port, _, _ in listeners] == [12345, 12345]
    assert listeners[1][2] == {"host": "127.0.0.3"}
    client = object()
    listeners[1][1](client)
    raw.assert_called_once_with(client, ("127.0.0.2", 12345), service.stop)
