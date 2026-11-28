"""The virtual machine engine: QEMU's AArch64 system emulator built for Android, from pinned Termux packages.

    python app/phone-isolation/engine.py lock --index PACKAGES --cache DIR   # write engine.lock.json
    python app/phone-isolation/engine.py build --cache DIR --out DIR         # the APK's engine files

QEMU is GPL-2.0 and is run as a separate program; its libraries carry their own licences (GLib LGPL-2.1, others in
each package). A shipped APK would build it from source with only the devices the machine uses; these packages are
the prototype's way to have it today. Android unpacks only ``lib*.so`` from an APK, so a library named like
``libz.so.1`` is renamed to ``libz_1.so``, in the same number of bytes, in its own file and in every file naming it.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import struct
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / "engine.lock.json"
ARCHIVE = "https://packages.termux.dev/apt/termux-main/"
PREFIX = "./data/data/com.termux/files/usr/"
ROOT_PACKAGE = "qemu-system-aarch64-headless"
PROGRAM = "qemu-system-aarch64"
#: Libraries every Android process already has.
PLATFORM = frozenset({"libc.so", "libm.so", "libdl.so", "liblog.so", "libandroid.so", "libz.so"})


class EngineError(RuntimeError):
    """An engine that cannot be built; the message says why."""


def installable(name: str) -> str:
    """``name`` as Android will unpack it, no longer than before."""
    if name.startswith("lib") and name.endswith(".so"):
        return name
    stem, found, version = name.partition(".so.")
    if not found or not stem.startswith("lib"):
        raise EngineError(f"{name} has no installable name of the same length")
    return f"{stem}_{version.replace('.', '_')}.so"


def _dynamic(data: bytes) -> tuple[list[int], list[int], int, int]:
    """For a 64-bit little-endian ELF: the string offsets it needs, its own name's, and its string table's place."""
    if data[:6] != b"\x7fELF\x02\x01":
        raise EngineError("not a 64-bit little-endian ELF file")
    headers, size, count = struct.unpack_from("<Q", data, 32)[0], *struct.unpack_from("<HH", data, 54)
    loads, dynamic = [], None
    for index in range(count):
        kind, _, offset, address, _, length = struct.unpack_from("<IIQQQQ", data, headers + index * size)
        if kind == 1:
            loads.append((address, offset, length))
        elif kind == 2:
            dynamic = (offset, length)
    if dynamic is None:
        raise EngineError("the file is not dynamically linked")
    needed, own, table, extent = [], [], 0, 0
    for at in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        tag, held = struct.unpack_from("<qQ", data, at)
        if tag == 0:
            break
        if tag == 1:
            needed.append(held)
        elif tag == 14:
            own.append(held)
        elif tag == 5:
            table = next(offset + held - address for address, offset, length in loads
                         if address <= held < address + length)
        elif tag == 10:
            extent = held
    return needed, own, table, extent


def needs(data: bytes) -> list[str]:
    """The libraries an ELF file asks the loader for."""
    needed, _, table, _ = _dynamic(data)
    return [data[table + at:data.index(b"\0", table + at)].decode() for at in needed]


def renamed(data: bytes, names: dict[str, str]) -> bytes:
    """``data`` with each library name of ``names`` replaced inside its dynamic string table, byte for byte."""
    _, _, table, extent = _dynamic(data)
    strings = bytearray(data[table:table + extent])
    for old, new in names.items():
        if len(new) > len(old):
            raise EngineError(f"{new} is longer than {old}")
        wanted = b"\0" + old.encode() + b"\0"
        replacement = b"\0" + new.encode().ljust(len(old), b"\0") + b"\0"
        strings = strings.replace(wanted, replacement)
    return data[:table] + bytes(strings) + data[table + extent:]


def _data_tar(package: bytes) -> bytes:
    if not package.startswith(b"!<arch>\n"):
        raise EngineError("a package is not an ar archive")
    at = 8
    while at + 60 <= len(package):
        name = package[at:at + 16].decode("ascii").strip().rstrip("/")
        size = int(package[at + 48:at + 58])
        if name.startswith("data.tar"):
            return package[at + 60:at + 60 + size]
        at += 60 + size + size % 2
    raise EngineError("a package holds no data")


def package_contents(package: bytes) -> tuple[dict[str, bytes], dict[str, str]]:
    """A package's libraries and the emulator by base name, and its links by base name."""
    files, links = {}, {}
    with tarfile.open(fileobj=io.BytesIO(_data_tar(package)), mode="r:*") as archive:
        for member in archive:
            if not member.name.startswith(PREFIX):
                continue
            inside = member.name[len(PREFIX):]
            folder, _, name = inside.rpartition("/")
            library = ".so" in name and (folder == "lib" or folder == "lib/pulseaudio")
            if not (library or inside == f"bin/{PROGRAM}"):
                continue
            if member.issym():
                links[name] = member.linkname.rpartition("/")[2]
            elif member.isfile():
                files[name] = archive.extractfile(member).read()
    return files, links


def closure(files: dict[str, bytes], links: dict[str, str]) -> dict[str, bytes]:
    """The emulator and every library it needs, directly or through another, by the name asked for."""
    found: dict[str, bytes] = {}
    queue = [PROGRAM]
    while queue:
        name = queue.pop()
        if name in found:
            continue
        target, seen = name, set()
        while target in links and target not in seen:
            seen.add(target)
            target = links[target]
        if target not in files:
            if name in PLATFORM:
                continue
            raise EngineError(f"no package holds {name}")
        found[name] = files[target]
        queue.extend(needs(files[target]))
    return found


def installed(found: dict[str, bytes]) -> dict[str, bytes]:
    """The files of a closure under the names the APK carries."""
    names = {name: installable(name) for name in found if name != PROGRAM}
    if len(set(names.values())) != len(names):
        raise EngineError("two libraries would share one installed name")
    changed = {old: new for old, new in names.items() if old != new}
    return {("libqemu.so" if name == PROGRAM else names[name]): renamed(data, changed) for name, data in found.items()}


def fetched(url: str, digest: str, cache: Path) -> bytes:
    """The file at ``url``, kept in ``cache``, once it matches ``digest``."""
    held = cache / url.rsplit("/", 1)[1]
    if not held.is_file():
        if not url.startswith("https://"):
            raise EngineError(f"{url} is not an https address")
        with urllib.request.urlopen(url, timeout=300) as reply:  # noqa: S310 - https, pinned below
            data = reply.read()
        held.parent.mkdir(parents=True, exist_ok=True)
        held.write_bytes(data)
    data = held.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise EngineError(f"{held.name} does not match its pinned SHA-256")
    return data


def resolve(index: str, cache: Path) -> list[dict[str, str]]:
    """The packages of the index ``index`` that hold a file the emulator needs."""
    packages = {}
    for block in index.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(" "))
        if "Package" in fields:
            packages[fields["Package"]] = fields
    names, queue = [], [ROOT_PACKAGE]
    while queue:
        name = queue.pop(0)
        if name in names or name not in packages:
            continue
        names.append(name)
        queue.extend(part.split("|")[0].strip().split(" ")[0] for part in packages[name].get("Depends", "").split(","))
    files, links, owner = {}, {}, {}
    for name in names:
        fields = packages[name]
        held, linked = package_contents(fetched(ARCHIVE + fields["Filename"], fields["SHA256"], cache))
        files.update(held)
        links.update(linked)
        owner.update(dict.fromkeys([*held, *linked], name))
    used = {owner[name] for name in closure(files, links)}
    return [{"name": name, "version": packages[name]["Version"], "url": ARCHIVE + packages[name]["Filename"],
             "sha256": packages[name]["SHA256"]} for name in sorted(used)]


def build(cache: Path, out: Path) -> list[Path]:
    files, links = {}, {}
    for entry in json.loads(LOCK.read_text(encoding="utf-8"))["packages"]:
        held, linked = package_contents(fetched(entry["url"], entry["sha256"], cache))
        files.update(held)
        links.update(linked)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for name, data in sorted(installed(closure(files, links)).items()):
        (out / name).write_bytes(data)
        made.append(out / name)
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("lock", "build"))
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "lock":
            entries = resolve(args.index.read_text(encoding="utf-8"), args.cache)
            LOCK.write_text(json.dumps({"packages": entries}, indent=1) + "\n", encoding="utf-8")
            sys.stdout.write(f"{len(entries)} packages\n")
        else:
            made = build(args.cache, args.out)
            sys.stdout.write(f"{len(made)} files, {sum(path.stat().st_size for path in made)} bytes\n")
    except (EngineError, OSError) as exc:
        sys.stderr.write(f"engine: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
