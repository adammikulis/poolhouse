"""A node or daemon listens beyond loopback only for the installed software on the account's real state root."""

import pytest

from poolhouse import home, lan
from poolhouse.fleet.daemon import ALL_INTERFACES, LOOPBACK, bind_address


@pytest.fixture
def clean(monkeypatch):
    for name in (lan.OFF_ENV, lan.TESTS_ENV, "CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def real_home(monkeypatch, *, test=False):
    monkeypatch.setenv(home.ROOT_ENV, str(home.account_roots()[0]))
    monkeypatch.setattr(lan, "under_test", lambda: test)


def test_a_moved_state_root_is_not_the_real_home(clean, tmp_path):
    clean.setenv(home.ROOT_ENV, str(tmp_path / "rig"))
    assert not lan.on_real_home()


def test_a_redirected_home_does_not_fool_it(clean, tmp_path):
    clean.delenv(home.ROOT_ENV, raising=False)
    clean.setenv("HOME", str(tmp_path))
    assert not lan.on_real_home(), "the account's own directory comes from the password database, not from $HOME"


def test_the_account_root_is_the_real_home(clean):
    real_home(clean)
    assert lan.on_real_home() and lan.allowed()


def test_nothing_set_under_a_test_is_closed(clean):
    real_home(clean, test=True)
    assert not lan.allowed()


def test_pytest_itself_is_recognised_without_any_variable(clean):
    assert lan.under_test()


def test_off_beats_everything(clean):
    real_home(clean)
    clean.setenv(lan.OFF_ENV, "off")
    clean.setenv(lan.TESTS_ENV, "1")
    assert not lan.allowed()


def test_a_person_may_run_the_tests_that_listen_but_an_agent_may_not(clean, tmp_path):
    clean.setenv(home.ROOT_ENV, str(tmp_path))
    clean.setenv(lan.TESTS_ENV, "1")
    assert lan.allowed()
    clean.setenv("CLAUDECODE", "1")
    assert not lan.allowed()


def test_the_flag_is_dropped_only_where_the_network_is_closed(clean):
    assert lan.clamp(["--lan", "--project", "p"]) == ["--project", "p"]
    assert lan.clamp(["--listen", "127.0.0.1:0"]) == ["--listen", "127.0.0.1:0"]
    real_home(clean)
    assert lan.clamp(["--lan"]) == ["--lan"]


def test_the_daemon_binds_loopback_under_a_test_even_told_to_join_the_lan(clean):
    assert bind_address(None, lan=True, joined=True) == LOOPBACK
    real_home(clean)
    assert bind_address(None, lan=True, joined=False) == ALL_INTERFACES
    assert bind_address("192.0.2.7", lan=False, joined=False) == "192.0.2.7", "an address a person typed is theirs"


def test_the_beacon_goes_to_loopback_alone_and_its_socket_is_bound_there_with_no_group(clean):
    from poolhouse.fleet import discovery

    assert discovery._destinations(discovery.default_group(), 9) == [(("127.0.0.1", 9), "")]
    with discovery._socket(broadcast=True, bind=("", 0), group=discovery.default_group()) as sock:
        assert sock.getsockname()[0] == "127.0.0.1"
    real_home(clean)
    assert len(discovery._destinations(discovery.default_group(), 9)) == 4, "the real home keeps multicast, broadcast and loopback"


def test_a_desktop_dialog_is_shown_only_on_the_persons_machine_whatever_is_set(clean):
    from poolhouse import desktop

    clean.delenv(desktop.ENV)  # the suite sets it to console; this is about what happens with it unset
    assert desktop.which_way("Darwin", which=lambda name: "/usr/bin/" + name) == "none", "unset, under a test: nothing on a screen"
    assert desktop.which_way("Darwin", env={}, which=lambda name: "/usr/bin/" + name) == "macos", "an explicit environment is judged as given"
    real_home(clean)
    assert desktop.which_way("Darwin", which=lambda name: "/usr/bin/" + name) == "macos"
    clean.setenv(desktop.ENV, "console")
    assert desktop.which_way("Darwin", which=lambda name: "/usr/bin/" + name) == "none"
