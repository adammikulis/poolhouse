#!/usr/bin/env python3
"""Regenerate `contracts/language-kinds.json`, the language registry snapshot `guard.runs_later` reads (docs/design-isolated-launcher.md, 4.4.2).

The snapshot is generated from GitHub's public language registry (`languages.yml` of github-linguist/linguist) and records the registry
revision and the digest of the file it was made from, so a change of the registry is a reviewed diff and never a silent drift.

    python scripts/refresh_language_kinds.py                   # fetch the registry at its newest revision and write the snapshot
    python scripts/refresh_language_kinds.py --check           # the nightly job: exit 1 when the committed snapshot differs from the registry
    python scripts/refresh_language_kinds.py --source FILE     # build from a registry file on disk (the revision is then `local`)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from poolhouse import http  # noqa: E402

REPOSITORY = "github-linguist/linguist"
REGISTRY = "lib/linguist/languages.yml"
SNAPSHOT = ROOT / "contracts" / "language-kinds.json"
KINDS = ("programming", "markup", "prose", "data")
STRICTNESS = {kind: rank for rank, kind in enumerate(("prose", "markup", "programming", "data"))}
"""An extension two languages claim takes the strictest kind of its claimants: one data claimant makes it data."""


def fetch(revision: str) -> tuple[bytes, str]:
    """The registry file at ``revision`` (``main`` for the newest) and the commit it was read at."""
    commit = http.request_json(f"https://api.github.com/repos/{REPOSITORY}/commits/{revision}")["sha"]
    raw = http.request_bytes(f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{REGISTRY}").body
    return raw, commit


def build(raw: bytes, revision: str) -> dict:
    """The snapshot for the registry bytes ``raw`` read at ``revision``: the kind of each extension and each name the registry assigns."""
    extensions: dict[str, str] = {}
    names: dict[str, str] = {}
    for language, entry in yaml.safe_load(raw).items():
        kind = entry.get("type", "")
        if kind not in KINDS:
            raise ValueError(f"{language}: type {kind!r} is not one of {KINDS}")
        for table, keys in ((extensions, entry.get("extensions", ())), (names, entry.get("filenames", ()))):
            for key in keys:
                key = key.lower()
                if key not in table or STRICTNESS[kind] > STRICTNESS[table[key]]:
                    table[key] = kind
    return {"registry": {"repository": f"https://github.com/{REPOSITORY}", "file": REGISTRY, "revision": revision,
                         "digest": hashlib.sha256(raw).hexdigest()},
            "plain_kinds": ["programming", "markup", "prose"],
            "extensions": dict(sorted(extensions.items())), "names": dict(sorted(names.items()))}


def text(snapshot: dict) -> str:
    return json.dumps(snapshot, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the committed snapshot differs from the registry")
    parser.add_argument("--source", type=Path, help="a registry file on disk, in place of the network")
    parser.add_argument("--output", type=Path, default=SNAPSHOT)
    args = parser.parse_args(argv)
    raw, revision = (args.source.read_bytes(), "local") if args.source else fetch("main")
    made = text(build(raw, revision))
    if not args.check:
        args.output.write_text(made)
        print(f"{args.output}: registry revision {revision}")
        return 0
    current = args.output.read_text() if args.output.exists() else ""
    if current != made:
        sys.stderr.write(f"{args.output} differs from the registry at {revision}: run scripts/refresh_language_kinds.py and review the diff\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
