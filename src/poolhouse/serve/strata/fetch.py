"""Strata's pinned downloads, every one through `poolhouse.net`: whole files, and the tensors of the draft layer read out of a
checkpoint by byte range."""
from __future__ import annotations

import json
import struct
from collections.abc import Callable
from pathlib import Path

from poolhouse import hub, net, provision
from poolhouse.files import promote, sha256_file, write_json
from poolhouse.hub import admission, hf_cache, remote

__all__ = ["Refused", "admit_hub", "draft_tensors", "pinned", "snapshot", "whole"]

HUB = "https://huggingface.co"
CHUNK = 16 << 20
HEADER_LIMIT = 64 << 20
"""The largest safetensors header or index read."""
MOST_BYTES = 1 << 30
"""The largest source archive or header package downloaded."""

Say = Callable[[str], None]


class Refused(OSError):
    """A pinned download this device may not make, or one whose bytes are not the pinned ones."""


def _allowed(what: str) -> None:
    if why := provision.download_refusal():
        raise Refused(f"{what} is not on this device and {why}")


def whole(url: str, dest: Path, want: net.Want) -> Path:
    """``url`` saved at ``dest`` and checked against the SHA-256 ``want`` pins; a ``dest`` already holding those bytes is kept."""
    if dest.is_file() and sha256_file(dest) == want.sha256:
        return dest
    _allowed(want.purpose)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        net.download(url, dest, want)
    except (net.Blocked, net.ChecksumMismatch, net.Truncated, net.NeedsApproval, net.Distrusted) as exc:
        raise Refused(f"{want.purpose} could not be downloaded: {exc}") from exc
    return dest


def pinned(sha256: str, purpose: str, *, size: int = 0, nested: bool = False) -> net.Want:
    """What a download pinned by this project must be."""
    return net.Want(sha256=sha256, require_digest=True, pinned=True, size=size, nested=nested, purpose=purpose,
                    max_bytes=size or MOST_BYTES)


def admit_hub(repo: str, revision: str, paths: list[str]) -> None:
    """`Refused` unless the Hub's rule for what an agent may fetch admits these files: a server start is never a person's pull."""
    try:
        admission.admit(remote.Ref(repo, revision=revision, commit=revision), [remote.RemoteFile(path, 0) for path in paths],
                        admission.AGENT)
    except remote.RemoteError as exc:
        raise Refused(str(exc)) from exc


def snapshot(pin: dict, say: Say) -> Path:
    """The folder holding the pinned files of a Hub repository: the one already in the Hub cache, else fetched into it through
    `poolhouse.hub` under the agent's admission rule. Every file is checked against its pinned size and SHA-256."""
    held = hub.hub_cache() / f"models--{pin['repo'].replace('/', '--')}" / "snapshots" / pin["revision"]
    if not all((held / file["name"]).is_file() for file in pin["files"]):
        admit_hub(pin["repo"], pin["revision"], [file["name"] for file in pin["files"]])
        _allowed(f"{pin['repo']}")
        say(f"fetching {pin['repo']} at its pinned revision")
        try:
            held = hf_cache.fetch(pin["repo"], revision=pin["revision"], trust=admission.AGENT)
        except (remote.RemoteError, ValueError) as exc:
            raise Refused(f"{pin['repo']} could not be fetched: {exc}") from exc
    for file in pin["files"]:
        path = held / file["name"]
        if path.stat().st_size != file["bytes"] or sha256_file(path) != file["sha256"]:
            raise Refused(f"{path} is not the pinned {file['name']}: its size or SHA-256 differs")
    return held


def _range(url: str, start: int, end: int, purpose: str) -> bytes:
    """Bytes ``start`` to ``end`` (inclusive) of ``url``; only an answer that is that range is taken."""
    got = net.default().get(url, net.Ask(purpose=purpose, headers={"Range": f"bytes={start}-{end}"},
                                         max_bytes=end - start + 1, tries=3))
    said = {key.lower(): value for key, value in got.headers.items()}.get("content-range", "")
    if got.status != 206 or not said.startswith(f"bytes {start}-{end}/") or len(got.body) != end - start + 1:
        raise Refused(f"{url} did not answer the byte range asked for (status {got.status}, {said!r})")
    return got.body


def _tensor(url: str, row: dict, target: Path, want: str, say: Say) -> None:
    """The tensor ``row`` names written to ``target``, resumed where an earlier run stopped, and checked against ``want``."""
    if target.is_file() and target.stat().st_size == row["bytes"] and sha256_file(target) == want:
        return
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    if have > row["bytes"]:
        part.unlink()
        have = 0
    with part.open("ab") as out:
        for start in range(row["start"] + have, row["end"] + 1, CHUNK):
            out.write(_range(url, start, min(start + CHUNK, row["end"] + 1) - 1, "Strata draft layer"))
    if sha256_file(part) != want:
        part.unlink()
        raise Refused(f"the tensor {row['name']} is not the pinned one: its SHA-256 differs")
    promote(part, target)
    say(f"draft layer: {row['name']}")


def draft_tensors(pin: dict, out: Path, say: Say) -> Path:
    """The pinned checkpoint's ``mtp.*`` tensors under ``out`` (``tensors/`` and ``mtp-manifest.json``), the layout Strata's
    ``mtp_pack.py`` reads. Only the named byte ranges are downloaded."""
    manifest = out / "mtp-manifest.json"
    wanted: dict[str, str] = pin["tensors"]
    tensors = out / "tensors"
    if manifest.is_file():
        rows = json.loads(manifest.read_text(encoding="utf-8"))
        if {row["name"] for row in rows} == set(wanted) and all(
                (out / row["file"]).is_file() and sha256_file(out / row["file"]) == wanted[row["name"]] for row in rows):
            return out
    _allowed("the Strata draft layer")
    admit_hub(pin["repo"], pin["revision"], ["model.safetensors.index.json"])
    tensors.mkdir(parents=True, exist_ok=True)
    base = f"{HUB}/{pin['repo']}/resolve/{pin['revision']}/"
    index = net.default().get(base + "model.safetensors.index.json",
                              net.Ask(purpose="Strata draft layer", max_bytes=HEADER_LIMIT, tries=3))
    shards = json.loads(index.body)["weight_map"]
    if missing := sorted(set(wanted) - set(shards)):
        raise Refused(f"the pinned checkpoint names no tensor {', '.join(missing)}")
    headers: dict[str, tuple[int, dict]] = {}
    rows = []
    for name in sorted(wanted):
        shard = shards[name]
        if not shard.endswith(".safetensors") or "/" in shard or "\\" in shard:
            raise Refused(f"the pinned checkpoint names an unexpected file {shard!r}")
        if shard not in headers:
            length = struct.unpack("<Q", _range(base + shard, 0, 7, "Strata draft layer"))[0]
            if not 0 < length <= HEADER_LIMIT:
                raise Refused(f"{shard} has a header of {length} bytes")
            headers[shard] = (8 + length, json.loads(_range(base + shard, 8, 8 + length - 1, "Strata draft layer")))
        offset, header = headers[shard]
        meta = header[name]
        first, after = meta["data_offsets"]
        row = {"name": name, "shard": shard, "dtype": meta["dtype"], "shape": meta["shape"], "start": offset + first,
               "end": offset + after - 1, "bytes": after - first}
        _tensor(base + shard, row, tensors / f"{name}.bin", wanted[name], say)
        rows.append({**row, "file": f"tensors/{name}.bin", "sha256": wanted[name]})
    write_json(manifest, rows, indent=1)
    return out
