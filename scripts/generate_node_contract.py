#!/usr/bin/env python3
"""Generate the node's canonical data shapes without importing the application."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from node_contract import lifecycle, operations, python, rust, typescript
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
    return {
        root / "app/poolhouse-node/src/contract_operations.rs": operations.render_rust(meta["operations"], digest),
        root / "src/poolhouse/api/node_operations.py": operations.render_python(meta["operations"], digest),
        root / "app/contracts/node_operations.ts": operations.render_typescript(meta["operations"], digest),
        root / "app/poolhouse-node/src/contract.rs": rust.render(meta, rows, digest),
        root / "app/poolhouse-node/src/contract_lifecycle.rs": lifecycle.render(meta["lifecycle"], meta["adapters"]),
        root / "src/poolhouse/api/node_contract.py": python.render(meta, rows, digest),
        root / "app/contracts/node.ts": typescript.render(meta, rows, digest),
        root / "contracts/node/vectors.json": json.dumps(vectors, indent=2) + "\n",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for path, content in outputs(ROOT).items():
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
