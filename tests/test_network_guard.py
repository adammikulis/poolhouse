"""The session guard that keeps a test off the LAN, multicast and the person's screen.

Each case runs inside `network_guard.watching()` itself, so the refusal is seen here and the autouse
fixture (which would fail this test at its end) is given nothing to report.
"""

import contextlib
import ipaddress
import os
import socket
import stat
import subprocess
import sys

import network_guard
import pytest
from network_guard import loopback, opener, watching

ANY = str(ipaddress.IPv4Address(0))


def test_the_guard_is_on_for_every_test_by_itself():
    assert socket.socket.bind.__module__ == "network_guard", "an autouse fixture, not a variable somebody sets"


@pytest.mark.parametrize("host", ["127.0.0.1", "127.5.5.5", "::1", "localhost", "::ffff:127.0.0.1", "[::1]"])
def test_loopback_is_this_machine(host):
    assert loopback(host)


@pytest.mark.parametrize("host", ["", ANY, "::", "192.168.1.5", "10.0.0.2", "224.0.2.60", "255.255.255.255", "example.test", None])
def test_anything_else_is_not(host):
    assert not loopback(host)


@pytest.mark.parametrize("host", [ANY, "", "192.168.1.5"])
def test_a_bind_beyond_loopback_is_refused_before_the_system_sees_it(host):
    with watching() as seen, socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(PermissionError):
        s.bind((host, 0))
    assert [w.split(" at ")[0] for w in seen] == [f"bind {host!r}"]


def test_a_bind_on_loopback_is_left_alone():
    with watching() as seen, socket.socket() as s:
        s.bind(("127.0.0.1", 0))
    assert seen == []


def test_joining_a_multicast_group_is_refused():
    with watching() as seen, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s, pytest.raises(PermissionError):
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton("224.0.2.60") + socket.inet_aton(ANY))
    assert [w.split(" at ")[0] for w in seen] == ["join a multicast group"]


@pytest.mark.parametrize("target", ["255.255.255.255", "224.0.2.60", "192.168.1.5"])
def test_a_datagram_to_the_broadcast_the_group_or_the_lan_is_refused(target):
    with watching() as seen, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s, pytest.raises(PermissionError):
        s.sendto(b"x", (target, 9))
    assert [w.split(" at ")[0] for w in seen] == [f"send to {target!r}"]


def test_a_datagram_to_loopback_is_left_alone():
    with watching() as seen, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as there, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        there.bind(("127.0.0.1", 0))
        s.sendto(b"x", there.getsockname())
        assert there.recvfrom(8)[0] == b"x"
    assert seen == []


def test_a_tcp_connection_to_a_lan_address_is_refused():
    with watching() as seen, socket.socket() as s, pytest.raises(PermissionError):
        s.connect(("192.168.1.5", 7447))
    assert [w.split(" at ")[0] for w in seen] == ["connect to '192.168.1.5'"]


def test_a_refusal_the_code_swallowed_is_still_on_the_list():
    with watching() as seen, socket.socket() as s, contextlib.suppress(OSError):
        s.bind((ANY, 0))
    assert seen, "the fixture fails the test from this list, whatever the code under test caught"


@pytest.mark.parametrize("argv", [["/usr/bin/open", "x"], ["/usr/bin/osascript", "-e", "x"], ["/usr/bin/security", "find-generic-password"],
                                  "/usr/bin/open https://example.test", ["/usr/bin/xdg-open", "x"]])
def test_starting_a_program_that_puts_something_on_screen_is_refused(argv):
    with watching() as seen, pytest.raises(PermissionError):
        subprocess.Popen(argv)
    assert len(seen) == 1 and seen[0].startswith("start ") and "test_network_guard.py:" in seen[0]


def test_a_command_line_through_the_shell_is_refused_too():
    with watching() as seen, pytest.raises(PermissionError):
        os.system("/usr/bin/osascript -e 'beep'")  # noqa: S605
    assert [w.split(" at ")[0] for w in seen] == ["start osascript"]


def test_a_stand_in_the_test_made_in_the_temporary_directory_runs(tmp_path):
    fake = tmp_path / "security"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    with watching() as seen:
        assert subprocess.run([str(fake)], check=False).returncode == 0
    assert seen == [] and opener([str(fake)]) == ""


def test_an_ordinary_program_is_not_mistaken_for_one():
    with watching() as seen:
        assert subprocess.run([sys.executable, "-c", "pass"], check=False).returncode == 0
    assert seen == [] and opener(["ls"]) == "" and opener([]) == ""


def test_the_guard_puts_every_function_back():
    before = (socket.socket.bind, socket.socket.sendto, socket.socket.setsockopt, subprocess.Popen.__init__, os.system)
    with watching():
        pass
    assert before == (socket.socket.bind, socket.socket.sendto, socket.socket.setsockopt, subprocess.Popen.__init__, os.system)
    assert network_guard.OPENERS


def test_the_lan_marker_is_registered_and_switched_by_the_one_variable(request):
    from poolhouse.testing import live

    assert live.MARKERS["lan_network"] == "POOLHOUSE_LAN_TESTS"
    assert live.skip_reason(["lan_network"], {}) and not live.skip_reason(["lan_network"], {"POOLHOUSE_LAN_TESTS": "1"})
    assert any(line.startswith("lan_network:") for line in request.config.getini("markers"))
