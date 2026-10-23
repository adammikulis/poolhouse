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
    vectors = {"schema_sha256": digest, "decoded_values": "untrusted-data",
               "device_id": {"public_key": key.hex(), "domain_utf8_hex": meta["device_id_domain"].encode().hex(),
                             "expected": hashlib.sha256(meta["device_id_domain"].encode() + key).hexdigest()},
               "integers": {"u32_max": str(2**32 - 1), "u64_max": str(2**64 - 1)},
               "private_client_types": [row["name"] for row in rows if row["visibility"] == "private"]}
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
