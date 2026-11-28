"""What holds an install back: the off switch and LAN only stop every download that starts by itself, a status check
installs nothing, and an install in progress holds up only what it is writing. Every installer and download here is
plumbing only: a recorded call, or a canned module marked as such."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import home, keystore, lock, opencode_install, provision
from poolhouse.fleet import autostart_check, environment as environments, llama, runtime_paths
from poolhouse.fleet.settings import Settings
from poolhouse.serve import binary, build_recover
from poolhouse.serve.policy import LocalPolicy
from poolhouse.speech import asr, vad
from poolhouse.speech.protocols import ProviderError
from poolhouse.workspace import device_metadata

SRC = Path(__file__).resolve().parent.parent / "src"


def lan_only(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    Settings(download_sources="lan").save(root / "settings.json")


@pytest.fixture(params=["off", "lan"])
def held_back(request, monkeypatch):
    """The switch off, or this device's download source LAN only."""
    if request.param == "off":
        monkeypatch.setenv(provision.SWITCH, "off")
        return provision.SWITCH
    monkeypatch.delenv(provision.SWITCH, raising=False)
    monkeypatch.delenv("POOLHOUSE_DAEMON_ROOT", raising=False)
    lan_only(runtime_paths.default_root())
    return "LAN only"


def test_a_daemon_with_its_own_root_is_held_to_the_settings_saved_there(monkeypatch, tmp_path):
    monkeypatch.delenv(provision.SWITCH, raising=False)
    monkeypatch.setenv("POOLHOUSE_DAEMON_ROOT", "")
    custom = tmp_path / "custom-daemon"
    lan_only(custom)
    runtime_paths.configure(custom)
    assert "LAN only" in provision.download_refusal()


def test_opencode_is_not_downloaded_when_held_back(held_back, monkeypatch):
    monkeypatch.setattr(opencode_install, "keep", lambda *a, **k: pytest.fail("downloaded"))
    with pytest.raises(opencode_install.InstallFailed, match=held_back):
        opencode_install.ensure()


def test_the_app_server_is_not_downloaded_when_held_back(held_back, monkeypatch, tmp_path):
    monkeypatch.setattr(llama, "find_server", lambda vendor: None)
    monkeypatch.setattr(llama, "_tokens", lambda: ("macos-arm64",))
    monkeypatch.setattr(llama, "_download", lambda *a: pytest.fail("downloaded"))
    with pytest.raises(llama.LlamaError, match=held_back):
        llama.ensure_server(tmp_path / "an-app-root", sources="both")


def test_a_rejected_server_binary_is_not_replaced_by_a_build_or_download_when_held_back(held_back, monkeypatch, tmp_path):
    monkeypatch.setattr(build_recover, "can_build_from_source", lambda: False)
    monkeypatch.setattr(build_recover, "build_from_release", lambda args: pytest.fail("downloaded"))
    pins = LocalPolicy(tmp_path / "pins.json")
    server = tmp_path / "llama-server"
    server.write_text("# PLUMBING ONLY: a server file that is never run\n")
    assert binary.vetted(server, pins) == server
    server.write_text("# PLUMBING ONLY: changed by nobody that vouches for it\n")
    with pytest.raises(binary.BinaryTampered, match=held_back):
        binary.vetted(server, pins)


def test_a_job_python_and_its_libraries_are_not_fetched_when_held_back(held_back, monkeypatch, tmp_path):
    monkeypatch.setattr(environments.net, "default", lambda: pytest.fail("asked the network"))
    made = environments.Environment(tmp_path)
    monkeypatch.setattr(made, "pip", lambda *a, **k: pytest.fail("installed"))
    monkeypatch.setattr(made, "_own_wheel", lambda: pytest.fail("read the wheel"))
    with pytest.raises(OSError, match=held_back):
        made.fetch_python()
    with pytest.raises(OSError, match=held_back):
        made.locked_install(next(one for one in environments.CATALOG if one.name == "bench"))


def hub(tmp_path, loaded):
    """torch's hub, recording what it is asked to load: plumbing only."""
    return SimpleNamespace(hub=SimpleNamespace(get_dir=lambda: str(tmp_path / "hub"),
                                               load=lambda repo, name, **how: loaded.append(repo) or (object(), None)))


def test_the_voice_detector_is_not_downloaded_when_held_back_and_runs_from_a_held_copy(held_back, monkeypatch, tmp_path):
    loaded = []
    monkeypatch.setitem(sys.modules, "torch", hub(tmp_path, loaded))
    with pytest.raises(ProviderError, match=held_back):
        vad.SileroVAD().start()
    assert loaded == []
    (tmp_path / "hub" / f"snakers4_silero-vad_{vad.SILERO_COMMIT}").mkdir(parents=True)
    vad.SileroVAD().start()
    assert len(loaded) == 1


def test_the_speech_models_load_only_what_is_held_when_held_back(held_back, monkeypatch):
    asked = []

    def module(name, extra):
        def snapshot(model, *, local_files_only):
            asked.append((model, local_files_only))
            raise OSError("not held: plumbing only")

        return SimpleNamespace(WhisperModel=lambda model, **how: asked.append((model, how.get("local_files_only"))),
                               snapshot_download=snapshot)

    monkeypatch.setattr(provision, "module", module)
    asr.FasterWhisperASR("small.en")._load()
    with pytest.raises(ProviderError, match=held_back):
        asr._kept_only("openai/whisper-small.en")
    assert asked == [("small.en", True), ("openai/whisper-small.en", True)]


@pytest.fixture
def installs(monkeypatch, tmp_path):
    """A virtual environment's lock and installer, in the place of this one's: every install is recorded, none made."""
    monkeypatch.delenv(provision.SWITCH, raising=False)
    asked = []
    monkeypatch.setattr(provision, "target", lambda: tmp_path / "bin" / "python")
    monkeypatch.setattr(provision, "_lock", lambda python: tmp_path / "install.lock")
    monkeypatch.setattr(provision, "_install",
                        lambda python, locked: asked.append(locked.read_text()) or subprocess.CompletedProcess([], 1, "", "no"))
    monkeypatch.setattr(provision, "WAIT_S", 1.0)
    monkeypatch.setattr(provision, "_holds", lambda want: False)   # as where nothing locked is installed yet
    return SimpleNamespace(asked=asked, lock=tmp_path / "install.lock", writing=tmp_path / "install.lock.writing")


@pytest.mark.parametrize("switch", ["", "off"])
@pytest.mark.parametrize("writing", [None, "torch\nnumpy"])
def test_a_library_that_is_here_is_used_while_another_is_being_installed(installs, monkeypatch, switch, writing):
    monkeypatch.setenv(provision.SWITCH, switch)
    monkeypatch.delitem(sys.modules, "cryptography", raising=False)
    with lock.only_one(installs.lock, note="installing the torch libraries"):
        if writing:
            installs.writing.write_text(writing)
        provision.ensure("memory", "cryptography")
    assert installs.asked == []


def test_a_library_that_is_being_written_is_waited_for(installs, monkeypatch):
    monkeypatch.delitem(sys.modules, "cryptography", raising=False)
    with lock.only_one(installs.lock, note="installing the memory libraries"):
        installs.writing.write_text("cryptography")
        with pytest.raises(provision.Unavailable):
            provision.ensure("memory", "cryptography")


@pytest.mark.parametrize("probe", ["available", "backend"])
def test_asking_about_the_keystore_installs_nothing_and_keeping_a_key_may(installs, monkeypatch, probe):
    monkeypatch.setitem(sys.modules, "keyring", None)   # as on a device that never installed it
    said = getattr(keystore.Keystore(), probe)()
    assert said is False if probe == "available" else said.startswith("none:")
    assert installs.asked == []
    keystore.fetch_libraries()
    assert len(installs.asked) == 1


@pytest.mark.parametrize("check", [lambda: device_metadata.current.__wrapped__(), lambda: autostart_check.prepared()])
def test_a_status_check_installs_nothing_to_name_the_device(installs, monkeypatch, check):
    monkeypatch.setitem(sys.modules, "machineid", None)
    check()
    assert installs.asked == []
    with pytest.raises(RuntimeError):
        home.device_id()
    assert len(installs.asked) == 1, "what needs the identity still installs it"


LOOK = """
import sys
sys.argv = ["agent-identity", sys.argv[1]]
from poolhouse import provision
seen = []
provision.ensure = lambda *a, **k: seen.append(provision.download_refusal())
importlib = __import__("importlib")
importlib.import_module("poolhouse.workspace.github_identity")
print("held back" if seen[0] else "free")
"""


@pytest.mark.parametrize("command,said", [("check", "held back"), ("setup", "free")])
def test_checking_the_agent_identity_installs_nothing(monkeypatch, command, said):
    importlib.import_module("jwt")
    found = os.pathsep.join([str(SRC), *(path for path in sys.path if path.endswith("site-packages"))])
    env = {"PYTHONPATH": found, "HOME": str(Path.home()), "PATH": os.environ["PATH"], "POOLHOUSE_HOME": str(home.home())}
    done = subprocess.run([sys.executable, "-B", "-c", LOOK, command], env=env, capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == said, done.stderr
