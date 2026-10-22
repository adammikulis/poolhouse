"""Write THIRD_PARTY_NOTICES.md, or with --check fail on a disallowed licence.

The Python side is the installed closure of the extras a bundle ships (`poolhouse.installed.extras`);
the Rust side is `cargo metadata` over a copy of app, when cargo is on PATH.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "scripts" / "notices_static.md"
OUT = ROOT / "THIRD_PARTY_NOTICES.md"
EXTRAS = ("store", "hub", "web", "plot", "graph", "serve")

DISALLOWED = re.compile(
    r"\b(A?GPL|LGPL|GNU|SSPL|Commons[- ]Clause|CC-BY-NC|Non-?Commercial|Proprietary|UNKNOWN)", re.I
)
"""Terms that do not belong in a bundle distributed under Apache-2.0."""


LICENSE_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    ("socksio", "1.0.0"): (
        "MIT",
        "socksio-1.0.0.dist-info/LICENSE is the MIT License (Copyright (c) 2019, the socksio authors, "
        "see the project's repository) and the METADATA classifier says 'License :: OSI Approved :: MIT "
        "License'; only the legacy License: field is the literal 'UNKNOWN'",
    ),
}
"""Packages whose metadata does not state a licence, keyed by (normalised name, exact version).

Each entry names the licence and where the claim was verified. The exact version is part of the key so an upgrade
has to be looked at again; this is not an allow-list of licences.
"""


def _license_of(dist: metadata.Distribution) -> str:
    md = dist.metadata
    override = LICENSE_OVERRIDES.get((canonicalize_name(md["Name"]), dist.version))
    if override:
        return override[0]
    expr = md.get("License-Expression")
    if expr:
        return expr.strip()
    classifiers = [
        c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    text = (md.get("License") or "").strip()
    if text and len(text) <= 80 and "\n" not in text:
        return text
    return "; ".join(c for c in classifiers if c != "OSI Approved") or "UNKNOWN"


def python_closure() -> list[tuple[str, str, str]]:
    seen: dict[str, metadata.Distribution] = {}
    todo: list[tuple[str, tuple[str, ...]]] = [("poolhouse", EXTRAS)]
    while todo:
        name, extras = todo.pop()
        key = canonicalize_name(name)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        if key in seen and key != "poolhouse":
            continue
        seen[key] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is not None and not any(req.marker.evaluate({"extra": e}) for e in (*extras, "")):
                continue
            todo.append((req.name, tuple(req.extras)))
    rows = [(d.metadata["Name"], d.version, _license_of(d)) for k, d in seen.items() if k != "poolhouse"]
    return sorted(rows, key=lambda r: r[0].lower())


def rust_crates() -> list[tuple[str, str, str]]:
    if shutil.which("cargo") is None:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "app"
        shutil.copytree(ROOT / "app", work, ignore=shutil.ignore_patterns("target", "node_modules", "android"))
        done = subprocess.run(
            ["cargo", "metadata", "--format-version", "1"], cwd=work, capture_output=True, text=True, check=False
        )
    if done.returncode != 0:
        return []
    packages = json.loads(done.stdout)["packages"]
    rows = [(p["name"], p["version"], p.get("license") or "UNKNOWN") for p in packages if p.get("source")]
    return sorted(rows, key=lambda r: r[0].lower())


UI_ASSETS = "src/poolhouse/ui/assets"
UI_LICENCES = {"dompurify": "MPL-2.0 OR Apache-2.0", "marked": "MIT", "three": "MIT"}
"""The licence each npm package under `UI_ASSETS` declares; its text is the package's `<name>.LICENSE` beside it."""


def _manifests(root: Path) -> list[Path]:
    return sorted(p for p in (root / "examples").glob("**/vendor/vendor.json") if "node_modules" not in p.parts)


def vendored_rows(root: Path = ROOT) -> list[tuple[str, str, str, str]]:
    """(path, project, version, licence) for every vendored JavaScript file the repository records."""
    rows: list[tuple[str, str, str, str]] = []
    ui = root / UI_ASSETS / "vendor.json"
    if ui.is_file():
        data = json.loads(ui.read_text(encoding="utf-8"))
        for name, _source, target in data["files"]:
            rows.append((f"{UI_ASSETS}/{target}", name, data["dependencies"][name], UI_LICENCES.get(name, "UNKNOWN")))
    for manifest in _manifests(root):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in data["files"]:
            rel = (manifest.parent / entry["path"]).relative_to(root).as_posix()
            rows.append((rel, data["project"], data["version"], data["licence"]))
    return rows


def vendored_problems(root: Path = ROOT) -> list[str]:
    """A vendored file with no manifest entry, a missing file or licence text, or bytes that no longer match."""
    found: list[str] = []
    for manifest in _manifests(root):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        folder = manifest.parent
        listed = {e["path"] for e in data["files"]}
        if not (folder / data["licence_file"]).is_file():
            found.append(f"{folder.relative_to(root)}: licence text {data['licence_file']} is missing")
        for path in sorted(folder.glob("**/*.js")):
            if path.relative_to(folder).as_posix() not in listed:
                found.append(f"{path.relative_to(root)}: vendored JavaScript not listed in {manifest.name}")
        for entry in data["files"]:
            path = folder / entry["path"]
            if not path.is_file():
                found.append(f"{path.relative_to(root)}: listed in {manifest.name} but missing")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                found.append(f"{path.relative_to(root)}: sha256 differs from the pinned {data['package']} {data['version']}")
    for rel, _project, _version, licence in vendored_rows(root):
        if not (root / rel).is_file() or licence == "UNKNOWN":
            found.append(f"{rel}: vendored file missing or licence unknown")
    return found


def vendored_table() -> str:
    lines = ["| File | Project | Version | Licence |", "|---|---|---|---|"]
    lines += [f"| `{p}` | {n} | {v} | {lic} |" for p, n, v, lic in vendored_rows()]
    return "\n".join(lines)


def is_allowed(lic: str) -> bool:
    for alternative in re.split(r"\s+OR\s+|;|/", lic):
        if all(not DISALLOWED.search(part) for part in re.split(r"\s+AND\s+", alternative)):
            return True
    return False


def table(rows: list[tuple[str, str, str]]) -> str:
    lines = ["| Package | Version | Licence |", "|---|---|---|"]
    plain = re.compile(r"\b(MIT|BSD) License")
    for name, version, lic in rows:
        shown = plain.sub(r"\1", lic).replace("|", "/")
        lines.append(f"| `{name}` | {version} | {shown} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    py, rust = python_closure(), rust_crates()
    bad = [(n, v, lic) for n, v, lic in py + rust if not is_allowed(lic)]
    bad += [("vendored", "", line) for line in vendored_problems()]
    if "--check" in argv:
        for n, v, lic in bad:
            sys.stderr.write(f"disallowed licence: {n} {v}: {lic}\n")
        sys.stdout.write(f"checked {len(py)} Python and {len(rust)} Rust packages, {len(bad)} disallowed\n")
        return 1 if bad else 0
    parts = [
        "# Third-party notices",
        "",
        "Poolhouse is licensed under the Apache License 2.0 (`LICENSE`, `NOTICE`). `NOTICE` carries the notices for code "
        "ported into this repository. This file is generated by `scripts/notices.py`; do not edit the tables by hand.",
        "",
        STATIC.read_text(encoding="utf-8").rstrip(),
        "",
        "## Python packages in a bundle",
        "",
        f"Closure of the base requirements and the {', '.join(f'`{e}`' for e in EXTRAS)} extras at generation time.",
        "",
        table(py),
        "",
        "## Vendored JavaScript",
        "",
        "Copied into the repository, not installed from a registry. Each example's `vendor/vendor.json` pins the version "
        "and the sha256 of its files and the licence text sits beside them; `--check` fails when a vendored file is "
        "unlisted or its bytes change. The three.js texts (Copyright (c) 2010-2026 three.js authors) are the "
        "`three.LICENSE` files in `src/poolhouse/ui/assets` and the example's `vendor`.",
        "",
        vendored_table(),
        "",
        "## Rust crates in the window (`app/src-tauri`)",
        "",
        table(rust) if rust else "cargo was not available when this file was generated.",
        "",
    ]
    OUT.write_text("\n".join(parts), encoding="utf-8")
    sys.stdout.write(f"wrote {OUT.name}: {len(py)} Python, {len(rust)} Rust, {len(bad)} flagged\n")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
