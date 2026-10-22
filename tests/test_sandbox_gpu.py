"""A real llama-server on the GPU inside the sandbox: it answers, it holds the GPU, and the
same confinement refuses a read outside its allow-list and a connection out.

Runs with ``--real-model`` (tests/real_model.py) on a Mac: the Apple GPU and the macOS sandbox, the pinned small model
and no other llama-server running. Anywhere else the test fails and says there is no GPU; it never skips.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse import sandbox
from poolhouse.client import wait_for_health
from poolhouse.sandbox import policies
from poolhouse.serve import free_port
from poolhouse.serve.process import pid_exists

pytest_plugins = ["tests.sandbox_kit", "real_model"]

pytestmark = [pytest.mark.slow, pytest.mark.real_model]


def metal_box(real, request):
    """The binary and the model of a Mac that can run the sandboxed GPU server; fails, naming what is missing, otherwise."""
    real.require_metal_gpu()
    state = sandbox.backend().available()
    if not state.ok:
        pytest.fail(f"NO SANDBOX: the macOS sandbox is not available here: {state.reason}", pytrace=False)
    request.getfixturevalue("seatbelt")
    binary, model = real.binary(), real.model("chat")
    real.idle()
    return binary, model


def test_a_confined_server_answers_with_the_model_on_the_gpu(real, request):
    metal_box(real, request)
    with real.serving("chat", sandboxed=True, context=512, n_gpu_layers=99) as served:
        assert served.info.pid and pid_exists(served.info.pid)
        answer = served.client().chat([{"role": "user", "content": "Say hi."}], max_tokens=16)
        assert answer.content.strip()


def test_every_layer_is_offloaded_to_the_gpu_inside_the_profile(real, request, tmp_path):
    binary, model = metal_box(real, request)
    port = free_port()
    policy = replace(policies.model_server(binary, model.parent, gpu=True),
                     read=(*policies.model_server(binary, model.parent).read,
                           str(Path(os.path.realpath(model)).parent)))
    argv, _tag, _chosen = sandbox.wrapped(
        [str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(port), "-ngl", "99",
         "-c", "512", "--no-warmup", "-lv", "4"], policy)
    log = tmp_path / "server.log"
    with log.open("wb") as sink:
        process = subprocess.Popen(argv, stdout=sink, stderr=subprocess.STDOUT, env=dict(policy.env),
                                   cwd=str(Path(os.path.realpath(binary)).parent),
                                   start_new_session=True)
    try:
        assert wait_for_health(f"http://127.0.0.1:{port}", timeout=120,
                               is_alive=lambda: process.poll() is None)
        text = log.read_text(errors="replace")
        loaded = re.search(r"offloaded (\d+)/(\d+) layers to GPU", text)
        assert loaded and loaded[1] == loaded[2] and "MTL0" in text
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_the_same_confinement_refuses_a_read_outside_and_a_connection_out(real, request, tmp_path):
    binary, model = metal_box(real, request)
    decoy = tmp_path / "home" / ".ssh" / "id_rsa"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("the-secret-contents")
    policy = replace(policies.model_server(binary, model.parent, gpu=True),
                     exec=(os.path.realpath(binary), "/usr/bin/curl"))
    read = sandbox.run(["/usr/bin/curl", "-sS", f"file://{decoy}"], policy)
    out = sandbox.run(["/usr/bin/curl", "-sS", "-m", "5", "http://192.0.2.1/"], policy)
    assert read.returncode != 0 and "the-secret-contents" not in read.stdout
    assert any(d["target"] == str(decoy) for d in read.denials)
    assert out.returncode != 0
    assert any(d["operation"] == "network-outbound" for d in out.denials)
    assert Path(binary).exists()
