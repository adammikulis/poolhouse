"""``poolhouse security``: every listing is JSON on request and nothing prints a secret."""

from __future__ import annotations

import json
import os

import pytest

from poolhouse import caller, home, sentinel
from poolhouse.sentinel import Mode, State
from poolhouse.sentinel.cli import command
from poolhouse.sentinel.store import Holding


def run(capsys, *argv):
    code = command(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_status_json_reports_the_mode_and_a_verified_log(capsys):
    node = sentinel.default()
    node.store.watch("session", "s1", "x")
    code, out, _ = run(capsys, "status", "--json")
    data = json.loads(out)
    assert code == 0 and data["mode"] == "guarded" and data["log_ok"] is True
    assert data["subjects"]["watch"] == 1


def test_baseline_pins_then_scan_finds_a_change(capsys, monkeypatch):
    for marker in ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE"):
        monkeypatch.delenv(marker, raising=False)
    model = home.home() / "m.gguf"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"GGUF" * 1000)
    code, out, _ = run(capsys, "baseline", "--pin", str(model), "--json", "--honey")
    assert code == 0 and json.loads(out)[0]["path"] == str(model)
    assert run(capsys, "scan", "--deep")[0] == 0
    original = model.stat()
    model.write_bytes(b"GGUF" * 999 + b"evil")
    os.utime(model, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert model.stat().st_size == original.st_size
    assert model.stat().st_mtime_ns == original.st_mtime_ns
    node = sentinel.default()
    assert str(model) in node.manifest.pins()
    assert node.store.state_of("model", str(model)) == State.CLEAR
    code, out, err = run(capsys, "scan", "--deep", "--json")
    assert code == 1 and json.loads(out)[0]["kind"] == "integrity.content_changed", (
        out, err, node.manifest.pins(), node.store.state_of("model", str(model)))
    code, out, _ = run(capsys, "quarantine", "list", "--json")
    rows = json.loads(out)
    assert rows[0]["state"] == "quarantined" and rows[0]["moved"] is True
    assert not model.exists()


def test_events_filter_by_kind_and_severity(capsys):
    node = sentinel.default()
    node.store.quarantine(("member", "1.2.3.4"), "forged", {"token": "hf_" + "z" * 30})
    code, out, _ = run(capsys, "events", "--json", "--severity", "critical")
    rows = json.loads(out)
    assert code == 0 and rows and all(r["severity"] == "critical" for r in rows)
    assert "hf_zzz" not in out
    assert json.loads(run(capsys, "events", "--json", "--kind", "nothing.here")[1]) == []


def test_verify_passes_then_fails_after_an_edit(capsys):
    node = sentinel.default()
    node.store.watch("session", "s1", "x")
    node.store.watch("session", "s2", "x")
    assert run(capsys, "verify")[0] == 0
    log = node.bus.log.path
    log.write_text(log.read_text().replace("quarantine.watch", "quarantine.clear", 1))
    code, out, _ = run(capsys, "verify", "--json")
    assert code == 1 and json.loads(out)["ok"] is False


def test_show_prints_metadata_and_the_held_text_only_inert_for_a_process_no_agent_started(capsys):
    node = sentinel.default()
    held = node.store.quarantine(("message", "s:1"), "denied", None,
                                 Holding(text="SECRET INSTRUCTION TEXT"))
    code, out, _ = run(capsys, "quarantine", "show", held.id, "--json")
    assert code == 0 and "SECRET INSTRUCTION" not in out and held.id in out
    code, out, err = run(capsys, "quarantine", "show", held.id, "--text")
    assert code == 0 and "SECRET INSTRUCTION TEXT" in out and not err
    (ran,) = [r for r in caller.audit_rows() if r.get("action") == f"inspect {held.id}"]
    assert ran["event"] == "ran" and ran["who"].startswith("user ")


def test_show_text_is_refused_to_an_agent_at_once_while_the_gate_is_held_back_and_prints_none_of_it(capsys, monkeypatch):
    node = sentinel.default()
    held = node.store.quarantine(("message", "s:1"), "denied", None,
                                 Holding(text="SECRET INSTRUCTION TEXT"))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("POOLHOUSE_AUTHORITY_FLOOR", "person")
    code, out, err = run(capsys, "quarantine", "show", held.id, "--text")
    assert code == 2 and "SECRET INSTRUCTION" not in out + err and "refused to an agent" in err
    assert [r["action"] for r in caller.audit_rows() if r["event"] == "refused"] == [f"inspect {held.id}"]


def test_add_quarantines_a_member_by_hand(capsys):
    code, out, _ = run(capsys, "quarantine", "add", "member:10.5.5.5", "--reason", "seen scanning")
    assert code == 0 and "quarantined member:10.5.5.5" in out
    assert sentinel.default().member_blocked("10.5.5.5")


def test_honey_plant_status_remove(capsys):
    assert run(capsys, "honey", "plant")[0] == 0
    rows = json.loads(run(capsys, "honey", "status", "--json")[1])
    assert len(rows) == 3
    assert run(capsys, "honey", "remove")[0] == 0
    assert json.loads(run(capsys, "honey", "status", "--json")[1]) == []


def test_mode_shows_and_changes_for_a_process_no_agent_started_and_is_refused_to_an_agent_while_held_back(capsys, monkeypatch):
    assert run(capsys, "mode")[1].strip() == Mode.GUARDED.value
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("POOLHOUSE_AUTHORITY_FLOOR", "person")
    code, _, err = run(capsys, "mode", "off")
    assert code == 2 and "refused to an agent" in err and "sentinel.policy" in err
    assert run(capsys, "mode")[1].strip() == Mode.GUARDED.value
    monkeypatch.delenv("CLAUDECODE")
    assert run(capsys, "mode", "off")[0] == 0
    assert run(capsys, "mode")[1].strip() == Mode.OFF.value
    assert sentinel.default().store.state_of("member", "x") == State.CLEAR


def test_an_agent_pins_a_local_model_by_hand_and_pins_it_again_after_it_changes(capsys, monkeypatch):
    model = home.home() / "m.gguf"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"GGUF" * 1000)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert run(capsys, "baseline", "--pin", str(model), "--json")[0] == 0
    model.write_bytes(b"GGUF" * 2000)
    assert run(capsys, "baseline", "--pin", str(model), "--json")[0] == 0
    assert sentinel.default().manifest.pins()[str(model)].bytes == 8000
    assert [r for r in caller.audit_rows() if r["event"] == "refused"] == []


def test_an_agent_may_not_replace_the_pin_of_a_changed_downloaded_model(capsys, monkeypatch):
    model = home.home() / "m.gguf"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"GGUF" * 1000)
    made = sentinel.default().manifest.pin(model, "model", source="pull")
    model.write_bytes(b"GGUF" * 2000)
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(caller.AgentRefused, match="changed downloaded model is refused to an agent"):
        command(["baseline", "--pin", str(model), "--json"])
    assert sentinel.default().manifest.pins()[str(model)] == made


@pytest.mark.parametrize("source", ["pull", "manifest", "first-use", "pinned-digest"])
def test_a_hand_pin_cannot_claim_a_source_only_a_download_or_a_serve_records(capsys, source):
    model = home.home() / "m.gguf"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"GGUF" * 1000)
    code, _, err = run(capsys, "baseline", "--pin", str(model), "--source", source)
    assert code == 2 and "name another" in err and sentinel.default().manifest.pins() == {}


def test_review_lists_what_is_held_for_anyone_with_no_terminal_and_no_prompt(capsys, monkeypatch):
    node = sentinel.default()
    held = node.store.quarantine(("member", "10.7.7.7"), "forged", None)
    code, out, err = run(capsys, "review")
    assert code == 0 and "QUARANTINED" in out and "10.7.7.7" in out and not err
    monkeypatch.setenv("CLAUDECODE", "1")
    code, out, _ = run(capsys, "review", "--json")
    assert code == 0 and [row["id"] for row in json.loads(out)] == [held.id]
    assert node.store.get(held.id).state == State.QUARANTINED
