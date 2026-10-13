"""Serving Hugging Face weights with vLLM or SGLang, behind the same lease as llama-server.

A spec whose ``engine`` is ``"vllm"`` or ``"sglang"`` is served by that engine's OpenAI
server, run as ``python -m`` in the interpreter this process runs on. ``context`` is the
longest sequence, ``parallel`` the most concurrent sequences, and the weights are split
across every CUDA device the machine has.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from poolhouse.credentials import child_environment
from poolhouse.serve.backend import (
    Lease,
    ServerBackend,
    ServerFailed,
    ServerInfo,
    ServerSpec,
    claim_port,
    fetched,
    launch,
    never_downloaded,
    server_log,
)
from poolhouse.serve.downloads import PYTHON_ENGINE_FLAGS, unlisted_flag

__all__ = ["ENGINES", "SGLangBackend", "VllmBackend", "cuda_devices", "engine_environment"]


def engine_environment() -> dict[str, str]:
    """The environment of an engine: no tokens, and Hugging Face downloads and checks switched off."""
    return {**child_environment(), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}


def _arguments(spec: ServerSpec) -> tuple[str, list[str]]:
    """The model as a path on this machine, and the extra arguments, once both are checked."""
    named = str(never_downloaded(spec.model, "model"))
    if not Path(named).exists():
        raise ServerFailed(f"the model {named} must be fetched before serving; an engine is never "
                           f"asked to download by repository name (`poolhouse-models fetch`)")
    if flag := unlisted_flag(list(spec.extra_args), PYTHON_ENGINE_FLAGS):
        raise ServerFailed(f"{flag} is not a flag Poolhouse passes to {spec.engine or 'an engine'}")
    return named, list(spec.extra_args)


def cuda_devices() -> int:
    """How many CUDA devices NVML reports; 0 when there is no NVML."""
    try:
        import pynvml
    except ImportError:
        return 0
    try:
        pynvml.nvmlInit()
        return int(pynvml.nvmlDeviceGetCount())
    except (OSError, pynvml.NVMLError):
        return 0


class _PythonEngine(ServerBackend):
    """An engine that is a Python module with an OpenAI-compatible server."""

    name = ""

    def start(self, spec: ServerSpec, *, lease: Lease, timeout: float = 600.0,
              **starting: bool) -> ServerInfo:
        """Launch and wait until the server answers. Raises ``ServerFailed``; the lease's
        ``starting`` switches are llama-server's and do not apply."""
        if spec.draft:
            raise ServerFailed(f"{self.name} serves no draft model; drop draft from the spec")
        spec = replace(spec, model=fetched(spec.model, "model"))
        argv = self.command(spec)
        claim_port(spec, lease)
        log_path = server_log(self.name, spec.port)
        process, base_url, load_s = launch(argv, lease, log_path=log_path,
                                           timeout=timeout, env=engine_environment())
        return ServerInfo(base_url=base_url, port=spec.port, pid=process.pid, backend=self.name,
                          log_path=log_path, load_s=load_s, process=process)


class VllmBackend(_PythonEngine):
    """vLLM's OpenAI server."""

    name = "vllm"

    def command(self, spec: ServerSpec) -> list[str]:
        model, extra = _arguments(spec)
        argv = [sys.executable, "-m", "vllm.entrypoints.openai.api_server",
                "--model", model, "--host", "127.0.0.1", "--port", str(spec.port),
                "--dtype", "auto", "--max-num-seqs", str(max(1, spec.parallel))]
        if spec.context > 0:
            argv += ["--max-model-len", str(spec.context)]
        if cuda_devices() > 1:
            argv += ["--tensor-parallel-size", str(cuda_devices())]
        return [*argv, *extra]


class SGLangBackend(_PythonEngine):
    """SGLang's server."""

    name = "sglang"

    def command(self, spec: ServerSpec) -> list[str]:
        model, extra = _arguments(spec)
        argv = [sys.executable, "-m", "sglang.launch_server", "--model-path", model,
                "--host", "127.0.0.1", "--port", str(spec.port),
                "--max-running-requests", str(max(1, spec.parallel))]
        if spec.context > 0:
            argv += ["--context-length", str(spec.context)]
        if cuda_devices() > 1:
            argv += ["--tp", str(cuda_devices())]
        return [*argv, *extra]


ENGINES: dict[str, ServerBackend] = {"vllm": VllmBackend(), "sglang": SGLangBackend()}
"""Each engine a spec may name, by that name."""
