"""The virtual machine's guest: an Alpine Linux kernel, a small first disk and the system it installs.

    python app/phone-isolation/guest.py lock --index DIR --cache DIR    # write guest.lock.json from APKINDEX files
    python app/phone-isolation/guest.py build --cache DIR --out DIR [--extra TAR:PREFIX ...]

``build`` writes ``DIR/apk`` (``libkernel.so`` and ``libinitrd.so``, which the APK carries) and ``DIR/system.img``:
one uncompressed tar the guest unpacks onto its own work disk on its first start. Everything is written straight from
the packages into the archives, so no file passes through this computer's filesystem, where case and links differ.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "guest.lock.json"
MIRROR = "https://dl-cdn.alpinelinux.org/alpine"
REPOSITORIES = ("main", "community")
#: What the system holds beside what these need.
SYSTEM = ("alpine-baselayout", "alpine-baselayout-data", "busybox", "python3", "python3-tests", "py3-pytest",
          "py3-psutil", "py3-cryptography", "py3-packaging", "py3-yaml", "gcc", "musl-dev", "make", "git", "rust",
          "cargo")
#: What the first disk holds: enough to make a filesystem and unpack the system.
FIRST = ("busybox", "musl", "e2fsprogs")
MODULES = ("virtio_mmio", "virtio_blk", "virtio_console", "ext4", "crc32c_generic")


class GuestError(RuntimeError):
    """A guest that cannot be built; the message says why."""


def records(index: str, repository: str) -> list[dict[str, str]]:
    """The packages of one APKINDEX text."""
    found = []
    for block in index.split("\n\n"):
        fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        if "P" in fields:
            fields["repository"] = repository
            found.append(fields)
    return found


def bare(name: str) -> str:
    """A dependency's name without its version condition."""
    return re.split(r"[=<>~]", name, maxsplit=1)[0]


def resolve(packages: list[dict[str, str]], wanted: tuple[str, ...]) -> list[dict[str, str]]:
    """``wanted`` and everything they depend on, each package once."""
    by_name = {record["P"]: record for record in packages}
    provides: dict[str, dict[str, str]] = {}
    for record in packages:
        for item in record.get("p", "").split():
            provides.setdefault(bare(item), record)
    chosen: dict[str, dict[str, str]] = {}
    queue = list(wanted)
    while queue:
        asked = queue.pop(0)
        if asked.startswith("!"):
            continue
        name = bare(asked)
        record = by_name.get(name) or provides.get(name)
        if record is None:
            raise GuestError(f"no package provides {name}")
        if record["P"] in chosen:
            continue
        chosen[record["P"]] = record
        queue.extend(record.get("D", "").split())
    return [chosen[name] for name in sorted(chosen)]


def fetched(url: str, digest: str, cache: Path) -> bytes:
    held = cache / url.rsplit("/", 1)[1]
    if not held.is_file():
        if not url.startswith("https://"):
            raise GuestError(f"{url} is not an https address")
        with urllib.request.urlopen(url, timeout=600) as reply:  # noqa: S310 - https, pinned below
            data = reply.read()
        held.parent.mkdir(parents=True, exist_ok=True)
        held.write_bytes(data)
    data = held.read_bytes()
    if digest and hashlib.sha256(data).hexdigest() != digest:
        raise GuestError(f"{held.name} does not match its pinned SHA-256")
    return data


def members(package: bytes):
    """The files of an ``.apk`` package (concatenated gzip streams of one tar), without its own records."""
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(package)), mode="r:", ignore_zeros=True) as archive:
        for member in archive:
            if member.name.startswith("."):
                continue
            yield member, archive.extractfile(member).read() if member.isfile() else b""


def cpio(entries: list[tuple[str, int, bytes, tuple[int, int]]]) -> bytes:
    """A "newc" cpio archive of ``(name, mode, content, device)`` entries, as the kernel reads its first disk."""
    out = bytearray()
    for number, (name, mode, content, device) in enumerate([*entries, ("TRAILER!!!", 0, b"", (0, 0))], start=1):
        encoded = name.encode() + b"\0"
        fields = (number, mode, 0, 0, 1, 0, len(content), 0, 0, device[0], device[1], len(encoded), 0)
        out += b"070701" + "".join(f"{value:08x}" for value in fields).encode() + encoded
        out += b"\0" * (-len(out) % 4)
        out += content
        out += b"\0" * (-len(out) % 4)
    return bytes(out)


def module_order(modules: dict[str, bytes], wanted: tuple[str, ...]) -> list[str]:
    """The modules of ``wanted`` that are not built into the kernel, each after the ones it needs."""
    ordered: list[str] = []

    def add(name: str) -> None:
        if name in ordered or name not in modules:
            return
        found = re.search(rb"\0depends=([^\0]*)\0", modules[name])
        for other in (found.group(1).decode().split(",") if found else []):
            if other:
                add(other.replace("-", "_"))
        ordered.append(name)

    for name in wanted:
        add(name)
    return ordered


def kernel_parts(package: bytes) -> tuple[bytes, dict[str, bytes]]:
    """The kernel and its modules (unpacked, by name) from a ``linux-virt`` package."""
    kernel, modules = b"", {}
    for member, data in members(package):
        if member.name == "boot/vmlinuz-virt":
            kernel = data
        elif ".ko" in member.name and member.isfile():
            name = member.name.rpartition("/")[2].split(".ko")[0].replace("-", "_")
            modules[name] = gzip.decompress(data) if member.name.endswith(".gz") else data
    if not kernel:
        raise GuestError("the kernel package holds no kernel")
    return kernel, modules


def first_disk(packages: list[bytes], modules: dict[str, bytes]) -> bytes:
    """The initramfs: busybox, the filesystem tools, the modules in the order to load them, and ``init``."""
    entries: dict[str, tuple[int, bytes, tuple[int, int]]] = {}
    for name in ("bin", "sbin", "usr", "usr/bin", "usr/sbin", "usr/lib", "lib", "etc", "dev", "proc", "sys", "mnt",
                 "tmp", "modules"):
        entries[name] = (0o040755, b"", (0, 0))
    entries["dev/console"] = (0o020600, b"", (5, 1))
    for package in packages:
        for member, data in members(package):
            name = member.name.rstrip("/")
            if member.isdir():
                entries[name] = (0o040000 | member.mode, b"", (0, 0))
            elif member.issym():
                entries[name] = (0o120777, member.linkname.encode(), (0, 0))
            elif member.isfile():
                entries[name] = (0o100000 | member.mode, data, (0, 0))
    ordered = module_order(modules, MODULES)
    for name in ordered:
        entries[f"modules/{name}.ko"] = (0o100644, modules[name], (0, 0))
    entries["modules/order"] = (0o100644, "".join(f"{name}\n" for name in ordered).encode(), (0, 0))
    entries["init"] = (0o100755, (HERE / "guest" / "init").read_bytes(), (0, 0))
    listed = [(name, *entries[name]) for name in sorted(entries)]
    return gzip.compress(cpio(listed), compresslevel=6, mtime=0)


def system_disk(packages: list[bytes], extra: list[tuple[Path, str]], to: Path) -> None:
    """One tar of every file of ``packages`` and of each ``extra`` archive under its prefix, owned by root."""
    with tarfile.open(to, "w", format=tarfile.GNU_FORMAT) as out:
        def put(member: tarfile.TarInfo, data: bytes) -> None:
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
            member.pax_headers = {}
            out.addfile(member, io.BytesIO(data) if member.isfile() else None)

        for package in packages:
            for member, data in members(package):
                put(member, data)
        for archive, prefix in extra:
            with tarfile.open(archive, "r:*") as found:
                for member in found:
                    data = found.extractfile(member).read() if member.isfile() else b""
                    member.name = f"{prefix.strip('/')}/{member.name}"
                    put(member, data)


def url(record: dict[str, str], release: str) -> str:
    return f"{MIRROR}/{release}/{record['repository']}/aarch64/{record['P']}-{record['V']}.apk"


def lock(index: Path, cache: Path, release: str) -> dict:
    packages = []
    for repository in REPOSITORIES:
        with tarfile.open(index / f"{repository}.tar.gz", "r:gz", ignore_zeros=True) as archive:
            packages += records(archive.extractfile("APKINDEX").read().decode(), repository)
    kernel = next(record for record in packages if record["P"] == "linux-virt")
    made = {"release": release}
    for part, wanted in (("kernel", [kernel]), ("first", resolve(packages, FIRST)),
                         ("system", resolve(packages, SYSTEM))):
        made[part] = []
        for record in wanted:
            address = url(record, release)
            data = fetched(address, "", cache)
            made[part].append({"name": record["P"], "version": record["V"], "url": address,
                               "sha256": hashlib.sha256(data).hexdigest()})
    return made


def build(cache: Path, out: Path, extra: list[tuple[Path, str]]) -> None:
    held = json.loads(LOCK.read_text(encoding="utf-8"))
    parts = {part: [fetched(entry["url"], entry["sha256"], cache) for entry in held[part]]
             for part in ("kernel", "first", "system")}
    kernel, modules = kernel_parts(parts["kernel"][0])
    (out / "apk").mkdir(parents=True, exist_ok=True)
    (out / "apk" / "libkernel.so").write_bytes(kernel)
    (out / "apk" / "libinitrd.so").write_bytes(first_disk(parts["first"], modules))
    system_disk(parts["system"], extra, out / "system.img")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("lock", "build"))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--release", default="latest-stable")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--extra", action="append", default=[], help="TAR:PREFIX, unpacked into the system")
    args = parser.parse_args(argv)
    try:
        if args.command == "lock":
            made = lock(args.index, args.cache, args.release)
            LOCK.write_text(json.dumps(made, indent=1) + "\n", encoding="utf-8")
            sys.stdout.write(f"{len(made['system'])} system packages\n")
        else:
            extra = [(Path(item.rpartition(':')[0]), item.rpartition(':')[2]) for item in args.extra]
            build(args.cache, args.out, extra)
            sys.stdout.write(f"{(args.out / 'system.img').stat().st_size} bytes of system\n")
    except (GuestError, OSError, StopIteration) as exc:
        sys.stderr.write(f"guest: {exc!r}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
