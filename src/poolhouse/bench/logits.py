"""The log probabilities a GGUF file gives a fixed text, from llama.cpp's ``llama-perplexity``, and how two sets differ.

llama-server returns probabilities only for the tokens it generates (``n_probs``; ``echo`` is refused), so scoring a
given text goes through llama.cpp's own tool, built from the commit of the active managed build by the sandboxed
compile the server's build uses. ``--save-all-logits`` writes, for the second half of every context-sized chunk, the
log probabilities over the whole vocabulary, rounded to 16 bits and floored 16 nats under the largest logit.
"""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import bench, net, sandbox
from poolhouse.sandbox import policies
from poolhouse.sandbox.policy import Limits, Net, Policy
from poolhouse.serve import llamacpp_compile, llamacpp_state
from poolhouse.serve.llamacpp_upstream import Upstream

__all__ = ["BLOCK", "TOOL", "Header", "Scoring", "ToolFailed", "argv", "blocks", "build_tool", "compare", "header", "run",
           "tool_path"]

TOOL = "llama-perplexity"
MAGIC = b"_logits_"
#: Scored tokens in one block. Neighbouring tokens share a context, so intervals resample blocks, not tokens.
BLOCK = 64
#: Reference probabilities under e^-16 are left out of the divergence, as the tool itself leaves them out.
FLOOR = -16.0


class ToolFailed(RuntimeError):
    """The tool could not be found, built or run, or what it wrote cannot be compared."""


@dataclass(frozen=True)
class Scoring:
    """How a text is scored: the context of a chunk and how many, the K and V cache type, whether every layer and
    operation stays off the GPU, the tokens decoded together (1 decodes one at a time), and more words for the
    tool."""

    context: int
    chunks: int
    cpu: bool = False
    cache: str = "q8_0"
    ubatch: int = 512
    extra: tuple[str, ...] = ()


def active_build() -> dict[str, Any]:
    """The ``BUILD.json`` record of the managed llama.cpp build in use, empty when none is."""
    build = llamacpp_state.active()
    return dict(build.info) if build else {}


def tool_path(commit: str = "") -> Path:
    """Where the tool built from ``commit`` (the active build's when empty) is kept."""
    commit = commit or str(active_build().get("commit") or "")
    if not commit:
        raise ToolFailed("no managed llama.cpp build is active; 'poolhouse-serve llama-cpp ensure' makes one, "
                         "or name a binary with --tool")
    return bench.home_dir() / "tools" / commit / (TOOL + (".exe" if os.name == "nt" else ""))


def build_tool(say: Callable[[str], None] = print, *, jobs: int = 0) -> Path:
    """The tool built from the active build's commit: fetched through the host policy, compiled in the sandbox with
    the server's own cmake arguments, kept under the bench's home. Loads no model."""
    info = active_build()
    made = tool_path(str(info.get("commit") or ""))
    if made.is_file():
        return made
    made.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="work-", dir=made.parent))
    try:
        (work / "scratch").mkdir()
        llamacpp_compile.checkout(Upstream(), str(info["commit"]), work / "src", net.default())
        job = llamacpp_compile.Job(work / "src", work / "scratch", info.get("build"), str(info["commit"]),
                                   jobs or max(1, (os.cpu_count() or 2) - 2), llamacpp_compile.toolchain(),
                                   target=TOOL)
        shutil.copy2(llamacpp_compile.compile_source(job, say), made)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return made


def argv(tool: Path, model: Path, corpus: Path, out: Path, how: Scoring) -> list[str]:
    """The command that writes ``model``'s log probabilities of ``corpus`` to ``out``. The batch stays at 2048, so a
    long context is decoded in parts."""
    command = [str(tool), "-m", str(model), "-f", str(corpus), "-c", str(how.context),
               "-b", str(min(2048, how.context)), "-ub", str(min(how.ubatch, how.context)), "--chunks", str(how.chunks),
               "-ctk", how.cache, "-ctv", how.cache, "--save-all-logits", str(out)]
    if how.cpu:
        command += ["-ngl", "0", "--device", "none", "--no-op-offload"]
    return [*command, *how.extra]


def _policy(command: Sequence[str], model_files: Sequence[Path], corpus: Path, out: Path, wall: float) -> Policy:
    """The model-server policy without a network, reading the model's shards and the corpus, writing ``out``'s
    directory."""
    base = policies.model_server(command[0], model_files[0].parent)
    reads = {*base.read, os.path.realpath(corpus.parent)}
    for one in model_files:
        reads.update((os.path.realpath(one.parent), str(Path(os.path.realpath(one)).parent)))
    written = os.path.realpath(out.parent)
    return Policy("model-logits", read=tuple(sorted({*reads, written})), write=(written,), exec=base.exec,
                  net=Net.deny(), gpu=True, env=policies.system_env(HOME=written, TMPDIR=written),
                  limits=Limits(wall_seconds=wall, output_bytes=50_000_000, processes=base.limits.processes))


def run(command: Sequence[str], *, model_files: Sequence[Path], corpus: Path, out: Path,
        wall: float = 8 * 3600.0) -> float:
    """Run the tool sandboxed and return how many seconds it took. The caller holds the GPU claim."""
    out.parent.mkdir(parents=True, exist_ok=True)
    done = sandbox.run(list(command), _policy(command, model_files, corpus, out, wall), cwd=str(out.parent))
    if done.returncode != 0 or not out.is_file():
        tail = (done.stderr or done.stdout).strip()[-3000:]
        raise ToolFailed(f"{Path(command[0]).name} failed "
                         f"({'timed out' if done.timed_out else done.returncode}):\n{tail}")
    return float(done.seconds)


@dataclass(frozen=True)
class Header:
    """What a logits file says about itself."""

    context: int
    vocab: int
    chunks: int
    tokens: tuple[int, ...]
    offset: int

    @property
    def first(self) -> int:
        """The position in a chunk of the first scored token."""
        return self.context // 2

    @property
    def scored(self) -> int:
        """Scored tokens per chunk."""
        return self.context - 1 - self.first

    @property
    def row(self) -> int:
        """16-bit words per scored token: two floats, then one word per vocabulary entry, padded to even."""
        return 2 * ((self.vocab + 1) // 2) + 4


def header(path: Path) -> Header:
    """The header of a logits file; `ToolFailed` when it is not one or is cut short."""
    with path.open("rb") as handle:
        if handle.read(8) != MAGIC:
            raise ToolFailed(f"{path} is not a llama-perplexity logits file")
        context, vocab, chunks = struct.unpack("<Iii", handle.read(12))
        raw = handle.read(4 * context * chunks)
    if len(raw) != 4 * context * chunks:
        raise ToolFailed(f"{path} ends inside its tokens")
    got = Header(context, vocab, chunks, struct.unpack(f"<{context * chunks}i", raw), 20 + len(raw))
    expected = got.offset + 2 * got.row * got.scored * chunks
    if path.stat().st_size != expected:
        raise ToolFailed(f"{path} holds {path.stat().st_size} bytes and its header says {expected}")
    return got


def compare(reference: Path, other: Path) -> list[dict[str, float]]:
    """One record per scored token of two logits files over the same text: ``kl`` (nats, of ``other`` from
    ``reference``), ``agree`` (both rank the same token first), ``dlogp`` (``other`` minus ``reference`` for the
    token the text holds), ``margin`` (the logit gap between ``reference``'s first and second token, small at a near
    tie) and the chunk and position it came from. One token is in memory at a time."""
    import numpy as np

    a, b = header(reference), header(other)
    if (a.context, a.vocab, a.chunks, a.tokens) != (b.context, b.vocab, b.chunks, b.tokens):
        raise ToolFailed(f"{reference.name} and {other.name} are not over the same text, context and vocabulary")
    out: list[dict[str, float]] = []
    with reference.open("rb") as left, other.open("rb") as right:
        left.seek(a.offset)
        right.seek(b.offset)

        def row(handle: Any) -> Any:
            raw = handle.read(2 * a.row)
            scale, floor = struct.unpack_from("<ff", raw)
            return np.frombuffer(raw, dtype="<u2", count=a.vocab, offset=8) * np.float64(scale) + floor

        for chunk in range(a.chunks):
            for position in range(a.scored):
                base, got = row(left), row(right)
                target = a.tokens[chunk * a.context + a.first + position + 1]
                held = base > FLOOR
                second, first = np.partition(base, -2)[-2:]
                out.append({"chunk": chunk, "position": position,
                            "kl": float(np.sum(np.exp(base[held]) * (base[held] - got[held]))),
                            "agree": float(int(base.argmax()) == int(got.argmax())),
                            "dlogp": float(got[target] - base[target]), "margin": float(first - second)})
    return out


def blocks(tokens: Sequence[dict[str, float]], *, size: int = BLOCK) -> list[dict[str, float]]:
    """``tokens`` as the means of consecutive blocks of about ``size`` within each chunk, the unit an interval
    resamples and two builds are compared on, with how many top tokens differ in it and the sum of the margins
    there."""
    chunks: dict[int, list[dict[str, float]]] = {}
    for one in tokens:
        chunks.setdefault(int(one["chunk"]), []).append(one)
    out = []
    for chunk, rows in sorted(chunks.items()):
        count = max(1, len(rows) // size)
        for n in range(count):
            part = rows[n * len(rows) // count:(n + 1) * len(rows) // count]
            apart = [r for r in part if not r["agree"]]
            out.append({"chunk": chunk, "block": n, "tokens": len(part), "differ": len(apart),
                        "differ_margin": sum(r["margin"] for r in apart),
                        **{key: sum(r[key] for r in part) / len(part) for key in ("kl", "agree", "dlogp", "margin")}})
    return out
