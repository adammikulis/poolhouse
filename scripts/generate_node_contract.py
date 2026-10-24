#!/usr/bin/env python3
"""Generate the node's canonical data shapes without importing the application."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from node_contract import lifecycle, operations, python_package, rust, typescript
from node_contract.load import load

ROOT = Path(__file__).resolve().parent.parent


def outputs(root: Path) -> dict[Path, str]:
    meta, rows, digest = load(root / "contracts/node")
    key = bytes(range(32))
    audit_words = [0x01020304, 0x10203040, 0x11223344, 0x55667788, 0x89ABCDEF, 0x13579BDF, 0x2468ACE0, 0xFEDCBA98]
    vectors = {"schema_sha256": digest, "decoded_values": "untrusted-data",
               "device_id": {"public_key": key.hex(), "domain_utf8_hex": meta["device_id_domain"].encode().hex(),
                             "expected": hashlib.sha256(meta["device_id_domain"].encode() + key).hexdigest()},
               "integers": {"u32_max": str(2**32 - 1), "u64_max": str(2**64 - 1)},
               "utf8": {
                   "accepted": ["", "line\r\n\tend\x00", "é", "e\u0301", "🙂", "\uffff"],
                   "json_refused": [json.dumps(chr(0xd800)), json.dumps(chr(0xdc00)),
                                    json.dumps(chr(0xd800) + "A")],
                   "wrong_types": [None, True, 1],
                   "boundary": {"text": "🙂", "count": 16384, "bytes": 65536},
               },
               "finite_f64": {
                   "bit_order": "ieee754-binary64-msb-first",
                   "accepted": ["0000000000000000", "8000000000000000", "0000000000000001",
                                "000fffffffffffff", "0010000000000000", "3ff0000000000000",
                                "bff0000000000000", "3fd5555555555555", "7fefffffffffffff",
                                "ffefffffffffffff"],
                   "refused": ["7ff0000000000000", "fff0000000000000", "7ff8000000000000",
                               "fff8000000000000", "7ff0000000000001", "7fffffffffffffff",
                               "ffffffffffffffff", "3FF0000000000000", "3ff000000000000",
                               "03ff0000000000000", "gff0000000000000", "3ff0000000000000\n",
                               "3ff0000000000000\r", "3ff0000000000000\u2028", "3ff0000000000000\u2029",
                               "", 1, True, None],
               },
               "private_client_types": [row["name"] for row in rows if row["visibility"] == "private"],
               "audit_token": {"word_order": "little-endian-u32", "words": audit_words,
                               "encoded": b"".join(word.to_bytes(4, "little") for word in audit_words).hex()},
               "unix_microseconds": {
                   "accepted": [["0", "0", "0"], ["1", "999999", "1999999"],
                                ["2", "0", "2000000"], ["18446744073709", "551615", "18446744073709551615"]],
                   "refused": [["1", "1000000"], ["18446744073709", "551616"],
                               ["18446744073710", "0"], ["-1", "0"], ["0", "-1"]],
               },
               "job_scalars": [
                   {"type": "JobPageLimit", "accepted": ["1", "100"], "refused": ["0", "101", "01", 1, True]},
                   {"type": "JobOutputByteLimit", "accepted": ["1", "65536"], "refused": ["0", "65537", 1]},
                   {"type": "JobOutputBytes", "accepted": ["", "00c3a90aff"], "refused": ["0", "AA", "é", False]},
                   {"type": "ExitCode", "accepted": ["0", "-2147483648", "2147483647"],
                    "refused": ["-0", "+1", "01", "2147483648", "-2147483649", 1]},
                   {"type": "Boolean", "accepted": [True, False], "refused": [1, 0, "true", None]},
               ]}
    result = {
        root / "app/poolhouse-node/src/contract_operations.rs": operations.render_rust(meta["operations"], digest),
        root / "src/poolhouse/api/node_operations.py": operations.render_python(meta["operations"], digest),
        root / "app/contracts/node_operations.ts": operations.render_typescript(meta["operations"], digest),
        root / "app/poolhouse-node/src/contract.rs": rust.render(meta, rows, digest),
        root / "app/poolhouse-node/src/contract_lifecycle.rs": lifecycle.render(meta["lifecycle"], meta["adapters"]),
        root / "app/contracts/node.ts": typescript.render(meta, rows, digest),
        root / "contracts/node/vectors.json": json.dumps(vectors, indent=2) + "\n",
    }
    result.update(python_package.render_package(root, meta, rows, digest))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    legacy = ROOT / "src/poolhouse/api/node_contract.py"
    if legacy.exists() or legacy.is_symlink():
        parser.exit(1, "former generated flat contract exists; review and remove that known artifact through Git before generation\n")
    generated = outputs(ROOT)
    package = ROOT / "src/poolhouse/api/node_contract"
    if package.is_symlink():
        parser.exit(1, "generated contract package cannot be a symlink\n")
    for path in package.glob("*.py"):
        if path.is_symlink() or path not in generated:
            parser.exit(1, "unknown or stale generated contract package entry; review it through Git\n")
    changed = []
    for path, content in generated.items():
        if not path.is_file() or path.read_text() != content:
            changed.append(path.relative_to(ROOT).as_posix())
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
    if args.check and changed:
        parser.exit(1, "generated node contract differs: " + ", ".join(changed) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
