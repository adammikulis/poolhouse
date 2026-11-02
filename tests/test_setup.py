"""The first-run wizard: what this machine can do, and what happens without being asked.

Every fact it reports is a one-line check that nothing announces and everything depends on.
The tests here are about it never lying -- an unreadable machine must say so rather than
guess, because a wrong "supported" is worse than no answer.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading

import pytest

from poolhouse.checks import Finding, ask
from poolhouse.setup import BEHAVIOURS, explain, look, main


def test_it_reports_rather_than_changes(capsys):
    """Looking must never alter anything: it is what a first run does before trusting it."""
    findings = look()
    assert all(isinstance(f, Finding) for f in findings)
    assert findings, "a machine that reports nothing at all is a bug in the looking"
    assert all(f.name and f.said for f in findings)


@pytest.mark.parametrize('arguments', [[], ['--quiet'], ['--quiet', '--yes']])
def test_setup_diagnostics_never_install_optional_agent_hooks(tmp_path, monkeypatch, arguments):
    from poolhouse import agent_hooks, setup

    isolated = tmp_path / 'empty-home'
    isolated.mkdir()
    monkeypatch.setenv('HOME', str(isolated))
    monkeypatch.setenv('CODEX_HOME', str(isolated / '.codex'))
    monkeypatch.setattr(setup, 'look', lambda: [Finding(name='runtime', good=True, said='ready')])
    monkeypatch.setattr(agent_hooks, 'install_report', lambda: pytest.fail('changed optional agent settings'))
    assert setup.main(arguments) == 0
    assert list(isolated.iterdir()) == []


def test_runtime_readiness_never_requires_optional_agent_hook_settings(monkeypatch):
    from poolhouse import agent_hooks

    monkeypatch.setattr(agent_hooks, 'findings', lambda: pytest.fail('optional harness became required readiness'))
    findings = look()
    assert findings and all(isinstance(row, Finding) for row in findings)


def test_a_machine_that_will_not_answer_says_so_rather_than_guessing(monkeypatch):
    """A wrong "supported" sends someone to debug a model that was never going to load."""
    import poolhouse.setup as setup

    monkeypatch.setattr(setup, "_arches", lambda binary: set())
    named = {f.name for f in setup.look()}
    assert not any(n.startswith("architecture") for n in named), \
        "it must not claim an architecture is missing when it could not read any"


def test_an_architecture_is_only_claimed_when_the_names_were_read(monkeypatch, tmp_path):
    import poolhouse.setup as setup
    fake = tmp_path / "llama-server"
    fake.write_text("#!/bin/sh\necho usage\n")
    fake.chmod(0o755)
    monkeypatch.setattr("poolhouse.serve.binary.find_binary", lambda *a, **k: fake)

    monkeypatch.setattr(setup, "_arches", lambda binary: {"qwen3moe", "gemma4"})
    found = [f for f in setup.look() if f.name == "architecture qwen4exp"]
    assert found and not found[0].good
    assert "master" in found[0].note

    monkeypatch.setattr(setup, "_arches", lambda binary: {"qwen4exp", "gemma4"})
    found = [f for f in setup.look() if f.name == "architecture qwen4exp"]
    assert found and found[0].good


def test_nothing_is_run_without_being_asked(capsys, monkeypatch):
    """A wizard that fixes things by itself is a wizard nobody can safely run twice."""
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    ask([Finding(name="a thing", good=False, said="wrong", fix=["rm", "-rf", "/"])])
    assert ran == [], "a non-interactive run must offer, not act"
    assert "fix: rm -rf /" in capsys.readouterr().out


def test_a_fix_runs_only_when_it_is_wanted(capsys, monkeypatch):
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a[0]))
    ask([Finding(name="a thing", good=False, said="wrong", fix=["echo", "yes"])], yes=True)
    assert ran == [["echo", "yes"]]

    ran.clear()
    ask([Finding(name="a thing", good=True, said="fine", fix=["echo", "no"])], yes=True)
    assert ran == [], "nothing is run to fix what is not broken"


def test_it_never_asks_for_a_password():
    """Where a change needs root, sudo prompts on the terminal itself. Anything that reads a
    password in order to pass it along is doing what this deliberately does not."""
    import inspect

    import poolhouse.setup as setup

    source = inspect.getsource(setup)
    for reading in ("getpass", "password=", "input(\"password", "sudo -S", "--stdin"):
        assert reading not in source, f"{reading!r} would mean handling a password"


def test_the_behaviours_are_the_ones_that_surprise_people():
    """Each of these was once somebody's bug report. A default nobody was told about is
    indistinguishable from a fault when it does something unexpected."""
    named = {b.name for b in BEHAVIOURS}
    assert "a model that is not here" in named, "downloading tens of gigabytes unprompted"
    assert "repeated questions" in named, "an answer returning instantly reads as a fault"
    assert "a port that is taken" in named, "a server appearing somewhere unasked"
    assert all(b.does for b in BEHAVIOURS)
    assert all(b.why or b.setting for b in BEHAVIOURS), \
        "saying what happens without saying why it matters is just more output"


def test_explaining_says_what_happens_and_how_to_change_it(capsys):
    explain()
    said = capsys.readouterr().out
    assert "downloads, the first time it is asked for" in said
    assert "change it:" in said


def test_the_wizard_runs_end_to_end(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code = main(["--quiet"])
    assert code in (0, 1)
    assert "what this machine can do" in capsys.readouterr().out


def _server_answering(tmp_path, help_text: str):
    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\nif [ \"$1\" = --help ]; then cat <<'HELP'\n"
                      + help_text + "HELP\nfi\nexit 0\n")
    binary.chmod(0o755)
    return binary


def test_a_flag_the_build_lacks_is_reported_with_the_nearest_it_has(tmp_path, monkeypatch):
    """`--draft-max` became `--spec-draft-n-max`; a build listing only the new name must
    say so here, before a load finds out."""
    import poolhouse.serve.binary as binary_module
    import poolhouse.setup as setup
    from poolhouse.serve import backend

    monkeypatch.setattr(backend, "_HELP", {})
    monkeypatch.setattr(setup, "_arches", lambda binary: {"gemma4"})
    stand_in = _server_answering(tmp_path, "-m, --model FNAME   model path\n"
                                           "--kv-unified   one cache for every slot\n")
    monkeypatch.setattr(binary_module, "find_binary", lambda *a, **k: stand_in)
    found = [f for f in setup.look() if f.name == "flags this build lacks"]
    assert found and not found[0].good
    assert "--kv-unified-per-slot" in found[0].said
    assert "no --kv-unified-per-slot, it has --kv-unified" in found[0].note
    assert "--spec-draft-n-max" in found[0].said


def test_a_build_that_answers_every_flag_is_not_mentioned(tmp_path, monkeypatch):
    import poolhouse.serve.binary as binary_module
    import poolhouse.setup as setup
    from poolhouse.serve import backend
    from poolhouse.serve.backend import LlamaServerBackend
    from poolhouse.serve.emitted import emitted_flags

    monkeypatch.setattr(backend, "_HELP", {})
    monkeypatch.setattr(setup, "_arches", lambda binary: {"gemma4"})
    quiet = _server_answering(tmp_path, "-m, --model FNAME   model path\n")
    everything = "\n".join(f"{flag} X   described" for flag in
                           emitted_flags(LlamaServerBackend(binary=quiet))) + "\n"
    stand_in = _server_answering(tmp_path, everything)
    monkeypatch.setattr(backend, "_HELP", {})
    monkeypatch.setattr(binary_module, "find_binary", lambda *a, **k: stand_in)
    assert not [f for f in setup.look() if f.name == "flags this build lacks"]


def test_a_missing_architecture_offers_the_build_fix(monkeypatch, tmp_path):
    """`poolhouse-serve build` is the fix for a release lagging master by an architecture --
    `poolhouse-setup --yes` runs whatever a finding's `fix` names, so this is what makes
    `--yes` actually get a missing architecture rather than just naming the gap."""
    import poolhouse.setup as setup
    fake = tmp_path / "llama-server"
    fake.write_text("#!/bin/sh\necho usage\n")
    fake.chmod(0o755)
    monkeypatch.setattr("poolhouse.serve.binary.find_binary", lambda *a, **k: fake)

    monkeypatch.setattr(setup, "_arches", lambda binary: {"gemma4"})
    found = [f for f in setup.look() if f.name == "architecture qwen4exp"]
    assert found and found[0].fix == ["poolhouse-serve", "build"]

    monkeypatch.setattr(setup, "_arches", lambda binary: {"gemma4", "qwen4exp"})
    found = [f for f in setup.look() if f.name == "architecture qwen4exp"]
    assert found and found[0].fix == []


def test_lacking_flags_offer_the_build_fix(tmp_path, monkeypatch):
    import poolhouse.serve.binary as binary_module
    import poolhouse.setup as setup
    from poolhouse.serve import backend

    monkeypatch.setattr(backend, "_HELP", {})
    monkeypatch.setattr(setup, "_arches", lambda binary: {"gemma4"})
    stand_in = _server_answering(tmp_path, "-m, --model FNAME   model path\n")
    monkeypatch.setattr(binary_module, "find_binary", lambda *a, **k: stand_in)
    found = [f for f in setup.look() if f.name == "flags this build lacks"]
    assert found and found[0].fix == ["poolhouse-serve", "build"]


def test_a_managed_build_is_labelled_by_commit_and_age_not_by_version(tmp_path):
    """BUILD.json is what tells a managed build apart from a brew one -- read it rather than
    guessing from the path, since both sit at some arbitrary directory."""
    import poolhouse.setup as setup

    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    (tmp_path / "BUILD.json").write_text(json.dumps({
        "commit": "abc1234", "built_at": "2020-01-01T00:00:00+00:00", "version": "x"}))

    label = setup._build_label(str(binary))
    assert "abc1234" in label
    assert "d old" in label, "2020 is certainly more than a day before today"


def test_an_unmanaged_build_is_labelled_by_its_own_version_output(tmp_path):
    import poolhouse.setup as setup

    binary = tmp_path / "llama-server"
    binary.write_text("#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'version: 0.3.0'; fi\n")
    binary.chmod(0o755)

    assert setup._build_label(str(binary)) == "version: 0.3.0"


def test_a_build_that_prints_no_help_is_not_accused_of_lacking_anything(tmp_path, monkeypatch):
    """Unknown is not none: a stand-in that says nothing must not be reported as lacking
    every flag there is."""
    import poolhouse.serve.binary as binary_module
    import poolhouse.setup as setup
    from poolhouse.serve import backend

    monkeypatch.setattr(backend, "_HELP", {})
    monkeypatch.setattr(setup, "_arches", lambda binary: set())
    silent = tmp_path / "llama-server"
    silent.write_text("#!/bin/sh\nexit 0\n")
    silent.chmod(0o755)
    monkeypatch.setattr(binary_module, "find_binary", lambda *a, **k: silent)
    assert not [f for f in setup.look() if f.name == "flags this build lacks"]


def test_every_command_the_package_installs_is_looked_for_on_path(monkeypatch, tmp_path):
    """A new entry point is invisible until `pip install -e . && pyenv rehash`; a queue
    died on `command not found` for it. Setup names each missing command and the line."""
    import shutil

    import poolhouse.setup as setup

    monkeypatch.setattr(setup, "checkout", lambda: tmp_path)
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **k: None if name == "poolhouse-store"
                        else f"/opt/bin/{name}")
    found = [f for f in setup.look() if f.name == "commands on PATH"]
    assert found and not found[0].good
    assert "poolhouse-store" in found[0].said
    assert "poolhouse-serve" not in found[0].said, "only what is missing is named"
    assert found[0].fix == ["pip", "install", "-e", str(tmp_path)]

    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/opt/bin/{name}")
    found = [f for f in setup.look() if f.name == "commands on PATH"]
    assert found and found[0].good
    assert "poolhouse-serve" in found[0].note


def test_the_commands_are_read_from_the_install_and_the_checkouts_pyproject(monkeypatch,
                                                                           tmp_path):
    """The installed metadata says what was installed; the checkout's pyproject says what
    should be -- and the difference is the command nobody can run yet."""
    import poolhouse.setup as setup

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "poolhouse"\n[project.scripts]\n'
        'poolhouse-serve = "poolhouse.serve.cli:main"\n'
        'poolhouse-newthing = "poolhouse.newthing:main"\n')
    monkeypatch.setattr(setup, "checkout", lambda: tmp_path)
    names = setup._scripts()
    assert {"poolhouse-serve", "poolhouse-setup", "poolhouse-store"} <= set(names)
    assert "poolhouse-newthing" in names, "named in the checkout, not yet installed"
    assert names == sorted(set(names))


def test_the_printed_report_names_the_missing_command_and_the_line(monkeypatch, capsys,
                                                                   tmp_path):
    import shutil

    import poolhouse.setup as setup

    monkeypatch.setattr(setup, "checkout", lambda: tmp_path)
    monkeypatch.setattr(shutil, "which",
                        lambda name, *a, **k: None if name == "poolhouse-jobs"
                        else f"/opt/bin/{name}")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert setup.main(["--quiet"]) == 1
    out = capsys.readouterr().out
    assert "poolhouse-jobs" in out
    assert f"fix: pip install -e {tmp_path}" in out


def test_the_models_finding_names_each_cache_and_its_size(monkeypatch, capsys, tmp_path):

    hub = tmp_path / "hf" / "hub" / "models--maker--big-GGUF" / "snapshots" / "abc"
    hub.mkdir(parents=True)
    (hub / "big-Q4_K_M.gguf").write_bytes(b"x" * (2 * 1024 * 1024))
    mine = tmp_path / "models"
    mine.mkdir()
    (mine / "small.gguf").write_bytes(b"y" * (1024 * 1024 + 1024))
    monkeypatch.setattr("poolhouse.hub.default_roots",
                        lambda root: [tmp_path / "hf" / "hub", mine, tmp_path / "absent"])
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    main(["--quiet"])
    out = capsys.readouterr().out
    line = next(one for one in out.splitlines() if "models on this machine" in one)
    assert f"2 file(s): {tmp_path / 'hf' / 'hub'} 2M, {mine} 1M" in line
    assert "absent" not in line
    assert f"HF_HOME={os.environ['HF_HOME']} names the Hub cache" in out



def _speech(monkeypatch, *, asr=(), tts=(), vad=()):
    """Stand named providers on the three speech registries; each entry is (name, health)."""
    from poolhouse import speech as package
    from poolhouse.speech import Registry

    def stub(name, health):
        class One:
            def __init__(self):
                self.name = name

            def probe(self):
                return health

            def start(self):
                return None

            def stop(self):
                return None

        return One

    for attr, entries in (("ASR", asr), ("TTS", tts), ("VAD", vad)):
        registry: Registry = Registry(kind=attr.lower())
        for name, health in entries:
            registry.register(name, stub(name, health))
        monkeypatch.setattr(package, attr, registry)


def test_the_speech_findings_name_the_engine_and_the_one_that_would_be_used(monkeypatch):
    from poolhouse.speech import ProviderHealth

    _speech(monkeypatch,
            asr=[("faster-whisper", ProviderHealth.ok("small.en")),
                 ("transformers-whisper", ProviderHealth.missing("torch not installed"))],
            vad=[("energy", ProviderHealth.ok("energy"))])
    found = {f.name: f for f in look()}
    heard = found["speech recognition"]
    assert heard.good and "faster-whisper (small.en)" in heard.said
    assert "uses faster-whisper" in heard.note
    assert "torch not installed" in heard.note, "what the other one still wants"
    assert not heard.fix, "nothing to install when one already works"


def test_a_machine_with_no_speech_engine_is_told_what_would_add_one(monkeypatch):
    from poolhouse.speech import ProviderHealth

    _speech(monkeypatch,
            asr=[("faster-whisper", ProviderHealth.missing("faster-whisper is not installed"))],
            tts=[("system", ProviderHealth.missing("no system speech binary"))],
            vad=[("energy", ProviderHealth.ok("energy"))])
    found = {f.name: f for f in look()}
    assert not found["speech recognition"].good
    assert found["speech recognition"].fix == ["pip", "install", "poolhouse[speech]"]
    assert "faster-whisper is not installed" in found["speech recognition"].note
    assert not found["speech synthesis"].good
    assert found["voice activity"].good, "the energy detector needs nothing installed"


def test_the_speech_findings_are_printed_with_the_rest(monkeypatch, capsys):
    from poolhouse.speech import ProviderHealth

    _speech(monkeypatch, vad=[("energy", ProviderHealth.ok("energy"))])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    main(["--quiet"])
    out = capsys.readouterr().out
    assert "voice activity: energy (energy)" in out
    assert "speech recognition: nothing on this machine" in out


def test_the_firewall_finding_names_the_inbound_rule_and_how_to_add_it():
    """Windows answers no member until TCP 8770 is let in. `_firewall_rule_present` asks ``netsh``;
    where there is none, every rule reads absent. The node's own ports belong to `device_host`."""
    from poolhouse.fleet.windows_firewall import line, rules
    from poolhouse.setup import _firewall_finding

    finding = _firewall_finding()
    named = [name for name, _, _ in rules()]

    assert isinstance(finding, Finding)
    assert finding.name == "firewall"
    assert finding.root is True
    assert isinstance(finding.good, bool)
    assert all(name in finding.said for name in named)
    assert bool(finding.fix) is not finding.good
    if not finding.good:
        assert finding.said.startswith("no inbound rule for ")
        assert finding.fix == ["cmd", "/c", line()]
        assert "protocol=TCP" in finding.fix[2] and "UDP" not in finding.fix[2]
        assert "Windows blocks inbound TCP 8770" in finding.note
    else:
        assert finding.said.startswith("inbound rules present: ")
        assert finding.note == ""



def test_a_rule_netsh_cannot_confirm_reads_as_absent():
    from poolhouse.setup import _firewall_rule_present

    assert _firewall_rule_present("poolhouse: a rule no machine has") is False


def test_the_gguf_finding_only_counts_gguf_files(monkeypatch, tmp_path):
    """A safetensors checkpoint on disk does not mean llama-server has anything to load."""
    import poolhouse.setup as setup

    mine = tmp_path / "models"
    mine.mkdir()
    (mine / "small.safetensors").write_bytes(b"x")
    monkeypatch.setattr("poolhouse.hub.default_roots", lambda root: [mine, tmp_path / "absent"])
    found = {f.name: f for f in setup.look()}
    assert not found["a GGUF on disk"].good
    assert found["a GGUF on disk"].fix == ["poolhouse-models", "fetch", "hf:owner/repo/file.gguf"]

    (mine / "small-Q4_K_M.gguf").write_bytes(b"y")
    found = {f.name: f for f in setup.look()}
    assert found["a GGUF on disk"].good
    assert found["a GGUF on disk"].said == "1 file(s)"


# -- the store ------------------------------------------------------------

def test_store_finding_says_there_is_nothing_to_open_yet(tmp_path):
    from poolhouse.setup import _store_finding

    found = _store_finding(tmp_path / "runs.ladybug")
    assert found.good
    assert "no store yet" in found.said


def test_store_finding_opens_a_real_store(tmp_path):
    from poolhouse.graph.cypher import CypherStore
    from poolhouse.setup import _store_finding

    path = tmp_path / "runs.ladybug"
    with CypherStore(path):
        pass
    found = _store_finding(path)
    assert found.good
    assert found.said == f"{path} opens"


def test_store_finding_reports_a_store_that_will_not_open(tmp_path):
    from poolhouse.setup import _store_finding

    path = tmp_path / "runs.ladybug"
    path.write_bytes(b"not a ladybug database")
    found = _store_finding(path)
    assert not found.good
    assert found.fix == ["poolhouse-store", "check", str(path)]


# -- the ports --------------------------------------------------------------

def _free_tcp_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_ports_finding_says_free_when_nothing_listens():
    from poolhouse.setup import _ports_finding

    found = _ports_finding(_free_tcp_port(), None)
    assert found.good
    assert found.said == "free"


def test_ports_finding_says_what_holds_a_taken_port():
    from poolhouse.setup import _ports_finding

    http_port = _free_tcp_port()
    holder = socket.socket()
    holder.bind(("127.0.0.1", http_port))
    holder.listen(1)
    try:
        found = _ports_finding(http_port, None)
    finally:
        holder.close()
    assert not found.good
    assert str(http_port) in found.said
    assert ["lsof", "-nP", f"-iTCP:{http_port}"] == found.fix


def test_ports_finding_says_held_by_the_given_daemon():
    from poolhouse.setup import _ports_finding

    found = _ports_finding(8770, {"name": "testbox", "ok": True})
    assert found.good
    assert "this machine's own daemon" in found.said


# -- the fleet ----------------------------------------------------------------

@pytest.fixture
def pool_answers(tmp_path, monkeypatch):
    """PLUMBING ONLY: a canned node reply about the pool's compute services (the one stand-in, at its wire); no node runs here."""
    from poolhouse.fleet import service_pool, tls

    identity = tls.identity(tmp_path / "tls", "here")
    monkeypatch.setattr(tls, "local", lambda: identity)
    rows: list[dict] = []

    def node(_state, method, params=None):
        if method == "service_members":
            return {"pool": "a" * 32, "services": rows}
        raise ValueError("not authorized")

    monkeypatch.setattr(service_pool.node_health, "call", node)
    return rows


def test_fleet_finding_says_the_node_does_not_answer_and_how_to_join(monkeypatch, tmp_path):
    from poolhouse import node_health
    from poolhouse.setup import _fleet_findings

    monkeypatch.setattr(node_health, "default_state", lambda: tmp_path / "no-node")  # a state directory no node runs in: the real refusal
    found = {f.name: f for f in _fleet_findings(port=_free_tcp_port())}
    assert found.keys() == {"fleet: joined", "ports"}
    assert not found["fleet: joined"].good
    assert "node must answer" in found["fleet: joined"].said
    assert "poolhouse-pool join --host" in found["fleet: joined"].note


def test_fleet_finding_says_the_daemon_does_not_answer(pool_answers):
    from poolhouse.setup import _fleet_findings

    found = {f.name: f for f in _fleet_findings(port=_free_tcp_port())}
    assert found["fleet: joined"].good
    assert not found["fleet: daemon"].good
    assert found["fleet: daemon"].fix == ["poolhouse"]
    assert "fleet: seen" not in found
    assert found["ports"].good


@pytest.mark.slow
def test_fleet_findings_see_a_real_daemon_on_loopback(tmp_path, pool_answers):
    from poolhouse.fleet.api import Daemon, make_handler
    from poolhouse.fleet.daemon import load_or_create_token
    from poolhouse.fleet.jobs import JobRunner
    from poolhouse.fleet.launch import wait_for_health
    from poolhouse.http import Server
    from poolhouse.setup import _fleet_findings

    root = tmp_path / "traind"
    (root / "files").mkdir(parents=True)
    runner = JobRunner(root)
    port = _free_tcp_port()
    httpd = Server(("127.0.0.1", port), make_handler(Daemon(runner, root / "files", load_or_create_token(root), "setuptestbox")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert wait_for_health(port, seconds=30) is not None, "the first /health samples telemetry and is slow on a loaded machine"
        found = {f.name: f for f in _fleet_findings(port=port)}
    finally:
        runner.shutdown()
        httpd.shutdown()
        httpd.server_close()
    assert found["fleet: joined"].good
    assert found["fleet: daemon"].good and "setuptestbox" in found["fleet: daemon"].said
    assert found["fleet: seen"].good and found["fleet: seen"].said == "listed with 1 device(s) that answer"
    assert found["ports"].good and "this machine's own daemon" in found["ports"].said


def test_a_named_build_that_is_not_here_is_a_finding(monkeypatch, tmp_path):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path))
    monkeypatch.setenv("POOLHOUSE_LLAMA_BUILD", "gone")
    found = [f for f in look() if f.name == "llama-server"]
    assert len(found) == 1 and not found[0].good
    assert "'gone'" in found[0].said
    assert "--name gone" in found[0].note


def test_a_model_root_that_cannot_be_read_is_a_finding(monkeypatch):
    from poolhouse.fleet import models

    def unreadable(root):
        raise PermissionError(13, "Permission denied", "/models")

    monkeypatch.setattr(models, "caches", unreadable)
    found = [f for f in look() if f.name == "models on this machine"]
    assert len(found) == 1 and not found[0].good
    assert "Permission denied" in found[0].said


def test_a_checkout_with_no_installed_metadata_reads_its_pyproject(monkeypatch, tmp_path):
    from importlib.metadata import PackageNotFoundError

    import poolhouse.setup as setup

    def not_installed(name):
        raise PackageNotFoundError(name)

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "poolhouse"\n[project.scripts]\npoolhouse-newthing = "m:main"\n')
    monkeypatch.setattr(setup, "checkout", lambda: tmp_path)
    monkeypatch.setattr(setup, "distribution", not_installed)
    assert setup._scripts() == ["poolhouse-newthing"]


def test_a_fix_naming_a_hostile_path_runs_as_one_argument_and_no_shell(tmp_path, monkeypatch):
    """A path with shell metacharacters reaches the command as data: nothing it contains runs."""
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "executed"
    hostile = tmp_path / "a; touch executed; $(touch executed) `touch executed` & b"
    hostile.mkdir()
    ask([Finding(name="a thing", good=False, said="wrong", fix=["ls", "-d", str(hostile)])], yes=True)
    ask([Finding(name="a thing", good=False, said="wrong", fix=["pwd"], cwd=str(hostile))], yes=True)
    assert not marker.exists() and not (hostile / "executed").exists() and not list(tmp_path.glob("**/executed"))
