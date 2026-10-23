"""The real-model harness: every test of what a model says, picks or ranks goes through it.

A test asks for a model by use (``real.serving("chat")``, ``real.leased("judge")``) and gets a real llama-server on a
pinned small GGUF, leased the normal way (`ServerManager` under the node's lease table, or the guard's `native.Leased`)
and never started by hand. Nothing here skips: a missing model, a missing binary, another model on the compute or a
machine without the GPU a test needs fails that test and names the cause and the command that fixes it.

Name the plugin in a test module (``pytest_plugins = ["real_model"]``), mark the module ``pytest.mark.real_model`` and
run it with ``--real-model`` (``scripts/test all tests/FILE.py --real-model``). Shape assertions only for a small
model: it is weak, and a test that needs a right answer asks for the use that pins a model able to give one.
"""

from __future__ import annotations

import os
import platform
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from poolhouse.client import Client
from poolhouse.guard import native
from poolhouse.serve import LlamaServerBackend, ServerManager, ServerSpec, free_port
from poolhouse.serve.process import every_server
from tests.model_approval import approved

pytest_plugins = ["serve_node"]

LEASE_S = 300
"""Seconds a lease may wait for the broker to admit it and the server to load."""
BUSY_WAIT_S = 300
"""Seconds a test waits for a server that is already running to finish before it fails naming that server."""


@dataclass(frozen=True)
class Pin:
    """One GGUF file as the Hugging Face cache holds it, with the size the Hub publishes."""

    file: str
    repo: str
    size: int

    @property
    def ref(self) -> str:
        return f"hf:{self.repo}/{self.file}"

    @property
    def fetch(self) -> str:
        return f"poolhouse-models fetch {self.ref}"


@dataclass(frozen=True)
class Use:
    """What a test needs a model for, the file pinned for it, and how the server is started for it."""

    name: str
    why: str
    pin: Pin
    companion: Pin | None = None
    spec: dict[str, Any] = field(default_factory=dict)


GRANITE = Pin("granite-4.0-h-350m-Q4_K_M.gguf", "ibm-granite/granite-4.0-h-350m-GGUF", 222_662_560)
QWEN_4B = Pin("Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "lmstudio-community/Qwen3-4B-Instruct-2507-GGUF", 2_497_280_448)
EMBEDDER = Pin("embeddinggemma-2-Q8_0.gguf", "ggml-org/embeddinggemma-2-GGUF", 309_855_456)
RERANKER = Pin("qwen3-reranker-0.6b-q8_0.gguf", "ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF", 639_153_184)
SEER = Pin("Qwen3VL-2B-Instruct-Q4_K_M.gguf", "Qwen/Qwen3-VL-2B-Instruct-GGUF", 1_107_409_952)
EYES = Pin("mmproj-Qwen3VL-2B-Instruct-F16.gguf", "Qwen/Qwen3-VL-2B-Instruct-GGUF", 819_394_848)
GUARD = Pin("Qwen3Guard-Gen-0.6B.Q4_K_M.gguf", "mradermacher/Qwen3Guard-Gen-0.6B-GGUF", 484_220_416)

USES = {use.name: use for use in (
    Use("chat", "any small model that answers a chat: lease, queue, stop, smoke", GRANITE),
    Use("decide", "logprob decisions; the model is weak, so shape assertions only", GRANITE),
    Use("judge", "a decision that has to be right (the guard's judge)", QWEN_4B),
    Use("tool_calling", "a model that chooses and calls a tool", QWEN_4B),
    Use("embedding", "vectors for a ranking", EMBEDDER, spec={"embedding": True}),
    Use("rerank", "a reranker scoring documents against a query", RERANKER, spec={"reranking": True}),
    Use("vision", "a model that sees: weights and the projector beside them", SEER, companion=EYES),
    Use("other", "a second, different model, to tell two models apart", GUARD),
)}


def hub_roots(account: Path) -> list[Path]:
    """The Hugging Face hub cache folders a pinned file is looked for in, the machine's own account first."""
    named = [Path(os.environ[key]).expanduser() / sub for key, sub in (("HF_HUB_CACHE", ""), ("HF_HOME", "hub"))
             if os.environ.get(key)]
    return [*named, account / ".cache" / "huggingface" / "hub"]


def locate(pin: Pin, roots: list[Path]) -> Path | None:
    """The pinned file under the first root that holds it, as a snapshot path; None when none does."""
    for root in roots:
        for pattern in (f"models--*/snapshots/*/{pin.file}", f"models--*/snapshots/*/*/{pin.file}"):
            for hit in sorted(root.glob(pattern)):
                if hit.is_file():
                    return hit
    return None


def model_problem(use: Use, which: Pin, roots: list[Path]) -> str:
    """Why the file pinned for ``use`` cannot be used, with the command that fixes it; "" when it can."""
    found = locate(which, roots)
    if found is None:
        return (f"REAL MODEL MISSING: the {use.name} model {which.file} ({which.size / 1e6:.0f} MB; {use.why}) is not "
                f"in the Hugging Face cache ({', '.join(str(r) for r in roots)}). A test of a model never runs on a "
                f"stand-in and never skips; fetch it once: {which.fetch}")
    if found.stat().st_size != which.size:
        return (f"REAL MODEL WRONG: {found} is {found.stat().st_size} bytes and the pin for {use.name} says "
                f"{which.size}, so it is a partial download or another revision. Fetch it again: {which.fetch}")
    return ""


def server_problem(account: Path) -> tuple[Path | None, str]:
    """The llama-server this machine runs models with, else why there is none and how to get one."""
    named = os.environ.get("POOLHOUSE_TEST_LLAMA_SERVER")
    candidates = [Path(named)] if named else [account / ".poolhouse" / "llama.cpp" / "current" / "llama-server",
                                              Path(shutil.which("llama-server") or "")]
    for path in candidates:
        if path.is_file():
            return path.resolve(), ""
    return None, ("LLAMA-SERVER MISSING: no llama-server binary "
                  + (f"at POOLHOUSE_TEST_LLAMA_SERVER={named}" if named else
                     "at ~/.poolhouse/llama.cpp/current or on PATH")
                  + ". A test of a model needs a real one and never skips: poolhouse-serve llama-cpp update "
                    "(or point POOLHOUSE_TEST_LLAMA_SERVER at a build).")


def computing() -> list[Any]:
    """The llama-servers running now; a defunct one (killed, its parent has not yet reaped it) computes nothing."""
    return [one for one in every_server() if not (isinstance(one, dict) and one.get("defunct"))]


def busy_problem(running: list[Any]) -> str:
    """Why a model cannot start beside the servers already running, each named; "" when none is."""
    if not running:
        return ""
    named = ", ".join(f"pid {one.get('pid')} on port {one.get('port')} serving {Path(str(one.get('model'))).name}"
                      if isinstance(one, dict) else str(one) for one in running)
    return (f"COMPUTE BUSY: another llama-server is running ({named}) and stayed up for {BUSY_WAIT_S} s. A test "
            "does not load a model beside someone else's work, so it waits for it to finish and then fails. "
            "`poolhouse-serve status` shows it, `poolhouse-serve down MODEL` stops one that is yours.")


def gpu_problem(system: str, machine: str) -> str:
    """Why this machine has no Apple GPU for a test that needs one; "" on a Mac."""
    if system == "Darwin":
        return ""
    return (f"NO GPU: this test needs the Apple GPU (Metal) and the macOS sandbox; this machine is {system} on "
            f"{machine}, which has neither, so it cannot pass here. Run it on a Mac with --real-model.")


def stop(problem: str) -> None:
    """Fail the running test with ``problem`` when there is one."""
    if problem:
        pytest.fail(problem, pytrace=False)


@dataclass
class Served:
    """A real server a test holds: its lease, the model file behind it and the binary that runs it."""

    info: Any
    model: Path
    binary: Path
    use: str

    @property
    def url(self) -> str:
        return str(self.info.base_url)

    def client(self) -> Client:
        return Client(self.url)


class Real:
    """What the ``real`` fixture hands a test: finds a pinned model, takes the lease, yields the server."""

    def __init__(self, account: Path, request: Any, monkeypatch: Any, roots: list[Path] | None = None,
                 busy_wait: float = BUSY_WAIT_S) -> None:
        self.account, self.request, self.monkeypatch = account, request, monkeypatch
        self.roots = roots if roots is not None else hub_roots(account)
        self.busy_wait = busy_wait

    def model(self, use: str) -> Path:
        """The file pinned for ``use`` (``$POOLHOUSE_TEST_GGUF`` names the one for "chat"); fails when it is missing."""
        wanted = USES[use]
        named = os.environ.get("POOLHOUSE_TEST_GGUF") if use == "chat" else ""
        if named:
            stop("" if Path(named).is_file() else f"POOLHOUSE_TEST_GGUF={named} is not a file")
            return Path(named)
        stop(model_problem(wanted, wanted.pin, self.roots))
        return locate(wanted.pin, self.roots)  # type: ignore[return-value]

    def projector(self, use: str) -> Path:
        """The vision projector pinned beside the model for ``use``; fails when it is missing."""
        wanted = USES[use]
        assert wanted.companion is not None, f"the {use} use has no projector"
        stop(model_problem(wanted, wanted.companion, self.roots))
        return locate(wanted.companion, self.roots)  # type: ignore[return-value]

    def binary(self) -> Path:
        """The llama-server binary; fails when this machine has none."""
        found, problem = server_problem(self.account)
        stop(problem)
        return found  # type: ignore[return-value]

    def idle(self, wait: float | None = None) -> None:
        """Waits up to ``wait`` seconds (``busy_wait`` unless given) for another llama-server to finish (a second
        request waits for the first), then fails naming it: one thing computes at a time."""
        deadline = time.monotonic() + (self.busy_wait if wait is None else wait)
        while computing() and time.monotonic() < deadline:
            time.sleep(2)
        stop(busy_problem(computing()))

    def require_metal_gpu(self) -> None:
        """Fails, saying there is no GPU, on a machine that is not a Mac."""
        stop(gpu_problem(platform.system(), platform.machine()))

    def _ready(self, use: str) -> tuple[Path, Path]:
        binary, model = self.binary(), self.model(use)
        self.idle()
        self.request.getfixturevalue("serve_leases")
        self.monkeypatch.setenv("LLAMA_CPP_SERVER", str(binary))
        # `serve()` keeps its manager, and that manager the lease table it was made with: one made in an earlier
        # test would ask a node whose state root that test removed
        self.monkeypatch.setattr("poolhouse.serve.manager._DEFAULT", None)
        return binary, model

    def prepared(self, use: str) -> Path:
        """The model file pinned for ``use``, with the machine ready to serve it (the binary named, the compute idle,
        the lease table in place), for code that leases it through `serve()` itself."""
        return self._ready(use)[1]

    @contextmanager
    def serving(self, use: str, *, sandboxed: bool = False, **spec: Any) -> Iterator[Served]:
        """A real server on the model pinned for ``use``, leased through the broker and released on exit.

        ``spec`` overrides `ServerSpec` fields (``context``, ``n_gpu_layers``, ``mmproj=None`` for no projector).
        """
        binary, model = self._ready(use)
        wanted = USES[use]
        fields = {"context": 2048, **wanted.spec, **spec}
        if wanted.companion is not None and "mmproj" not in fields:
            fields["mmproj"] = self.projector(use)
        held = ServerManager(LlamaServerBackend(binary=binary, sandboxed=sandboxed))
        info = held.lease(ServerSpec(model=model, port=free_port(), **fields), timeout=LEASE_S)
        try:
            yield Served(info, model, binary, use)
        finally:
            held.release(info)

    @contextmanager
    def pointer(self) -> Iterator[str]:
        """The released Strands decider (pointer backend, in this process) under the GPU hold; yields its model id.

        Fails, never skips, naming the command that fixes it, when the checkpoint is not in the cache.
        """
        from poolhouse.decide import router
        from poolhouse.decide.pins import STRANDS_V19
        from poolhouse.serve import gpu

        cache = self.roots[-1]
        if cache.is_dir():
            self.monkeypatch.setenv("HF_HUB_CACHE", str(cache))
        why = router.unavailable("pointer", router.Config(backend="pointer"))
        stop(f"REAL MODEL MISSING: the Strands decider {STRANDS_V19.name} cannot run here ({why}). A test of a model never runs on a "
             "stand-in and never skips; fetch it once: poolhouse-decide fetch" if why else "")
        self.idle()
        self.request.getfixturevalue("serve_leases")
        with gpu.hold("test of the Strands decider", wait_s=BUSY_WAIT_S):
            yield STRANDS_V19.name

    @contextmanager
    def leased(self, use: str, **options: Any) -> Iterator[native.Leased]:
        """The guard's own lease (`native.Leased`) on the model pinned for ``use``, closed on exit."""
        _binary, model = self._ready(use)
        approved(model)  # the broker's own process asks a person before it serves a file it has not seen
        held = native.Leased(model=str(model), lease_timeout=LEASE_S, **options)
        try:
            yield held
        finally:
            held.close()


@pytest.fixture
def real(_real_home, request, monkeypatch) -> Real:
    """The harness for one test; the machine's own account is read for the models, never written."""
    return Real(_real_home.state.parent, request, monkeypatch)

