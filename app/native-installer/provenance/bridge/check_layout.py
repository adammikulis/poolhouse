"""Verify the layout table enumerates exactly the generated cgo fields.

This is a build check, not authentication of the header, archive or toolchain.
The build must generate and link both from the same reviewed verifier sources.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

NAMES = {
    "PH_BYTES_FIELDS": "ph_bytes",
    "PH_VERSION_FIELDS": "ph_version",
    "PH_INPUT_FIELDS": "ph_provenance_input",
    "PH_RESULT_FIELDS": "ph_provenance_result",
}


def listed(source: str, macro: str) -> list[str]:
    lines = source.splitlines()
    prefix = "#define " + macro + "(X) "
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        parts = [line[len(prefix):]]
        while parts[-1].rstrip().endswith("\\"):
            index += 1
            if index >= len(lines):
                raise ValueError("Incomplete layout field list.")
            parts.append(lines[index])
        return re.findall(r"\bX\(([a-z][a-z0-9_]*)\)", "\n".join(parts))
    raise ValueError("Missing layout field list.")


def check(header: Path, table: Path) -> None:
    with tempfile.TemporaryFile() as output:
        subprocess.run(["/usr/bin/clang", "-Xclang", "-ast-dump=json", "-fsyntax-only", "-x", "c", str(header)],
                       stdout=output, stderr=subprocess.PIPE, check=True, env={"PATH": "/usr/bin:/bin"})
        if output.tell() > 32 * 1024 * 1024:
            raise ValueError("Layout metadata is too large.")
        output.seek(0)
        tree = json.load(output)
    nodes = tree.get("inner", [])
    records = {node["id"]: node for node in nodes if node.get("kind") == "RecordDecl" and "id" in node}
    typedefs = {node.get("name"): node for node in nodes if node.get("kind") == "TypedefDecl"}
    source = table.read_text(encoding="utf-8")
    for macro, typename in NAMES.items():
        node = typedefs.get(typename, {})
        tags = [item.get("ownedTagDecl", {}).get("id") for item in node.get("inner", [])]
        if len(tags) != 1 or tags[0] not in records:
            raise ValueError("Invalid generated layout type.")
        actual = [item.get("name") for item in records[tags[0]].get("inner", []) if item.get("kind") == "FieldDecl"]
        expected = listed(source, macro)
        if not actual or actual != expected or len(set(expected)) != len(expected):
            raise ValueError("Generated layout fields do not match the table.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_layout.py GENERATED_HEADER TABLE_HEADER")
    try:
        check(Path(sys.argv[1]), Path(sys.argv[2]))
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError):
        raise SystemExit("Native provenance layout check failed.") from None
