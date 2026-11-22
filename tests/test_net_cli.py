"""The security commands for what came in from the internet."""

import json
import sys

import pytest

from poolhouse import authority, caller, net
from poolhouse.httpguard import Limits
from poolhouse.net import cli, policy, provenance
from poolhouse.net.scan import ScanPolicy
from tests.net_site import Site, gguf_bytes


@pytest.fixture
def person():
    """A process no agent started, with no terminal on either stream (pytest captures both)."""
    assert not caller.marked() and not caller.is_terminal(sys.stdin) and not caller.is_terminal(sys.stdout)


@pytest.fixture
def delegated(monkeypatch):
    """The `sentinel.policy` gate delegated (the suite holds every gate back from agents): an agent passes it and the pass is audited."""
    monkeypatch.delenv(authority.FLOOR_ENV)
    authority.set_state(["sentinel.policy"], authority.DELEGATED, by="test")


def rows(event: str) -> list[dict]:
    return [r for r in caller.audit_rows() if r["event"] == event]


def run(capsys, *argv):
    code = cli.command(list(argv))
    return code, capsys.readouterr()


def test_downloads_lists_what_was_fetched_with_its_source_and_scan(tmp_path, capsys):
    pipe = net.Pipeline(policy=net.Policy(allowed=["127.0.0.1"], path=tmp_path / "a.jsonl"),
                        limits=Limits(allow_hosts=frozenset({"127.0.0.1"})),
                        scanners=[])
    with Site() as site:
        site.add("/m.gguf", gguf_bytes())
        net.download(site.base + "/m.gguf", tmp_path / "m.gguf", None, pipe)
        url = site.base + "/m.gguf"
    code, out = run(capsys, "downloads", "--json")
    rows = json.loads(out.out)
    assert code == 0 and rows[0]["url"] == url and rows[0]["state"] == "present"
    assert rows[0]["scan"].startswith("scan skipped: model weights")
    assert rows[0]["sha256"] and rows[0]["kind"] == "gguf"
    (tmp_path / "m.gguf").write_bytes(b"GGUF tampered")
    rows = json.loads(run(capsys, "downloads", "--json", "--verify")[1].out)
    assert rows[0]["state"] == "changed"
    (tmp_path / "m.gguf").unlink()
    assert json.loads(run(capsys, "downloads", "--json")[1].out)[0]["state"] == "gone"


def test_downloads_says_so_when_there_are_none(capsys):
    assert "no downloads recorded" in run(capsys, "downloads")[1].out


def test_held_downloads_are_listed_with_their_reason(tmp_path, capsys):
    pipe = net.Pipeline(policy=net.Policy(allowed=["127.0.0.1"], path=tmp_path / "a.jsonl"),
                        limits=Limits(allow_hosts=frozenset({"127.0.0.1"})),
                        scanners=[])
    with Site() as site:
        site.add("/m.gguf", b"not a model")
        with pytest.raises(net.Blocked):
            net.download(site.base + "/m.gguf", tmp_path / "m.gguf", None, pipe)
    rows = json.loads(run(capsys, "downloads", "--json", "--held")[1].out)
    assert rows[0]["outcome"] == "held" and "GGUF" in rows[0]["reason"]


def test_an_approval_runs_with_no_terminal_and_no_prompt_and_is_recorded(person, capsys):
    code, out = run(capsys, "approve-host", "mirror.example", "--note", "my mirror")
    assert code == 0 and "mirror.example approved" in out.out, out.err
    row = policy.default().approvals()[0]
    assert (row.host, row.note) == ("mirror.example", "my mirror") and row.by
    assert policy.default().approved("mirror.example")
    ran = [r for r in rows("ran") if r["action"] == "approve-host mirror.example"]
    assert len(ran) == 1 and ran[0]["who"].startswith("user ")


@pytest.mark.parametrize("marker", caller.AGENT_MARKERS)
def test_an_agent_is_refused_at_once_when_it_approves_a_host_and_nothing_is_recorded(person, capsys, monkeypatch, marker):
    monkeypatch.setenv(marker, "1")
    code, out = run(capsys, "approve-host", "evil.example")
    assert code == 2 and "refused to an agent" in out.err and marker in out.err and "sentinel.policy" in out.err
    assert not policy.default().approved("evil.example") and policy.default().approvals() == []
    refused = rows("refused")
    assert len(refused) == 1 and refused[0]["action"] == "approve-host evil.example"
    assert refused[0]["who"].startswith(f"agent {marker}")


def test_an_agent_passes_while_the_policy_gate_is_delegated_and_the_audit_names_it(delegated, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    code, out = run(capsys, "approve-host", "mirror.example")
    assert code == 0, out.err
    assert policy.default().approved("mirror.example")
    used = [r for r in rows("authority.use") if r["gate"] == "sentinel.policy"]
    assert len(used) == 1 and used[0]["who"].startswith("agent CLAUDECODE")


def test_hosts_lists_the_allow_list_and_the_approvals(person, capsys):
    run(capsys, "approve-host", "mirror.example")
    data = json.loads(run(capsys, "hosts", "--json")[1].out)
    assert "huggingface.co" in data["allowed"] and data["approved"][0]["host"] == "mirror.example"


def test_the_scan_policy_defaults_and_can_be_changed_with_no_terminal(person, capsys):
    data = json.loads(run(capsys, "scan-policy", "--json")[1].out)
    assert data == {"executable": "refuse", "archive": "refuse", "model": "warn", "data": "warn",
                    "scan_models": False}
    assert run(capsys, "scan-policy", "archive", "warn")[0] == 0
    assert ScanPolicy.load().archive == "warn"
    assert run(capsys, "scan-policy", "scan_models", "on")[0] == 0
    assert ScanPolicy.load().scan_models is True
    assert [r["action"] for r in rows("ran")] == ["scan-policy archive", "scan-policy scan_models"]


@pytest.mark.parametrize("marker", ["CLAUDECODE", "POOLHOUSE_AGENT"])
def test_the_scan_policy_is_refused_to_an_agent_at_once_and_stays_as_it_was(person, capsys, monkeypatch, marker):
    monkeypatch.setenv(marker, "1")
    code, out = run(capsys, "scan-policy", "archive", "allow")
    assert code == 2 and "refused to an agent" in out.err
    assert ScanPolicy.load().archive == "refuse"
    assert run(capsys, "scan-policy", "--json")[0] == 0, "reading the policy needs no gate"


def test_the_environment_overrides_the_saved_policy(person, capsys, monkeypatch):
    run(capsys, "scan-policy", "archive", "warn")
    monkeypatch.setenv("POOLHOUSE_NET_UNSCANNED", "archive:refuse")
    assert ScanPolicy.load().archive == "refuse"


def test_scanners_reports_each_backend_and_what_is_missing(capsys):
    rows = json.loads(run(capsys, "scanners", "--json")[1].out)
    names = {r["name"]: r for r in rows}
    assert set(names) == {"clamav", "windows-defender", "macos"}
    assert names["windows-defender"]["available"] is False or names["windows-defender"]["note"] == ""


def test_the_download_index_keeps_only_the_most_recent_rows(tmp_path):
    index = tmp_path / "index.jsonl"
    for number in range(provenance.INDEX_LIMIT + 5):
        provenance.record(provenance.Provenance(
            url=f"http://x/{number}", final_url="", path="", sha256="", size=0, kind="",
            fetched_at=""), index=index)
    assert len(index.read_text().splitlines()) == provenance.INDEX_LIMIT
    assert provenance.recent(1, index)[0]["url"].endswith(str(provenance.INDEX_LIMIT + 4))
