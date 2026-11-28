"""What a Strata server needs from a model before it can start: the files checked against their pinned digests, the engine's pack,
on a Mac one file for the GPU when the set has more than two, and the drafter. Made once, under Poolhouse's state."""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from poolhouse.files import promote, read_json, write_json
from poolhouse.hub.modelfile import Limits, scan_gguf
from poolhouse.lock import only_one
from poolhouse.serve import python_runtime
from poolhouse.serve.backend import ServerFailed
from poolhouse.serve.logs import tail
from poolhouse.serve.strata import fetch, pins
from poolhouse.serve.strata.install import Engine, root

__all__ = ["DRAFTERS", "Job", "Prepared", "data_dir", "drafter_for", "experts_file", "mapped_bytes", "prepare"]

DRAFTERS = ("mtp", "dflash2", "off")
"""The drafters a start may name."""
NGRAM_TABLE = "per_layer_token_embd"
"""The tensor the engine reads from disk a few rows at a time rather than holding."""
RECORD = "POOLHOUSE.json"
SPARE_BYTES = 10 << 30
TOOL_TIMEOUT_S = 6 * 3600
Say = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class Job:
    """One start: the installed engine, the model set and its files, and the device."""

    engine: Engine
    chosen: pins.ModelSet
    files: tuple[Path, ...]
    kind: str
    memory: int
    context: int = 0
    """Tokens over all the slots, as a server spec counts them."""
    slots: int = 1

    @property
    def each(self) -> int:
        """Tokens one conversation gets: what the engine is started with, each of its slots holding that many."""
        return self.context // max(1, self.slots)


@dataclass(frozen=True, slots=True)
class Prepared:
    """The paths a start is configured from."""

    data: Path
    pack: Path
    native: Path
    ngram: Path
    repacked: bool
    drafter: str
    why: str


def data_dir() -> Path:
    """The model data folder, in the layout Strata's server finds its drafters in (``packs/``, ``mtp/``, ``dflash2/``)."""
    return root() / "data"


def ngram_shard(files: Sequence[Path]) -> Path:
    """The file of ``files`` that holds the n-gram table."""
    for path in files:
        if any(t.name.startswith(NGRAM_TABLE) for t in scan_gguf(path, tensors=True, limits=Limits(keep_array=0)).tensors):
            return path
    raise ServerFailed(f"no file of {files[0].name} holds the n-gram table ({NGRAM_TABLE})")


def mapped_bytes(chosen: pins.ModelSet) -> int:
    """The bytes of a set the device holds: every file but the one that is the n-gram table (the largest set of rows read from
    disk, the file every set of one repository shares)."""
    table = 28_800_138_432
    return chosen.bytes - (table if any(f.bytes == table for f in chosen.files) else 0)


def _run(tool: Path, arguments: list[str], log: Path, what: str) -> None:
    """One of Strata's preparation programs (local files only) under the managed Python."""
    argv = [*python_runtime.interpreter("strata"), str(tool), *arguments]
    environment = python_runtime.environment_for_child({"HOME": str(data_dir()), "HF_HUB_OFFLINE": "1"})
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as out:
        done = subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, env=environment, cwd=data_dir(), timeout=TOOL_TIMEOUT_S)
    if done.returncode:
        raise ServerFailed(f"{what} failed (exit {done.returncode})\n--- {log} ---\n{tail(log)}")


def _made(record: Path, want: dict) -> bool:
    return read_json(record, None) == want


def _room(where: Path, need: int, what: str) -> None:
    free = shutil.disk_usage(where).free
    if free < need + SPARE_BYTES:
        raise ServerFailed(f"{what} needs {need / 1e9:.0f} GB and {SPARE_BYTES / 1e9:.0f} GB to spare on the disk holding {where}; "
                           f"{free / 1e9:.0f} GB are free")


def _repack(engine: Engine, chosen: pins.ModelSet, files: Sequence[Path], say: Say) -> Path:
    """The experts and dense tensors of a many-file set written into the one file the Metal engine maps."""
    native = data_dir() / "metal" / f"{chosen.key}-metal.gguf"
    record = native.with_name(native.name + ".json")
    want = {"commit": engine.home.name, "files": [f.sha256 for f in chosen.files]}
    if native.is_file() and _made(record, want):
        return native
    native.parent.mkdir(parents=True, exist_ok=True)
    native.unlink(missing_ok=True)
    _room(native.parent, mapped_bytes(chosen), "the file the GPU reads")
    say(f"writing {native.name} ({mapped_bytes(chosen) / 1e9:.0f} GB, once); every tensor is checked against its source")
    _run(engine.source / "engine" / "metal" / "tools" / "metal_repack.py",
         ["--shard1", str(files[0]), "--out", str(native), "--verify"], native.with_suffix(".log"), "writing the GPU's file")
    write_json(record, want)
    return native


def _pack(engine: Engine, chosen: pins.ModelSet, native: Path, say: Say) -> Path:
    pack = data_dir() / "packs" / chosen.key
    want = {"commit": engine.home.name, "files": [f.sha256 for f in chosen.files], "native": native.name}
    if _made(pack / RECORD, want):
        return pack
    if pack.exists():
        shutil.rmtree(pack)
    pack.parent.mkdir(parents=True, exist_ok=True)
    say(f"preparing the model pack of {chosen.key} (once)")
    _run(engine.source / "terminal" / "tools" / "iq_pack.py",
         ["--gguf", str(native), "--out", str(pack), *(["--compat-bf16"] if chosen.unsloth else [])],
         pack.parent / f"{chosen.key}.log", "preparing the model pack")
    write_json(pack / RECORD, want)
    return pack


def _mtp(engine: Engine, kind: str, pin: dict, say: Say) -> None:
    """The MTP draft layer: the checkpoint's draft tensors fetched by byte range and packed for the engine (and, for an NVIDIA
    card, into the runtime folder its engine reads)."""
    where = data_dir() / "mtp"
    out = where / "mtp-q2_0.gguf"
    record = where / RECORD
    want = {"commit": engine.home.name, "revision": pin["mtp"]["revision"]}
    if not (out.is_file() and _made(record, want)):
        say("fetching the MTP draft layer (about 5 GB, once)")
        fetch.draft_tensors(pin["mtp"], where, say)
        _run(engine.source / "terminal" / "tools" / "mtp_pack.py", ["--src", str(where), "--experts", "q2_0", "--out", str(out)],
             where / "pack.log", "packing the MTP draft layer")
        write_json(record, want)
        shutil.rmtree(where / "tensors", ignore_errors=True)
        (where / "mtp-manifest.json").unlink(missing_ok=True)
    if kind == "cuda" and not (where / "rt" / "experts.bin").is_file():
        _run(engine.source / "terminal" / "tools" / "mtp_rt.py", ["--gguf", str(out), "--out", str(where / "rt")],
             where / "rt.log", "preparing the MTP draft layer for the card")
    _vocabulary(engine, where / "rt")


def experts_file(engine: Engine, chosen: pins.ModelSet, made: Prepared, say: Say = lambda _line: None) -> None:
    """The pack's ``experts.bin``, which a PC whose RAM does not hold the experts reads them from."""
    with only_one(made.data / ".prepare.lock"):
        if (made.pack / "experts.bin").is_file():
            return
        _room(made.pack, int(chosen.experts_gb * 1e9), "the experts' file")
        say(f"writing the experts into one file ({chosen.experts_gb:.0f} GB, once)")
        _run(engine.source / "terminal" / "tools" / "iq_pack.py",
             ["--gguf", str(made.native), "--out", str(made.pack), "--experts-bin", *(["--compat-bf16"] if chosen.unsloth else [])],
             made.pack.parent / f"{chosen.key}-experts.log", "writing the experts' file")


def _vocabulary(engine: Engine, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(engine.source / "terminal" / "data" / "draft_vocab.bin", folder / "draft_vocab.bin")


def _checked(path: Path, pin: dict, what: str) -> Path:
    if path.stat().st_size != pin["bytes"] or pins.Digests().digest(path) != pin["sha256"]:
        path.unlink()
        raise ServerFailed(f"{what} did not come out as the pinned file, so it was removed")
    return path


def _dflash2(engine: Engine, pin: dict, say: Say) -> None:
    """The DFlash2 drafter: the pinned weights fetched, packed into the engine's file, and its smaller selector made from that."""
    held = pin["dflash2"]
    where = data_dir() / "dflash2"
    packed = where / held["pack"]["name"]
    converted = where / held["converted"]["name"]
    tool = engine.source / "terminal" / "tools" / "dflash2_pack.py"
    if not packed.is_file():
        sources = fetch.snapshot(held, say)
        where.mkdir(parents=True, exist_ok=True)
        staged = packed.with_name(packed.name + ".new")
        _run(tool, [str(sources / "model.safetensors"), str(sources / "config.json"), str(staged), "--q4_0"],
             where / "pack.log", "packing the DFlash2 drafter")
        promote(_checked(staged, held["pack"], "the DFlash2 pack"), packed)
    _checked(packed, held["pack"], "the DFlash2 pack")
    if not converted.is_file():
        staged = converted.with_name(converted.name + ".new")
        _run(tool, ["--convert-selector", str(packed), str(staged)], where / "convert.log", "converting the DFlash2 selector")
        promote(_checked(staged, held["converted"], "the converted DFlash2 pack"), converted)
    _checked(converted, held["converted"], "the converted DFlash2 pack")
    _vocabulary(engine, where)


def drafter_for(job: Job, asked: str, say: Say, pin: dict) -> tuple[str, str]:
    """``(drafter, why)``: the drafter's files made present and the value Strata's config takes. ``asked`` empty is Strata's own
    default for the set and device, and MTP where DFlash2 cannot draft or its files may not be fetched. A start drafts unless
    ``asked`` is ``off``; a drafter that was named and cannot be had fails the start rather than being replaced."""
    engine, chosen, kind = job.engine, job.chosen, job.kind
    one_at_a_time = kind == "metal" and job.slots > 1   # Strata's Metal engine has no DFlash2 drafter for each request slot
    if asked == "off":
        return "off", "drafting was switched off for this start"
    if asked == "dflash2":
        if one_at_a_time:
            raise ServerFailed(f"DFlash2 drafts for one conversation at a time on a Mac, not {job.slots}: "
                               "serve one slot, or draft with mtp")
        _dflash2(engine, pin, say)
        return "dflash2", "asked for"
    if asked == "mtp" or one_at_a_time or (kind == "metal" and chosen.key == "iq3_s"):
        _mtp(engine, kind, pin, say)
        if asked == "mtp":
            return "mtp", "asked for"
        if one_at_a_time:
            return "mtp", "DFlash2 drafts one conversation at a time on a Mac"
        return "mtp", "Strata measured MTP faster than DFlash2 for IQ3_S on a Mac"
    try:
        _dflash2(engine, pin, say)
    except fetch.Refused as why:
        _mtp(engine, kind, pin, say)
        return "mtp", f"MTP drafts because DFlash2 is not available here: {why}"
    if kind == "metal":
        return "dflash2", "Strata's default on a Mac for this set"
    _mtp(engine, kind, pin, say)
    return "auto", "Strata chooses between DFlash2 and MTP by the card"


def prepare(job: Job, asked: str = "", say: Say = lambda _line: None) -> Prepared:
    """Everything a start of ``job`` needs, made when it is not here."""
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    with only_one(data / ".prepare.lock"):
        try:
            pins.check_files(job.files, job.chosen.files, say=say)
            repacked = job.kind == "metal" and len(job.files) > 2
            native = _repack(job.engine, job.chosen, job.files, say) if repacked else job.files[0]
            ngram = ngram_shard(job.files)
            pack = _pack(job.engine, job.chosen, native, say)
            drafter, why = drafter_for(job, asked, say, pins.load())
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise ServerFailed(f"Strata could not prepare {job.chosen.key}: {exc}") from exc
    return Prepared(data, pack, native, ngram, repacked, drafter, why)
