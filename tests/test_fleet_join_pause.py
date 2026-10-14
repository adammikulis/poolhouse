"""Coordinated pool pauses and authenticated member recipients."""

from __future__ import annotations

import math
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from poolhouse.fleet import join as joining
from poolhouse.fleet.discovery import (
    Advertiser,
    Beacon,
    derive_token,
    load_pool_key,
    memberships,
)
from poolhouse.fleet.join import (
    describe,
    table,
)
from poolhouse.http import Server
from tests import test_fleet_join as support
from tests.pool_support import join as join_pool_by_passphrase
from tests.test_fleet_join import DEVICE, WORDS, _free_tcp

key = support.key
udp = support.udp
machine_looks_fine = support.machine_looks_fine
loopback_advertisers = support.loopback_advertisers


class PausableDaemon:
    """A member with a real `Availability` behind ``/availability``, and a beacon."""

    def __init__(self, port: int, key: bytes, udp: int, name: str, root: Path) -> None:
        from poolhouse.fleet.api import Daemon, make_handler
        from poolhouse.fleet.availability import Availability
        from poolhouse.fleet.jobs import JobRunner

        self.name = name
        self.schedule = Availability()
        schedule = self.schedule
        files = root / "files"
        files.mkdir(parents=True)
        self.runner = JobRunner(root, files)
        handler = make_handler(Daemon(self.runner, files, derive_token(key), name=name,
                                      schedule=schedule))
        def refresh(b: Beacon) -> None:
            b.device = {**DEVICE, "availability": schedule.public()}

        self.httpd = Server(("127.0.0.1", port), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        beacon = Beacon(name=name, port=port, device=dict(DEVICE), machine=f"id-{name}")
        self.advertiser = Advertiser(beacon, key, port=udp, interval_s=0.2,
                                     refresh=refresh).start()

    def close(self) -> None:
        self.advertiser.stop()
        self.runner.shutdown()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def pool(tmp_path, key, udp):
    """Three members on loopback in one pool, and the root a command would keep."""
    from tests.pool_support import join as join_pool_by_passphrase

    join_pool_by_passphrase(WORDS, group="home", path=key)
    made = [PausableDaemon(_free_tcp(), load_pool_key(key), udp, name=n, root=tmp_path / n)
            for n in ("harrowgate", "larch", "studio")]
    yield made, key, udp, tmp_path / "root"
    for one in made:
        one.close()


def _rows(key: Path, udp: int, made: list, self_name: str = "") -> list[dict]:
    """The discovery rows, addressed to where these fakes actually listen."""
    ports = {d.name: d.httpd.server_address[1] for d in made}
    rows = joining.members(pool_key_path=key, port=udp,
                         self_machine=f"id-{self_name}" if self_name else "")
    for row in rows:
        row["base_url"] = f"http://127.0.0.1:{ports[row['name']]}"
    return rows


class TestPauseTheFleet:
    @pytest.mark.slow
    def test_pause_reaches_every_machine_and_resume_puts_them_back(self, pool):
        from poolhouse.fleet.pausing import Fanout, pause_fleet, pause_table

        made, key, udp, root = pool
        rows = _rows(key, udp, made, self_name="studio")
        assert len(rows) == 3, [r["name"] for r in rows]

        answers = pause_fleet(Fanout(root, minutes=120, reason="away from the desk",
                                     pool_key_path=key), rows)
        assert [a.ok for a in answers] == [True] * 3
        assert all(d.schedule.paused for d in made)
        assert {d.schedule.paused_reason for d in made} == {"away from the desk"}
        text = pause_table(answers, span="2h")
        assert text.startswith("paused 3 of 3 machines for 2h")
        assert "studio (this machine)" in text, text

        back = pause_fleet(Fanout(root, resume=True, pool_key_path=key), rows)
        assert [a.said for a in back] == ["taking work again"] * 3
        assert not any(d.schedule.paused for d in made)
        assert pause_table(back, resume=True).startswith("resumed 3 of 3 machines")

    @pytest.mark.slow
    def test_a_machine_that_is_off_is_named_as_still_taking_work(self, pool):
        from poolhouse.fleet.pausing import UNHEARD, Fanout, pause_fleet, pause_table

        made, key, udp, root = pool
        rows = _rows(key, udp, made)
        asked = Fanout(root, minutes=60, pool_key_path=key)
        pause_fleet(asked, rows)

        made[1].close()                                  # larch goes to sleep
        answers = pause_fleet(asked, [r for r in rows if r["name"] != "larch"])

        gone = [a for a in answers if not a.ok]
        assert [a.name for a in gone] == ["larch"]
        assert gone[0].said == UNHEARD
        text = pause_table(answers, span="1h")
        assert "larch" in text and UNHEARD in text
        assert "1 machine did not hear this" in text

    def test_the_length_of_the_pause_is_read_the_way_every_duration_is(self, pool,
                                                                      monkeypatch):
        _, key, _, root = pool
        sent: list = []
        monkeypatch.setattr(joining, "pause_fleet", lambda what, rows: sent.append(what) or [])
        monkeypatch.setattr(joining, "members", lambda **kw: [])
        code = joining.main(["--pool-key", str(key), "--root", str(root),
                             "pause", "--for", "2h 30m", "--reason", "somebody is here"])
        assert code == 1, "no machine answered, so the command says so"
        assert sent[0].minutes == 150
        assert sent[0].reason == "somebody is here"

    def test_a_length_that_is_not_a_length_is_refused(self, pool):
        _, key, _, root = pool
        assert joining.main(["--pool-key", str(key), "--root", str(root),
                             "pause", "--for", "soon"]) == 2


def _paused(name: str, until: float | None, reason: str = "",
            paused: bool = True) -> dict:
    """A member row shaped the way a beacon carries one."""
    return {"name": name,
            "device": {"availability": {"paused": paused, "paused_until": until,
                                        "paused_reason": reason}}}


class TestWhichPauseIsAdopted:
    def test_the_longest_pause_wins(self):
        from poolhouse.fleet.pausing import pause_among

        now = time.time()
        found = pause_among([_paused("harrowgate", now + 600, "a call"),
                             _paused("larch", now + 7200, "rendering")])
        assert found is not None
        assert (found.name, found.reason) == ("larch", "rendering")

    def test_a_pause_with_no_end_beats_a_timed_one(self):
        from poolhouse.fleet.pausing import pause_among

        found = pause_among([_paused("larch", time.time() + 86400, "rendering"),
                             _paused("studio", None, "somebody is here")])
        assert found is not None and found.name == "studio"
        assert found.until == math.inf
        assert "until it is resumed" in found.said()

    def test_a_pause_that_has_run_out_is_not_adopted(self):
        from poolhouse.fleet.pausing import pause_among

        assert pause_among([_paused("larch", time.time() - 60, "rendering")]) is None

    def test_a_machine_taking_work_carries_no_pause(self):
        from poolhouse.fleet.pausing import pause_among

        assert pause_among([_paused("larch", None, "", paused=False)]) is None

    def test_a_machine_keeps_its_own_longer_pause(self):
        from poolhouse.fleet.availability import Availability
        from poolhouse.fleet.pausing import adopt_pause, pause_among

        mine = Availability()
        mine.pause(minutes=600, reason="rendering here")
        assert adopt_pause(mine, pause_among([_paused("larch", time.time() + 600,
                                                      "a call")])) is None
        assert mine.paused_reason == "rendering here"

    def test_a_pause_with_no_reason_still_says_why(self):
        from poolhouse.fleet.availability import Availability
        from poolhouse.fleet.pausing import POOL_PAUSE, adopt_pause, pause_among

        mine = Availability()
        adopt_pause(mine, pause_among([_paused("larch", None)]))
        assert POOL_PAUSE in mine.may_start()[1]


class TestAdoptingThePauseOnStartup:
    @pytest.mark.slow
    def test_a_machine_that_starts_into_a_paused_pool_comes_up_paused(self, pool):
        from poolhouse.fleet.availability import Availability
        from poolhouse.fleet.pausing import adopt_pause, member_pause

        made, key, udp, _ = pool
        for one in made:
            one.schedule.pause(minutes=120, reason="somebody is here")

        mine = Availability()
        taken = adopt_pause(mine, member_pause(key, timeout_s=3.0, port=udp))
        assert taken is not None, "no member's pause was heard"
        assert not mine.may_start()[0]
        assert "somebody is here" in mine.may_start()[1]

    @pytest.mark.slow
    def test_a_machine_that_starts_into_a_running_pool_takes_work(self, pool):
        from poolhouse.fleet.availability import Availability
        from poolhouse.fleet.pausing import adopt_pause, member_pause

        made, key, udp, _ = pool
        for one in made:
            one.schedule.pause(minutes=1, reason="somebody is here")
            one.schedule.resume()

        mine = Availability()
        assert adopt_pause(mine, member_pause(key, timeout_s=3.0, port=udp)) is None
        assert mine.may_start()[0]

    def test_a_machine_with_no_pool_does_not_wait(self, tmp_path):
        from poolhouse.fleet.pausing import member_pause

        started = time.time()
        assert member_pause(tmp_path / "nothing.key", timeout_s=30.0) is None
        assert time.time() - started < 1.0


class TestStatusSaysWhoIsPaused:
    def test_a_paused_member_is_named_with_its_reason(self):
        from poolhouse.fleet.availability import Availability

        schedule = Availability()
        schedule.pause(minutes=120, reason="somebody is here")
        beacon = Beacon(name="larch", port=8770, host="127.0.0.1",
                        device={**DEVICE, "availability": schedule.public()})
        row = describe(beacon)
        assert row["paused"] and "somebody is here" in row["paused_because"]
        text = table([row])
        assert "paused" in text and "somebody is here" in text

    @pytest.mark.slow
    def test_a_real_daemon_boots_into_a_paused_pool_paused(self, pool, tmp_path, loopback_advertisers):
        """The whole path: a daemon started as a process reads the beacons and writes
        down the pause before it takes work."""
        import os

        from poolhouse.fleet.availability import Availability

        made, key, udp, _ = pool
        made[1].schedule.pause(minutes=120, reason="somebody is at the desk")
        root = tmp_path / "windermere"
        env = {**os.environ, "POOLHOUSE_POOL_KEY": str(key),
               "POOLHOUSE_DISCOVERY_PORT": str(udp),
               "PYTHONPATH": str(Path(joining.__file__).parents[2])}
        code = ("from poolhouse.fleet import discovery;"
                f"discovery._destinations=lambda group, port: {loopback_advertisers('', udp)!r};"
                "from poolhouse.fleet.daemon import serve, DaemonOptions;"
                f"serve(DaemonOptions(root={str(root)!r}, host='127.0.0.1',"
                f" port={_free_tcp()}, name='windermere', web=False, announce=False))")
        proc = subprocess.Popen([sys.executable, "-c", code], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        saved = root / "availability.json"
        try:
            deadline = time.time() + 90
            while time.time() < deadline and not saved.exists():
                time.sleep(0.2)
            assert saved.exists(), "the daemon wrote down no pause"
            back = Availability.load(saved)
            assert back.paused and "somebody is at the desk" in back.may_start()[1]
        finally:
            proc.terminate()
            proc.wait(timeout=30)


class TestWhoAPauseIsSentTo:
    """`pausing.member_clients` builds the clients `pause_fleet` posts to."""

    def test_a_row_gets_a_client_holding_the_token_its_pool_derives(self, tmp_path):
        from poolhouse.fleet.discovery import derive_token
        from poolhouse.fleet.pausing import member_clients

        key = tmp_path / "pools.json"
        join_pool_by_passphrase("nine blue kettles", group="studio", path=key)
        join_pool_by_passphrase("four grey lanterns", group="annex", path=key)
        held = {m.group: m.key for m in memberships(key)}

        clients = member_clients(
            [{"name": "workshop", "base_url": "https://10.0.0.4:8770", "pools": ["studio"]},
             {"name": "attic", "base_url": "https://10.0.0.5:8770", "pools": ["annex"]}],
            pool_key_path=key, timeout=12.5)

        assert sorted(clients) == ["attic", "workshop"]
        assert clients["workshop"].base_url == "https://10.0.0.4:8770"
        assert clients["workshop"].token == derive_token(held["studio"])
        assert clients["attic"].token == derive_token(held["annex"])
        assert clients["workshop"].token != clients["attic"].token
        assert clients["workshop"].timeout == 12.5

    def test_a_row_no_key_of_this_machine_reaches_gets_no_client(self, tmp_path):
        from poolhouse.fleet.pausing import member_clients

        key = tmp_path / "pools.json"
        join_pool_by_passphrase("nine blue kettles", group="studio", path=key)

        clients = member_clients(
            [{"name": "workshop", "base_url": "https://10.0.0.4:8770", "pools": ["studio"]},
             {"name": "stranger", "base_url": "https://10.0.0.6:8770", "pools": ["elsewhere"]},
             {"name": "unlisted", "base_url": "https://10.0.0.7:8770"}],
            pool_key_path=key)

        assert list(clients) == ["workshop"]
