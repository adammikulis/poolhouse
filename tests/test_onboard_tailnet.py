"""Tailscale detection through a real executable on PATH, and how a member is reached
over a tailnet. No Tailscale is needed: the executable is a script that prints canned output."""

import hashlib
import json
import socket
import ssl
import stat
import threading

import pytest
from onboard_support import Recorder, identity

from poolhouse.fleet import tailnet, tls
from poolhouse.fleet.onboard.devices import Devices
from poolhouse.fleet.onboard.lan import NotLocal, in_tailnet, require_local
from poolhouse.fleet.onboard.routes import Route, pinned_probe, reach, resolve

ME, MEMBER = "100.64.0.1", "100.100.5.7"
FP = "ab" * 32


def status(**more):
    doc = {"BackendState": "Running", "AuthURL": "https://login.tailscale.com/a/SECRETURL",
           "TailscaleIPs": [ME, "fd7a:115c:a1e0::1"],
           "Self": {"HostName": "laptop", "TailscaleIPs": [ME], "PrivateKey": "privkey:SECRET"},
           "Member": {"nodekey:SECRET": {"HostName": "kitchen-pi",
                                       "DNSName": "kitchen-pi.tail1.ts.net.",
                                       "TailscaleIPs": [MEMBER, "fd7a:115c:a1e0::7"], "Online": True,
                                       "Key": "SECRETKEY"},
                    "nodekey:2": {"HostName": "attic", "DNSName": "attic.tail1.ts.net.",
                                  "TailscaleIPs": ["100.100.5.8"], "Online": False}}}
    doc.update(more)
    return doc


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A `tailscale` on PATH that prints ``out.json`` (or does what ``mode`` says) and records
    its arguments and its environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "tailscale"
    script.write_text(
        '#!/bin/sh\nd=$(dirname "$0")\necho "$@" >> "$d/argv.log"\nenv > "$d/env.log"\n'
        'case "$(cat "$d/mode" 2>/dev/null)" in\n'
        '  sleep) sleep 30;;\n'
        '  huge) cat "$d/out.json"; head -c 3000000 /dev/zero | tr "\\0" " ";;\n'
        '  fail) exit 3;;\n'
        '  *) cat "$d/out.json";;\nesac\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setattr(tailnet, "MAC_APP_CLI", str(tmp_path / "no-such-app"))

    class Script:
        def say(self, doc=None, mode=""):
            (bin_dir / "out.json").write_text(doc if isinstance(doc, str) else json.dumps(doc))
            (bin_dir / "mode").write_text(mode)

        def argv(self):
            log = bin_dir / "argv.log"
            return log.read_text().splitlines() if log.exists() else []

        def env(self):
            return (bin_dir / "env.log").read_text()
    return Script()


def test_a_running_client_gives_the_addresses_and_the_members(fake):
    fake.say(status())
    net = tailnet.detect()
    assert net.installed and net.up and net.state == "Running"
    assert net.addresses == (ME, "fd7a:115c:a1e0::1") and net.name == "laptop"
    kitchen, attic = net.members
    assert (kitchen.name, kitchen.dns_name, kitchen.online) == (
        "kitchen-pi", "kitchen-pi.tail1.ts.net", True)
    assert kitchen.addresses == (MEMBER, "fd7a:115c:a1e0::7") and attic.online is False
    assert fake.argv() == ["status --json"]


@pytest.mark.parametrize("state", ["Stopped", "NeedsLogin", "Starting"])
def test_a_client_that_is_not_connected_is_down(fake, state):
    fake.say(status(BackendState=state))
    net = tailnet.detect()
    assert net.installed and not net.up and net.addresses == () and net.members == ()


def test_no_client_is_not_installed(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    assert tailnet.detect() == tailnet.Tailnet()


def test_a_failing_client_is_down_not_an_error(fake):
    fake.say(status(), mode="fail")
    net = tailnet.detect()
    assert net.installed and not net.up and net.state == "unavailable"


@pytest.mark.parametrize("raw", ["not json", "[]", "null", "{" * 5000, '{"BackendState": 7}', ""])
def test_malformed_output_is_down(fake, raw):
    fake.say(raw)
    assert not tailnet.detect().up


def test_a_public_address_is_dropped(fake):
    doc = status()
    doc["Member"]["nodekey:SECRET"]["TailscaleIPs"] = ["8.8.8.8", MEMBER, "2606:4700::1111"]
    doc["Member"]["nodekey:2"]["TailscaleIPs"] = ["1.1.1.1"]
    fake.say(doc)
    net = tailnet.detect()
    assert [p.addresses for p in net.members] == [(MEMBER,)]
    assert "8.8.8.8" not in repr(net)


def test_a_public_self_address_means_down(fake):
    fake.say(status(TailscaleIPs=["8.8.8.8"], Self={"HostName": "x", "TailscaleIPs": ["8.8.8.8"]}))
    assert not tailnet.detect().up


def test_hostile_names_are_neutralised(fake):
    doc = status()
    doc["Member"]["nodekey:SECRET"]["HostName"] = "pi\x1b[31m\nrm -rf /;$(reboot)`id`‮"
    doc["Member"]["nodekey:SECRET"]["DNSName"] = "a b;rm.ts.net."
    fake.say(doc)
    member = tailnet.detect().members[0]
    assert all(ch.isalnum() or ch in "._- " for ch in member.name)
    assert member.dns_name == ""


def test_output_over_the_cap_is_ignored(fake):
    fake.say(status(), mode="huge")
    net = tailnet.detect()
    assert not net.up and net.state == "unavailable"


def test_a_slow_client_times_out(fake):
    fake.say(status(), mode="sleep")
    net = tailnet.detect(timeout_s=0.5)
    assert not net.up and net.state == "unavailable"


def test_many_members_and_addresses_are_bounded(fake):
    doc = status()
    doc["Member"] = {f"k{i}": {"HostName": f"h{i}", "Online": True,
                             "TailscaleIPs": [f"100.65.{i // 250}.{i % 250 + 1}"]
                             + [f"100.66.0.{j}" for j in range(1, 12)]}
                   for i in range(600)}
    fake.say(doc)
    net = tailnet.detect()
    assert len(net.members) == tailnet.MOST_MEMBERS
    assert all(len(p.addresses) <= tailnet.MOST_ADDRESSES for p in net.members)


def test_only_read_only_subcommands_are_ever_run(fake):
    fake.say(status())
    tailnet.detect()
    tailnet.detect()
    assert fake.argv() == ["status --json"] * 2
    with pytest.raises(ValueError, match="read-only"):
        tailnet._run("/bin/true", ("up", "--authkey=x"), timeout_s=1, most=100)
    with pytest.raises(ValueError, match="read-only"):
        tailnet._run("/bin/true", ("serve", "--bg"), timeout_s=1, most=100)
    assert fake.argv() == ["status --json"] * 2


def test_secrets_never_reach_the_child_or_the_result_or_the_log(fake, monkeypatch, caplog):
    monkeypatch.setenv("TS_AUTHKEY", "tskey-auth-SECRETVALUE")
    monkeypatch.setenv("TAILSCALE_API_TOKEN", "tok-SECRETVALUE")
    fake.say(status())
    with caplog.at_level("DEBUG"):
        net = tailnet.detect()
    assert "SECRETVALUE" not in fake.env()
    shown = json.dumps(net.public()) + repr(net) + caplog.text
    for word in ("SECRET", "login.tailscale.com", "privkey", "AuthURL"):
        assert word not in shown


def test_names_resolve_only_through_what_status_reported(fake):
    fake.say(status())
    net = tailnet.detect()
    assert net.member_named("kitchen-pi").addresses[0] == MEMBER
    assert net.member_named("KITCHEN-PI.tail1.ts.net.").addresses[0] == MEMBER
    assert net.member_named("example.com") is None and net.member_named("") is None
    assert tailnet.Tailnet(members=net.members).member_named("kitchen-pi") is None


def test_tailnet_ranges():
    assert in_tailnet("100.64.0.1") and in_tailnet("100.127.255.254")
    assert in_tailnet("fd7a:115c:a1e0::5")
    for other in ("100.63.255.255", "100.128.0.1", "10.0.0.1", "8.8.8.8", "fd00::1", "nope", ""):
        assert not in_tailnet(other)
    require_local("100.100.5.7")
    require_local("fd7a:115c:a1e0::5")
    with pytest.raises(NotLocal):
        require_local("100.128.0.1")


def device(tmp_path, *, address="192.168.1.20", name="kitchen-pi", fp=FP):
    devices = Devices(tmp_path / "devices.json", bus=Recorder().bus)
    devices.add_accepter(fp, name, address, secret="c2VjcmV0", mine=True)
    return devices


def seen(answers):
    """A probe that answers for the (address, fingerprint) joins in ``answers``."""
    asked = []

    def probe(address, port, fingerprint):
        asked.append(address)
        return (address, fingerprint) in answers
    probe.asked = asked
    return probe


def up(**more):
    return tailnet.parse(json.dumps(status(**more)).encode())


def test_a_device_on_the_network_is_lan_and_the_tailnet_is_not_asked(tmp_path):
    devices = device(tmp_path)
    probe = seen({("192.168.1.20", FP)})
    got = reach(devices.all()[0], up(), 8772, probe)
    assert (got.route, got.address) == (Route.LAN, "192.168.1.20")
    assert probe.asked == ["192.168.1.20"]


def test_a_device_off_the_network_is_reached_over_the_tailnet(tmp_path):
    devices = device(tmp_path)
    got = reach(devices.all()[0], up(), 8772, seen({(MEMBER, FP)}))
    assert (got.route, got.address) == (Route.TAILNET, MEMBER)


def test_the_certificate_decides_not_the_name(tmp_path):
    devices = device(tmp_path)
    got = reach(devices.all()[0], up(), 8772, seen({(MEMBER, "cd" * 32)}))
    assert got.route is Route.UNREACHABLE


def test_an_offline_member_or_a_down_tailnet_is_unreachable(tmp_path):
    attic = device(tmp_path, name="attic").all()[0]
    assert reach(attic, up(), 8772, seen({("100.100.5.8", FP)})).route is Route.UNREACHABLE
    kitchen = device(tmp_path / "b").all()[0]
    stopped = up(BackendState="Stopped")
    assert reach(kitchen, stopped, 8772, seen({(MEMBER, FP)})).route is Route.UNREACHABLE
    assert reach(kitchen, tailnet.Tailnet(), 8772, seen({(MEMBER, FP)})).route is Route.UNREACHABLE


def test_without_tailscale_only_the_network_is_tried(tmp_path):
    devices = device(tmp_path)
    probe = seen(set())
    assert reach(devices.all()[0], tailnet.Tailnet(), 8772, probe).route is Route.UNREACHABLE
    assert probe.asked == ["192.168.1.20"]


def test_a_device_recorded_at_a_tailnet_address_remembers_it(tmp_path):
    d = device(tmp_path, address=MEMBER).all()[0]
    assert d.tailnet_address == MEMBER
    assert reach(d, tailnet.Tailnet(), 8772, seen({(MEMBER, FP)})).route is Route.UNREACHABLE
    got = reach(d, up(), 8772, seen({(MEMBER, FP)}))
    assert (got.route, got.address) == (Route.TAILNET, MEMBER)


def test_a_public_address_is_never_a_route(tmp_path):
    devices = device(tmp_path, address="8.8.8.8")
    probe = seen({("8.8.8.8", FP)})
    assert reach(devices.all()[0], tailnet.Tailnet(), 8772, probe).route is Route.UNREACHABLE
    assert probe.asked == []


def test_a_device_name_resolves_to_the_address_that_answers(tmp_path):
    devices = device(tmp_path)
    probe = seen({(MEMBER, FP)})
    assert resolve("Kitchen-Pi", 8772, devices, up, probe) == MEMBER

    def never():
        raise AssertionError("the tailnet was asked about an address")
    assert resolve("192.168.1.99", 8772, devices, never, probe) == "192.168.1.99"
    with pytest.raises(NotLocal):
        resolve("kitchen-pi", 8772, devices, tailnet.Tailnet, seen(set()))


def test_the_probe_checks_the_pinned_certificate_on_a_real_server(tmp_path):
    ident = identity(tmp_path, "member")
    server = tls.server_context(ident)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    port = listener.getsockname()[1]

    def serve():
        for _ in range(2):
            conn, _addr = listener.accept()
            try:
                with server.wrap_socket(conn, server_side=True) as wrapped:
                    wrapped.recv(1)
            except (OSError, ssl.SSLError):
                conn.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        assert pinned_probe("127.0.0.1", port, hashlib.sha256(ident.der).hexdigest())
        assert not pinned_probe("127.0.0.1", port, "cd" * 32)
        assert not pinned_probe("8.8.8.8", 8772, hashlib.sha256(ident.der).hexdigest())
    finally:
        listener.close()
        thread.join(2)
