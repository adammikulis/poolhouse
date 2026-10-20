"""Installation proof binds all public metadata and two actual Unix connections."""

import base64
import copy
import hashlib
import hmac
import json
import os
import socket
import ssl
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest

from poolhouse.fleet import app_identity, producer_bootstrap as bootstrap, tls

NONCE = "1" * 64


@pytest.fixture
def directory():
    with tempfile.TemporaryDirectory(prefix="bootstrap-", dir="/tmp") as name:
        root = Path(name).resolve()
        root.chmod(0o700)
        (root / app_identity.PROOF_KEY_FILE).write_text("ab" * 32)
        (root / app_identity.PROOF_KEY_FILE).chmod(0o600)
        yield root


@contextmanager
def connection(directory, name, identity=None):
    path = str(directory / name)
    stop, errors = threading.Event(), []
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(path)
        listener.listen()
        listener.settimeout(5)

        def serve():
            try:
                channel, _ = listener.accept()
                with channel:
                    channel.settimeout(5)
                    if identity is not None:
                        with tls.server_context(identity).wrap_socket(channel, server_side=True):
                            stop.wait(5)
                    else:
                        stop.wait(5)
            except (OSError, ValueError) as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(5)
                client.connect(path)
                if identity is None:
                    yield client
                else:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.check_hostname = False
                    context.load_verify_locations(cafile=str(identity.certfile))
                    with context.wrap_socket(client, server_hostname="localhost") as secure:
                        yield secure
        finally:
            stop.set()
            thread.join(6)
            assert not thread.is_alive()
            assert not errors


def current(directory, identity):
    return {
        "certificate": identity.beacon,
        "control_path": str(directory / "control.sock"),
        "daemon": {
            "pid": os.getpid(),
            "started": psutil.Process().create_time(),
            "instance": "a" * 64,
        },
        "pool": "b" * 64,
        "runtime": "c" * 64,
    }


def holder(directory):
    identity = tls.identity(directory / "tls", "bootstrap-test")
    metadata = current(directory, identity)
    return bootstrap.BootstrapHolder(directory, current=lambda: copy.deepcopy(metadata)), identity


def test_actual_plain_bootstrap_then_pinned_tls_control(directory):
    owned, identity = holder(directory)
    reply = owned.answer(NONCE)
    # Independent canonical frame verifies every field, not a challenge-only health proof.
    encoded = json.dumps(
        reply["binding"], sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    assert (
        reply["mac"]
        == hmac.new(
            bytes.fromhex("ab" * 32), b"poolhouse/producer-bootstrap/v1\n" + encoded, hashlib.sha256
        ).hexdigest()
    )
    with connection(directory, "bootstrap.sock") as plain:
        binding = bootstrap.authenticate(directory, NONCE, reply, plain)
    with connection(directory, "control.sock", identity) as secure:
        public = bootstrap.verify_control(binding, secure)
    assert public == reply["binding"]
    assert "ab" * 32 not in json.dumps(reply)
    public["daemon"]["instance"] = "d" * 64
    assert reply["binding"]["daemon"]["instance"] == "a" * 64


@pytest.mark.parametrize(
    "field",
    [
        "version",
        "nonce",
        "certificate",
        "control_path",
        "daemon",
        "pool",
        "runtime",
        "issued",
        "expires",
    ],
)
def test_every_binding_field_is_mac_bound(directory, field):
    owned, _ = holder(directory)
    reply = owned.answer(NONCE)
    changes = {
        "version": 2,
        "nonce": "2" * 64,
        "certificate": base64.b64encode(b"other").decode(),
        "control_path": "/tmp/other.sock",
        "daemon": {**reply["binding"]["daemon"], "instance": "f" * 64},
        "pool": "d" * 64,
        "runtime": "e" * 64,
        "issued": reply["binding"]["issued"] - 1,
        "expires": reply["binding"]["expires"] - 1,
    }
    reply["binding"][field] = changes[field]
    with (
        connection(directory, "bootstrap.sock") as plain,
        pytest.raises(bootstrap.BootstrapUnavailable),
    ):
        bootstrap.authenticate(directory, NONCE, reply, plain)


@pytest.mark.parametrize(
    "change", ["extra", "missing", "nan", "bool", "expired", "future", "long", "bad-mac"]
)
def test_malformed_stale_or_substituted_proofs_fail(directory, change):
    owned, _ = holder(directory)
    reply = owned.answer(NONCE)
    if change == "extra":
        reply["binding"]["extra"] = "injected"
    elif change == "missing":
        del reply["binding"]["runtime"]
    elif change == "nan":
        reply["binding"]["daemon"]["started"] = float("nan")
    elif change == "bool":
        reply["binding"]["daemon"]["pid"] = True
    elif change == "expired":
        reply["binding"]["expires"] = int(time.time()) - 1
    elif change == "future":
        reply["binding"]["issued"] += 60
        reply["binding"]["expires"] += 60
    elif change == "long":
        reply["binding"]["expires"] += 60
    else:
        reply["mac"] = "0" * 64
    with pytest.raises(bootstrap.BootstrapUnavailable):
        bootstrap.authenticate(directory, NONCE, reply, None)


def test_other_installation_secret_and_old_nonce_cannot_authenticate(directory):
    owned, _ = holder(directory)
    reply = owned.answer(NONCE)
    (directory / app_identity.PROOF_KEY_FILE).write_text("cd" * 32)
    with (
        connection(directory, "bootstrap.sock") as plain,
        pytest.raises(bootstrap.BootstrapUnavailable),
    ):
        bootstrap.authenticate(directory, NONCE, reply, plain)
    with pytest.raises(bootstrap.BootstrapUnavailable):
        bootstrap.authenticate(directory, "2" * 64, reply, None)


def test_control_der_and_process_must_both_match(directory):
    owned, identity = holder(directory)
    reply = owned.answer(NONCE)
    with connection(directory, "bootstrap.sock") as plain:
        binding = bootstrap.authenticate(directory, NONCE, reply, plain)
        with pytest.raises(bootstrap.BootstrapUnavailable):
            bootstrap.verify_control(binding, plain)
    other = tls.identity(directory / "other-tls", "other-test")
    with (
        connection(directory, "control.sock", other) as secure,
        pytest.raises(bootstrap.BootstrapUnavailable),
    ):
        bootstrap.verify_control(binding, secure)
    (directory / "control.sock").unlink()
    with connection(directory, "control.sock", identity) as secure:
        binding["daemon"]["started"] += 1
        with pytest.raises(bootstrap.BootstrapUnavailable):
            bootstrap.verify_control(binding, secure)


@pytest.mark.parametrize(
    "kind", ["missing", "short", "extra", "uppercase", "symlink", "hardlink", "public", "directory"]
)
def test_existing_identity_is_never_created_repaired_or_followed(directory, kind):
    path = directory / app_identity.PROOF_KEY_FILE
    path.unlink()
    if kind == "short":
        path.write_text("short")
    elif kind == "extra":
        path.write_text("ab" * 32 + "\n")
    elif kind == "uppercase":
        path.write_text("AB" * 32)
    elif kind in ("symlink", "hardlink"):
        target = directory / "target"
        target.write_text("ab" * 32)
        target.chmod(0o600)
        if kind == "symlink":
            path.symlink_to(target)
        else:
            os.link(target, path)
    elif kind == "public":
        path.write_text("ab" * 32)
        path.chmod(0o644)
    elif kind == "directory":
        path.mkdir()
    if path.is_file() and kind not in ("public", "symlink"):
        path.chmod(0o600)
    before = path.lstat() if path.exists() or path.is_symlink() else None
    with pytest.raises(bootstrap.BootstrapUnavailable):
        bootstrap.read_existing(directory)
    assert (path.lstat() if path.exists() or path.is_symlink() else None) == before


def test_replaced_file_during_read_and_symlink_ancestor_denied(directory, monkeypatch):
    original = os.read

    def replace(fd, size):
        data = original(fd, size)
        path = directory / app_identity.PROOF_KEY_FILE
        replacement = directory / "replacement"
        replacement.write_text("ab" * 32)
        replacement.chmod(0o600)
        replacement.replace(path)
        return data

    monkeypatch.setattr(os, "read", replace)
    with pytest.raises(bootstrap.BootstrapUnavailable):
        bootstrap.read_existing(directory)
    monkeypatch.setattr(os, "read", original)
    link = directory / "linked"
    link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(bootstrap.BootstrapUnavailable):
        bootstrap.read_existing(link)


def test_holder_signs_only_current_exact_metadata_and_valid_nonce(directory):
    owned, _ = holder(directory)
    with pytest.raises(bootstrap.BootstrapUnavailable):
        owned.answer({"runtime": "d" * 64})
    owned.current = lambda: {"certificate": "caller-chosen"}
    with pytest.raises(bootstrap.BootstrapUnavailable):
        owned.answer(NONCE)


def test_valid_mac_still_requires_actual_bootstrap_process(directory):
    owned, _ = holder(directory)
    metadata = owned.current()
    metadata["daemon"]["started"] += 1
    owned.current = lambda: metadata
    reply = owned.answer(NONCE)
    with (
        connection(directory, "bootstrap.sock") as plain,
        pytest.raises(bootstrap.BootstrapUnavailable),
    ):
        bootstrap.authenticate(directory, NONCE, reply, plain)


def test_unmodified_signed_reply_expires(directory, monkeypatch):
    owned, _ = holder(directory)
    reply = owned.answer(NONCE)
    monkeypatch.setattr(bootstrap.time, "time", lambda: reply["binding"]["expires"] + 1)
    with pytest.raises(bootstrap.BootstrapUnavailable):
        bootstrap.authenticate(directory, NONCE, reply, None)


def test_public_identity_directory_is_refused_without_chmod(directory):
    directory.chmod(0o755)
    with pytest.raises(bootstrap.BootstrapUnavailable):
        bootstrap.read_existing(directory)
    assert directory.stat().st_mode & 0o777 == 0o755
