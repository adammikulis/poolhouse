#!/usr/bin/env python3
"""List the agent guidance that is still prose: every instruction, what enforces it, and what changed since last run.

    scripts/prose_to_code.py [PATH ...] [--top N] [--json] [--state FILE] [--no-save]
    scripts/prose_to_code.py --link ID --verdict enforced|partly --evidence PATH::TEXT [--evidence ...] [--note TEXT]
    scripts/prose_to_code.py --decide ID --decision keep|encode|delete --note TEXT

The last run is kept in the repository's shared git folder, so every worktree compares against it. Links and
decisions are kept in ``scripts/prose_links.toml``, which these commands write. The report refuses nothing.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import prose_enforce as enforce
import prose_extract as extract

HARMS = (("secrets", 4, r"credential|secret|keystore|token|password|\bkeys?\b"),
         ("privacy", 3, r"real people|identifying|private|personal"),
         ("others' work", 3, r"other agents?|others'|running work|active (work|jobs)|claim"),
         ("history or loss", 3, r"force|destroy|delet|overwrit|discard|\bkill|\blose"),
         ("landing and release", 3, r"\bmain\b|\btags?\b|release|\bpush|\bmerge|publish"),
         ("security", 2, r"secur|\bauth|sandbox|guard|public|paid"))
MOST_HARM = 8
LOADED = {"AGENTS.md": 4, "CLAUDE.md": 4}
"""Documents every agent reads in every session; a sentence there costs time each time."""
OFTEN = ((".claude/", 2), (".agents/", 2), ("docs/agent-policy.md", 1), ("src/poolhouse/briefing.py", 4),
         ("src/poolhouse/workspace/onboard.py", 2))
ORDER = ("prose", "unknown", "partly", "enforced")


def score(item: extract.Item, copies: int) -> tuple[int, list[str]]:
    """How much encoding the sentence would matter, from facts the text and its place show; and those facts."""
    text = item.text.lower()
    harms = [(name, points) for name, points, pattern in HARMS if re.search(pattern, text)]
    total = min(MOST_HARM, sum(points for _, points in harms))
    why = [f"harm words: {', '.join(name for name, _ in harms)}"] if harms else []
    if re.search(r"\b(never|must|do not|don't)\b", text):
        total, why = total + 2, [*why, "absolute"]
    cost = LOADED.get(item.path) or next((points for start, points in OFTEN if item.path.startswith(start)), 0)
    if cost:
        total, why = total + cost, [*why, "read every session" if cost >= 4 else "read often"]
    if copies > 1:
        total, why = total + min(3, copies - 1), [*why, f"stated {copies} times"]
    if enforce.anchors(item.text):
        total, why = total + 2, [*why, "names something code can check"]
    if item.kind == "source" and "refused" in text:
        total, why = total - 4, [*why, "words of a refusal message"]
    return total, why


def scan(root: Path, scope: list[str]) -> tuple[list[dict], list[str], Callable[[str], bool]]:
    """The items highest score first with their verdicts, the records nothing answers to, and which files this run
    can speak for: those it read and those under its paths that are gone."""
    files = extract.repository_files(root)
    chosen = extract.scoped(root, files, scope)
    items = extract.extract(root, chosen)
    records = enforce.load_records(root / enforce.LINKS)
    verdicts = enforce.judge(items, enforce.Tree(root, files), records)
    copies = collections.Counter(extract.normal(item.text) for item in items)
    rows = []
    for item in items:
        points, why = score(item, copies[extract.normal(item.text)] + len(item.also))
        verdict = verdicts[item.id]
        rows.append({**dataclasses.asdict(item), **dataclasses.asdict(verdict), "open": verdict.open,
                     "score": points, "why": why})
    rows.sort(key=lambda r: (not r["open"], -r["score"], ORDER.index(r["status"]), r["path"], r["line"]))
    def covers(path: str) -> bool:
        inside = any(extract.under(path, wanted) for wanted in scope or ["."])
        return inside and (path in chosen or not (root / path).exists())

    return rows, enforce.unmatched(items, records, set(chosen), not scope), covers


def state_file(root: Path) -> Path:
    """Where the last run is kept: the git folder every worktree of the repository shares."""
    asked = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, capture_output=True, text=True,
                           check=False)
    folder = root / asked.stdout.strip() if asked.returncode == 0 else root / "build"
    return folder / "prose-to-code" / "last-run.json"


def compare(rows: list[dict], before: dict | None, covers: Callable[[str], bool]) -> dict:
    """What opened and what closed since the last run in the files this one speaks for; ``first`` when none was kept."""
    if before is None:
        return {"first": True, "new": [], "resolved": []}
    held = {key: old for key, old in before.items() if covers(old["path"])}
    now = {row["id"]: row for row in rows}
    new = [row["id"] for row in rows if row["open"] and row["id"] not in before]
    resolved = []
    for key, old in held.items():
        if not old["open"] or (key in now and now[key]["open"]):
            continue
        row = now.get(key)
        how = "sentence gone or reworded" if row is None else \
            "now enforced" if row["status"] == "enforced" else f"decided: {row['decision']}"
        resolved.append({"id": key, "how": how, "path": old["path"], "line": old["line"], "text": old["text"]})
    return {"first": False, "new": new, "resolved": resolved}


def read_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["items"]
    except (OSError, ValueError, KeyError):
        return None


def save_state(path: Path, rows: list[dict], before: dict | None, covers: Callable[[str], bool]) -> str:
    """Keep this run, leaving what it does not speak for as it was; returns why not, or an empty string."""
    kept = {key: old for key, old in (before or {}).items() if not covers(old["path"])}
    kept.update({r["id"]: {k: r[k] for k in ("path", "line", "text", "status", "open")} for r in rows})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(f".{os.getpid()}.part")
        part.write_text(json.dumps({"items": kept}), encoding="utf-8")
        part.replace(path)
    except OSError as error:
        return f"this run was not kept: {error}"
    return ""


def _entry(row: dict) -> list[str]:
    tag = f"  [{row['decision']}]" if row["decision"] else ""
    lines = [f"{row['id']}  {row['status']}  score {row['score']}  {row['path']}:{row['line']}{tag}",
             f"    {row['text']}", f"    why: {'; '.join(row['why']) or 'none'}"]
    if row["also"]:
        lines.append(f"    also at lines: {', '.join(map(str, row['also']))}")
    if row["evidence"]:
        lines.append(f"    seen: {'; '.join(row['evidence'])}")
    if row["note"]:
        lines.append(f"    note: {row['note']}")
    return lines


def report(rows: list[dict], changes: dict, lost: list[str], scope: list[str], top: int) -> str:
    """The run as text: the counts, what changed, what went stale and the highest-scored open sentences."""
    counts = collections.Counter(row["status"] for row in rows)
    waiting = [row for row in rows if row["open"]]
    lines = [f"prose-to-code: {len(rows)} instructions in {len({r['path'] for r in rows})} files "
             f"(scope: {' '.join(scope) or 'all'})",
             "status: " + ", ".join(f"{name} {counts[name]}" for name in reversed(ORDER)) + f"; open {len(waiting)}"]
    if changes["first"]:
        lines.append("since last run: first run, nothing to compare")
    else:
        lines.append(f"since last run: {len(changes['new'])} new, {len(changes['resolved'])} resolved")
        lines += [f"  resolved {r['id']}  {r['how']}  {r['path']}:{r['line']}  {r['text']}"
                  for r in changes["resolved"][:top]]
        fresh = set(changes["new"])
        lines += [f"  new {r['id']}  {r['status']}  {r['path']}:{r['line']}  {r['text']}"
                  for r in [r for r in rows if r["id"] in fresh][:top]]
    stale = [f"  {r['id']}  {r['note']}" for r in rows if r["note"].startswith("stale link")]
    if stale or lost:
        lines += ["stale records (fix or record again):", *stale, *(f"  {x}: matches no sentence" for x in lost)]
    lines.append(f"open, highest score first ({min(top, len(waiting))} of {len(waiting)}):")
    for row in waiting[:top]:
        lines += _entry(row)
    return "\n".join(lines) + "\n"


def record(root: Path, args: argparse.Namespace) -> str:
    """Write one link or decision about a sentence of this tree; raises ValueError naming what does not hold."""
    wanted = args.link or args.decide
    rows = scan(root, [])[0]
    row = next((r for r in rows if r["id"] == wanted), None)
    if row is None:
        raise ValueError(f"no sentence has the id {wanted}")
    if args.link:
        tree = enforce.Tree(root, extract.repository_files(root))
        broken = [why for _, why in map(tree.show, args.evidence) if why]
        if broken or not args.evidence or not args.verdict:
            raise ValueError("; ".join(broken) or "a link needs --verdict and --evidence PATH::TEXT")
        table, entry = "link", {"id": wanted, "source": row["path"], "match": "", "verdict": args.verdict,
                                "evidence": args.evidence, "note": args.note}
    else:
        if not args.decision or not args.note:
            raise ValueError("a decision needs --decision and --note")
        table, entry = "decision", {"id": wanted, "decision": args.decision, "note": args.note}
    enforce.append_record(root / enforce.LINKS, table, entry)
    return f"recorded {table} for {wanted} ({row['path']}:{row['line']}) in {enforce.LINKS}\n"


def parser() -> argparse.ArgumentParser:
    made = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    made.add_argument("paths", nargs="*", help="files or folders to scan; every guidance file when none")
    made.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    made.add_argument("--top", type=int, default=30, help="how many open sentences the text report lists")
    made.add_argument("--json", action="store_true", help="every sentence as JSON")
    made.add_argument("--state", type=Path, help="where the last run is kept")
    made.add_argument("--no-save", action="store_true", help="compare with the last run and leave it as it is")
    what = made.add_mutually_exclusive_group()
    what.add_argument("--link", metavar="ID", help="record what enforces a sentence")
    what.add_argument("--decide", metavar="ID", help="record what to do about a sentence")
    made.add_argument("--verdict", choices=enforce.VERDICTS)
    made.add_argument("--evidence", action="append", default=[], metavar="PATH::TEXT")
    made.add_argument("--decision", choices=enforce.DECISIONS)
    made.add_argument("--note", default="")
    return made


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.root.resolve()
    if args.link or args.decide:
        try:
            sys.stdout.write(record(root, args))
        except ValueError as error:
            sys.stderr.write(f"not recorded: {error}\n")
            return 2
        return 0
    scope = [Path(p).resolve().relative_to(root).as_posix() if Path(p).is_absolute() else p for p in args.paths]
    rows, lost, covers = scan(root, scope)
    kept = args.state or state_file(root)
    before = read_state(kept)
    changes = compare(rows, before, covers)
    failed = "" if args.no_save else save_state(kept, rows, before, covers)
    if args.json:
        sys.stdout.write(json.dumps({"items": rows, "changes": changes, "unmatched": lost}, indent=1) + "\n")
    else:
        sys.stdout.write(report(rows, changes, lost, scope, args.top))
    if scope and not rows:
        sys.stdout.write(f"no guidance found under: {' '.join(scope)}\n")
    if failed:
        sys.stderr.write(failed + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
