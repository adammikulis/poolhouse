"""Fixed installed bootstrap holder; only public metadata crosses its private launch channel."""

from __future__ import annotations

import base64
import copy
import json
import os
import secrets
import socket
import stat
import subprocess
import threading
import time
from pathlib import Path

from poolhouse import home, maintenance, real_machine, runtime
from poolhouse.files import stat_signature
from poolhouse.json_objects import unique_object
from poolhouse.platform import start_process

from . import producer_bootstrap as bootstrap, tls
from .job_records import process_started
from .producer_control import receive_frame, send_frame

LOCATOR = "producer-control.json"
DEADLINE = 10.0
ENTRY = (
    "import sys;from poolhouse.fleet.producer_holder import child;sys.exit(child(int(sys.argv[1])))"
)

_BOOTSTRAPS = set()
_BOOTSTRAP_LOCK = threading.RLock()


def _before_fork():
    _BOOTSTRAP_LOCK.acquire()


def _after_parent():
    _BOOTSTRAP_LOCK.release()


def _after_child():
    global _BOOTSTRAP_LOCK
    for channel in _BOOTSTRAPS:
        channel.close()
    _BOOTSTRAPS.clear()
    _BOOTSTRAP_LOCK = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_before_fork, after_in_parent=_after_parent, after_in_child=_after_child
    )


def installed_root():
    """The fixed installed daemon location, independent of SDK arguments and environment."""
    return home.account_roots()[0] / "traind"


def _location(root):
    path = Path(root)
    if not path.is_absolute() or ".." in path.parts:
        raise bootstrap.BootstrapUnavailable("The installed software location is invalid.")
    descriptors, anchors = [], []
    try:
        at = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(at)
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at)
            descriptors.append(child)
            anchors.append((at, name, child, stat_signature(os.fstat(child))[:4]))
            at = child
        directory = os.fstat(at)
        if directory.st_uid != os.geteuid() or stat.S_IMODE(directory.st_mode) != 0o700:
            raise bootstrap.BootstrapUnavailable("The installed software location is not private.")
        fd = os.open(LOCATOR, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=at)
        descriptors.append(fd)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_size > 4096
            or stat.S_IMODE(before.st_mode) & 0o077
        ):
            raise bootstrap.BootstrapUnavailable("The software connection locator is unavailable.")
        data = os.read(fd, 4097)
        if stat_signature(os.fstat(fd)) != stat_signature(
            before
        ) or stat_signature(
            os.stat(LOCATOR, dir_fd=at, follow_symlinks=False)
        ) != stat_signature(before):
            raise bootstrap.BootstrapUnavailable("The software connection locator changed.")
        for parent, name, child, original in anchors:
            if (
                stat_signature(os.fstat(child))[:4] != original
                or stat_signature(os.stat(name, dir_fd=parent, follow_symlinks=False))[:4]
                != original
            ):
                raise bootstrap.BootstrapUnavailable("The software connection directory changed.")
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
    row = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    if (
        not isinstance(row, dict)
        or set(row) != {"version", "bootstrap_path"}
        or type(row["version"]) is not int
        or row["version"] != 1
    ):
        raise bootstrap.BootstrapUnavailable("The software connection locator is invalid.")
    location = row["bootstrap_path"]
    if (
        not isinstance(location, str)
        or len(location.encode()) > 103
        or "\0" in location
        or not Path(location).is_absolute()
        or ".." in Path(location).parts
        or not Path(location).is_relative_to(root)
    ):
        raise bootstrap.BootstrapUnavailable("The software connection locator is invalid.")
    return location


def _connect(path, deadline):
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        channel.set_inheritable(False)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Software authentication expired.")
        channel.settimeout(remaining)
        channel.connect(str(path))
        return channel
    except (OSError, ValueError):
        channel.close()
        raise


def local_anchor(root, nonce, *, deadline):
    """Internal holder operation; production supplies only its fixed installed root."""
    # Read only nonce/public metadata before authenticating the software installation.
    with _connect(_location(root), deadline) as channel:
        send_frame(channel, {"nonce": nonce}, deadline=deadline)
        reply = receive_frame(channel, deadline=deadline)
        binding = bootstrap.authenticate(root, nonce, reply, channel)
    context = tls.certificate_context(binding["certificate"], context_type=tls.ExactContext)
    context.expected_der = base64.b64decode(binding["certificate"], validate=True)
    with (
        _connect(binding["control_path"], deadline) as plain,
        context.wrap_socket(plain, server_hostname=None) as secure,
    ):
        return bootstrap.verify_control(binding, secure)


def serve_bootstrap(channel, *, root):
    """Private holder channel, with an isolated root injected only by trusted test constructors."""
    deadline = time.monotonic() + DEADLINE
    request = receive_frame(channel, deadline=deadline)
    if (
        set(request) != {"version", "nonce"}
        or type(request["version"]) is not int
        or request["version"] != 1
        or not isinstance(request["nonce"], str)
    ):
        raise bootstrap.BootstrapUnavailable("Invalid software bootstrap request.")
    binding = local_anchor(root, request["nonce"], deadline=deadline)
    send_frame(
        channel,
        {
            "version": 1,
            "nonce": request["nonce"],
            "binding": binding,
            "holder": {"pid": os.getpid(), "started": process_started(os.getpid())},
        },
        deadline=deadline,
    )
    ack = receive_frame(channel, deadline=deadline)
    if ack != {"ack": request["nonce"]}:
        raise bootstrap.BootstrapUnavailable("Invalid software bootstrap acknowledgement.")


def child(descriptor):
    """The constant installed launch entry accepts an inherited descriptor alone."""
    with socket.socket(fileno=descriptor) as channel:
        channel.set_inheritable(False)
        try:
            real_machine.require("Installed software producer authentication")
            serve_bootstrap(channel, root=installed_root())
            return 0
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            # A closed private channel gives the SDK one fixed failure; never raw details.
            return 1


def _launch(command):
    """Trusted fixed launcher; isolated tests supply their own non-installed child."""
    deadline = time.monotonic() + DEADLINE
    nonce = secrets.token_hex(32)
    with _BOOTSTRAP_LOCK:
        parent, child_channel = socket.socketpair()
        _BOOTSTRAPS.update((parent, child_channel))
    process, started = None, None
    try:
        parent.set_inheritable(False)
        child_channel.set_inheritable(False)
        process = start_process(
            [*command, str(child_channel.fileno())],
            pass_fds=(child_channel.fileno(),),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=runtime.environment({"PATH": os.defpath}),
        )
        with _BOOTSTRAP_LOCK:
            child_channel.close()
        started = process_started(process.pid)
        if started is None:
            raise bootstrap.BootstrapUnavailable("The installed software holder is unavailable.")
        send_frame(parent, {"version": 1, "nonce": nonce}, deadline=deadline)
        reply = receive_frame(parent, deadline=deadline)
        if (
            set(reply) != {"version", "nonce", "binding", "holder"}
            or type(reply["version"]) is not int
            or reply["version"] != 1
            or reply["nonce"] != nonce
            or not isinstance(reply["holder"], dict)
            or set(reply["holder"]) != {"pid", "started"}
            or type(reply["holder"]["pid"]) is not int
            or type(reply["holder"]["started"]) not in (int, float)
            or reply["holder"] != {"pid": process.pid, "started": started}
            or process.poll() is not None
            or process_started(process.pid) != started
        ):
            raise bootstrap.BootstrapUnavailable("The installed software holder proof changed.")
        binding = bootstrap._binding(reply["binding"], nonce)
        send_frame(parent, {"ack": nonce}, deadline=deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or process.wait(timeout=remaining) != 0:
            raise bootstrap.BootstrapUnavailable(
                "The installed software holder did not acknowledge."
            )
        return copy.deepcopy(binding)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        raise bootstrap.BootstrapUnavailable(
            "The installed software connection is unavailable."
        ) from exc
    finally:
        with _BOOTSTRAP_LOCK:
            parent.close()
            child_channel.close()
            _BOOTSTRAPS.difference_update((parent, child_channel))
        # An unreaped retained Popen is our actual OS child even when birth inspection failed.
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=DEADLINE)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=DEADLINE)


def launch_anchor():
    """Use only the account's verified managed interpreter and fixed installed entry."""
    expected = home.account_roots()[0] / "runtimes" / runtime.identity()
    if runtime.directory() != expected:
        raise bootstrap.BootstrapUnavailable("The installed software runtime location changed.")
    with maintenance.read():
        chosen = runtime.selected()
        if chosen is None:
            raise bootstrap.BootstrapUnavailable("The installed software runtime is unavailable.")
        return _launch([str(chosen.python), "-I", "-B", "-c", ENTRY])
