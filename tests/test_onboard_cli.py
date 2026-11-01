"""`poolhouse-pool bootstrap | share | fetch | signing` as separate processes on loopback with throwaway state."""

import json
import os
import signal
import subprocess
import sys

import pytest

FP1 = "1" * 64

@pytest.fixture(autouse=True)
def needs_spake2():
    pytest.importorskip("spake2")



@pytest.fixture(autouse=True)
def needs_cryptography():
    pytest.importorskip("cryptography")


def env_for(root):
    root.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), "POOLHOUSE_HOME": str(root),
            "PYTHON_KEYRING_BACKEND": "onboard_support.FileKeyring",
            "POOLHOUSE_TEST_KEYRING": str(root / "keyring.json"), "DISPLAY": ":0",
            "PYTHONUNBUFFERED": "1"}
    prepared = subprocess.run(
        [sys.executable, "-c", "from poolhouse.keystore import Keystore; Keystore().provision()"],
        env=env, capture_output=True, text=True, timeout=30, check=False)
    assert prepared.returncode == 0, prepared.stderr
    return env


def fleet(env, *args, stdin=None, timeout=60):
    return subprocess.run([sys.executable, "-m", "poolhouse.pool_cli", *args], env=env,
                          capture_output=True, text=True, timeout=timeout, input=stdin,
                          check=False)


def spawn(env, *args):
    return subprocess.Popen([sys.executable, "-m", "poolhouse.pool_cli", *args], env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def stop(proc):
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream:
            stream.close()






def read_document(proc):
    """One pretty-printed JSON document from a process's output."""
    text = ""
    while True:
        line = proc.stdout.readline()
        if not line:
            raise AssertionError(f"process ended: {proc.stderr.read()}")
        text += line
        try:
            json.loads(text)
            return text
        except ValueError:
            continue












def test_ssh_without_files_says_so(tmp_path):
    env = env_for(tmp_path / "x")
    ssh = fleet(env, "bootstrap", "--ssh", "pi@10.0.0.9", "--json", "--state",
                str(tmp_path / "x" / "s"))
    assert ssh.returncode == 2 and "--share" in json.loads(ssh.stdout)["error"]


def test_bootstrap_prints_an_offer_with_a_pinned_certificate(tmp_path):
    env = env_for(tmp_path / "x")
    share = tmp_path / "share"
    share.mkdir()
    (share / "poolhouse-0.2-py3-none-any.whl").write_bytes(b"wheel" * 100)
    proc = spawn(env, "bootstrap", "--share", str(share), "--valid", "60s", "--json",
                 "--state", str(tmp_path / "x" / "s"))
    try:
        doc = json.loads(read_document(proc))
        assert doc["url"].startswith("https://127.0.0.1:") and doc["url"].endswith(
            f"#fp={doc['certificate']}")
        assert doc["files"] == ["poolhouse-0.2-py3-none-any.whl"]
        assert "--pinnedpubkey" in doc["command"]
        state = tmp_path / "x" / "s"
        assert (state / "signing.json").stat().st_mode & 0o077 == 0
        assert not (state / "signing.key").exists() and not (state / "signing.key.enc").exists()
        assert json.loads((state / "signing.json").read_text())["store"] == "keystore"
    finally:
        stop(proc)
    refused = fleet(env, "bootstrap", "--json", "--state", str(tmp_path / "x" / "s"))
    assert refused.returncode == 2












def test_cli_fixture_provisions_only_its_file_keyring(tmp_path):
    root = tmp_path / "isolated-device"
    env = env_for(root)
    assert env["PYTHON_KEYRING_BACKEND"] == "onboard_support.FileKeyring"
    assert json.loads((root / "keyring.json").read_text())
    assert json.loads((root / "keystore" / "provisioned.json").read_text())["at"]
