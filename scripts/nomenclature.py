#!/usr/bin/env python3
"""Rewrite the tree to the vocabulary of docs/glossary.md, and say where it still is not.

    scripts/nomenclature.py --dry-run          what would change, by category (writes nothing)
    scripts/nomenclature.py --tokens           every old word and what it becomes, with collisions
    scripts/nomenclature.py --apply            rewrite file contents and rename files, idempotently
    scripts/nomenclature.py --check            exit 1 while anything would still change

Words are the runs of letters, digits and underscores; each is cut at underscores and at camelCase
boundaries, so ``paired_fingerprints`` and ``PairingServer`` are found and ``repair`` and ``socketpair``
are not. A word named in OVERRIDES is replaced whole; otherwise each banned part is replaced by its entry
in STEMS with the case it had. PHRASES run first, for the places a word-by-word rewrite reads badly.
Files and words that stay are named in ``contracts/glossary-allowlist.toml``, each with its reason.
"""

from __future__ import annotations

import argparse
import collections
import fnmatch
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "contracts" / "glossary-allowlist.toml"
RULES = ROOT / "contracts" / "glossary-rules.toml"
PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+|_")
WORD = re.compile(r"[A-Za-z0-9_]+")
PROJECT_BOARD = re.compile(r"(?<![A-Za-z])[Pp]roject_?(board)", re.I)
KINDS = {".rs": "rust", ".py": "python", ".toml": "toml", ".md": "markdown", ".html": "html",
         ".json": "json", ".js": "javascript", ".css": "css", ".sh": "shell", ".java": "java"}


def load(path: Path) -> dict:
    """A TOML file of the vocabulary's data: its rules, or the allowlist."""
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def tracked(root: Path = ROOT) -> list[str]:
    """Every file git tracks under ``root``, as paths from it."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=False)
    if out.returncode:
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    return [p for p in out.stdout.split("\0") if p and (root / p).is_file()]


def kind_of(path: str) -> str:
    """The language of a file, for the rules that read one language differently."""
    return KINDS.get(Path(path).suffix, "other")


def area_of(path: str) -> str:
    """Where a file lives, for the dry-run's grouping."""
    head = path.split("/")[0]
    if path.startswith("tests/") or "/tests/" in path:
        return "tests"
    return {"src": "src", "app": "app", "docs": "docs", "scripts": "scripts"}.get(head, "top" if "/" not in path else head)


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def _table(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(v, str) and v.strip() for v in value.values())


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _paths(value: object) -> bool:
    return _text(value) or (_strings(value) and bool(value))


def _boolean(value: object) -> bool:
    return type(value) is bool


def _entries(data: dict, name: str, fields: dict[str, Callable[[object], bool]], required: set[str]) -> None:
    rows = data.get(name, [])
    if not isinstance(rows, list):
        raise ValueError("Invalid vocabulary policy entries")
    for row in rows:
        if not isinstance(row, dict) or not required <= row.keys() or row.keys() - fields.keys():
            raise ValueError("Invalid vocabulary policy entry")
        for key, value in row.items():
            check = fields[key]
            if not check(value):
                raise ValueError("Invalid vocabulary policy value")


def policy(root: Path) -> tuple[dict, dict]:
    """Validated vocabulary data from the tree being checked."""
    data = []
    for name in ("glossary-rules.toml", "glossary-allowlist.toml"):
        path = root / "contracts" / name
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Vocabulary policy must be a file inside the checked tree")
        try:
            data.append(tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("Missing or malformed vocabulary policy") from exc
    rules, allow = data
    names = {"stems", "kind_stems", "overrides", "literal", "phrase", "family", "revoke_near", "renames", "gate"}
    if rules.keys() - names or not {"family", "stems", "gate"} <= rules.keys():
        raise ValueError("Unknown or incomplete vocabulary rules")
    if allow.keys() - {"exclude", "allow", "token"}:
        raise ValueError("Unknown vocabulary allowances")
    for key in ("stems", "overrides", "renames"):
        if not _table(rules.get(key, {})):
            raise ValueError("Invalid vocabulary replacement table")
    families = rules["family"]
    kinds = rules.get("kind_stems", {})
    if not isinstance(families, dict) or not families or not all(_strings(v) and v for v in families.values()):
        raise ValueError("Invalid vocabulary families")
    if not rules["stems"] or not isinstance(kinds, dict) or not all(_table(v) for v in kinds.values()):
        raise ValueError("Invalid vocabulary stems")
    if not _strings(rules.get("revoke_near", [])):
        raise ValueError("Invalid vocabulary neighboring words")
    _entries(rules, "literal", {"old": _text, "new": _text, "paths": _strings}, {"old", "new"})
    _entries(rules, "phrase", {"pattern": _text, "new": _text, "kinds": _strings,
                              "family": _text, "ignore_case": _boolean}, {"pattern", "new", "family"})
    _entries(rules, "gate", {"pattern": _text, "family": _text, "say": _text}, {"pattern", "family", "say"})
    if not rules["gate"]:
        raise ValueError("Missing vocabulary gates")
    _entries(allow, "exclude", {"path": _text, "reason": _text}, {"path", "reason"})
    _entries(allow, "allow", {"path": _paths, "words": _strings, "reason": _text}, {"path", "words", "reason"})
    _entries(allow, "token", {"token": _text, "reason": _text}, {"token", "reason"})
    known = set(families) | {row["family"] for row in rules["gate"]}
    if any(set(row["words"]) - known for row in allow.get("allow", [])):
        raise ValueError("Unknown vocabulary allowance family")
    try:
        for row in rules.get("phrase", []) + rules["gate"]:
            re.compile(row["pattern"])
    except re.error as exc:
        raise ValueError("Invalid vocabulary pattern") from exc
    return rules, allow


class Vocabulary:
    """The rules and the allowlist, loaded once, and the rewrite they describe."""

    def __init__(self, only: set[str] | None = None, *, root: Path = ROOT) -> None:
        rules, allow = policy(root)
        self.only = only
        self.stems: dict[str, str] = rules.get("stems", {})
        self.kind_stems: dict[str, dict[str, str]] = rules.get("kind_stems", {})
        self.overrides: dict[str, str] = rules.get("overrides", {})
        self.literals: list[tuple[str, str, tuple[str, ...]]] = [
            (r["old"], r["new"], tuple(r.get("paths", ()))) for r in rules.get("literal", [])]
        self.phrases = [(re.compile(r["pattern"], re.I if r.get("ignore_case", True) else 0), r["new"],
                         tuple(r.get("kinds", ())), r["family"]) for r in rules.get("phrase", [])]
        self.families: dict[str, str] = {w: f for f, ws in rules.get("family", {}).items() for w in ws}
        self.revoke_near: set[str] = set(rules.get("revoke_near", []))
        self.exclude = [(e["path"], e["reason"]) for e in allow.get("exclude", [])]
        self.allow = [(pattern, tuple(a["words"]), a["reason"]) for a in allow.get("allow", [])
                      for pattern in ([a["path"]] if isinstance(a["path"], str) else a["path"])]
        self.keep_tokens = {t["token"] for t in allow.get("token", [])}
        self.renames: dict[str, str] = rules.get("renames", {})
        self.gates = [(re.compile(g["pattern"], re.I), g["family"], g["say"]) for g in rules.get("gate", [])]

    def excluded(self, path: str) -> bool:
        """Whether the whole file is outside the vocabulary (history, vendored, third party)."""
        return any(fnmatch.fnmatch(path, pattern) for pattern, _ in self.exclude)

    def allowed(self, path: str) -> set[str]:
        """The families of word a file may keep, each with a reason in the allowlist."""
        kept = {w for pattern, words, _ in self.allow if fnmatch.fnmatch(path, pattern) for w in words}
        return kept | (set(self.families.values()) - self.only if self.only else set())

    def parts(self, word: str) -> list[str]:
        """The parts of one word: its snake_case and camelCase pieces."""
        return PART.findall(word)

    def family(self, part: str) -> str:
        """The family a part belongs to ('' when it is not a banned word)."""
        return self.families.get(part.lower(), "")

    def banned_parts(self, parts: list[str]) -> list[str]:
        """The family of each part, '' for a part that is allowed: a revoke part counts only beside a
        device, member, machine or pool, where it means taking one out; a key is still revoked."""
        out = []
        for i, part in enumerate(parts):
            fam = self.family(part)
            if fam == "revoke":
                near = [p.lower() for p in parts[max(0, i - 3):i + 4] if p != "_"]
                fam = fam if self.revoke_near & set(near) else ""
            out.append(fam)
        return out

    def new_word(self, word: str, keep: set[str], kind: str = "") -> str:
        """``word`` in the vocabulary; the same word when nothing in it is banned or allowed to stay."""
        if word in self.keep_tokens:
            return word
        if word in self.overrides and self.family_of_word(word) not in keep:
            return self.overrides[word]
        parts = self.parts(word)
        out = []
        for part, fam in zip(parts, self.banned_parts(parts), strict=True):
            if fam and fam not in keep and part.lower() in self.stems:
                out.append(carry_case(part, self.kind_stems.get(kind, {}).get(part.lower(), self.stems[part.lower()])))
            else:
                out.append(part)
        return PROJECT_BOARD.sub(lambda m: m.group(1).capitalize() if m.group(0)[0].isupper() else m.group(1),
                                 "".join(out)) if "board" in "".join(out).lower() else "".join(out)

    def family_of_word(self, word: str) -> str:
        """The first banned family a word holds, or ''."""
        return next((f for f in self.banned_parts(self.parts(word)) if f), "")

    def rewrite(self, text: str, path: str) -> tuple[str, collections.Counter]:
        """``text`` rewritten for the file at ``path``, and how many times each family changed it."""
        keep, kind = self.allowed(path), kind_of(path)
        hits: collections.Counter = collections.Counter()
        for old, new, paths in self.literals:
            if paths and not any(fnmatch.fnmatch(path, pattern) for pattern in paths):
                continue
            if old in text:
                hits["literal"] += text.count(old)
                text = text.replace(old, new)
        for pattern, new, kinds, family in self.phrases:
            if family in keep or (kinds and kind not in kinds):
                continue
            text, n = pattern.subn(lambda m, new=new: expand(m, new), text)
            if n:
                hits[family] += n

        def word(match: re.Match) -> str:
            old = match.group(0)
            new = self.new_word(old, keep, kind if plain_word(text, match) else "")
            if new != old:
                hits[self.family_of_word(old)] += 1
            return new

        return WORD.sub(word, text), hits

    def new_path(self, path: str) -> str:
        """``path`` with its own name in the vocabulary (a directory or file part renamed)."""
        if path in self.renames:
            return self.renames[path]
        keep = self.allowed(path)
        pieces = []
        for piece in path.split("/"):
            stem, dot, ext = piece.partition(".")
            pieces.append(WORD.sub(lambda m: self.new_word(m.group(0), keep), stem) + dot + ext)
        return "/".join(pieces)


    def findings(self, text: str, path: str) -> list[tuple[int, str, str]]:
        """What a file still says that the glossary bans, as ``(line, found, family)``.

        A word counts when it holds a banned part whose family the allowlist does not let this file keep
        and it is not an allowed token; a gate phrase counts when its family is not allowed here.
        """
        keep, found = self.allowed(path), []
        for n, line in enumerate(text.split("\n"), 1):
            for word in WORD.findall(line):
                family = self.family_of_word(word)
                if family and family not in keep and word not in self.keep_tokens:
                    found.append((n, word, family))
            found += [(n, m.group(0), fam) for pattern, fam, _ in self.gates if fam not in keep
                      for m in pattern.finditer(line)]
        return found


def plain_word(text: str, match: re.Match) -> bool:
    """Whether a word stands alone in prose, not as a part of a path, a command, an option or code."""
    before = text[match.start() - 1] if match.start() else " "
    after = text[match.end()] if match.end() < len(text) else " "
    ahead = text[match.end() + 1] if match.end() + 1 < len(text) else " "
    joined = before in "-_/." or after in "-_/" or (after == "." and ahead.isalnum())
    return not (joined or (before == "`" and after == "`") or len(PART.findall(match.group(0))) > 1)


def carry_case(old: str, new: str) -> str:
    """``new`` written the way ``old`` was: UPPER, Title or lower."""
    if old.isupper() and len(old) > 1:
        return new.upper()
    return new.capitalize() if old[:1].isupper() else new


def expand(match: re.Match, new: str) -> str:
    """A phrase's replacement, with the case of the match's first letter and its groups filled in."""
    out = match.expand(new)
    return out[:1].upper() + out[1:] if match.group(0)[:1].isupper() else out


def text_of(path: str, root: Path = ROOT) -> str | None:
    """A tracked file's text, or None for a file that is not UTF-8 text."""
    try:
        return (root / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def plan(vocab: Vocabulary) -> tuple[dict[str, tuple[str, collections.Counter]], dict[str, str]]:
    """The content changes and the file renames the vocabulary calls for."""
    changes: dict[str, tuple[str, collections.Counter]] = {}
    moves: dict[str, str] = {}
    for path in tracked():
        text = None if vocab.excluded(path) else text_of(path)
        if text is not None:
            new, hits = vocab.rewrite(text, path)
            if new != text:
                changes[path] = (new, hits)
        target = vocab.new_path(path)
        if target != path:
            moves[path] = target
    return changes, moves


def report(changes: dict, moves: dict) -> None:
    """Print the plan by family and by area, then every file that would move."""
    by_family: collections.Counter = collections.Counter()
    by_area: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for path, (_, hits) in changes.items():
        for family, n in hits.items():
            by_family[family] += n
            by_area[area_of(path)][family] += n
    print(f"{len(changes)} files change, {len(moves)} files move")
    for family, n in by_family.most_common():
        print(f"  {family:10} {n:6}")
    for area, counts in sorted(by_area.items()):
        print(f"  {area:10} " + ", ".join(f"{f} {n}" for f, n in counts.most_common()))
    for old, new in sorted(moves.items()):
        print(f"  move {old} -> {new}")


def token_table(vocab: Vocabulary) -> dict[str, dict[str, int]]:
    """Every word the rules change, what it becomes, and how often it occurs."""
    table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for path in tracked():
        text = text_of(path)
        if vocab.excluded(path) or text is None:
            continue
        keep = vocab.allowed(path)
        for old in WORD.findall(text):
            if vocab.family_of_word(old) or old in vocab.overrides:
                new = vocab.new_word(old, keep)
                if new != old:
                    table[old][new] += 1
    return {old: dict(c) for old, c in table.items()}


def collisions(vocab: Vocabulary, table: dict) -> list[tuple[str, str]]:
    """Pairs of ``(old, new)`` where ``new`` is already a word somewhere in the tree."""
    present = {w for path in tracked() if (t := text_of(path)) is not None for w in set(WORD.findall(t))}
    return sorted((old, new) for old, news in table.items() for new in news if new in present and new != old)


def show(changes: dict) -> None:
    """Print each changed line as before and after, for the review of a rewrite."""
    for path, (text, _) in sorted(changes.items()):
        old = (text_of(path) or "").split("\n")
        for n, (a, b) in enumerate(zip(old, text.split("\n"), strict=False), 1):
            if a != b:
                print(f"{path}:{n}\n  - {a.strip()[:220]}\n  + {b.strip()[:220]}")


def apply(changes: dict, moves: dict) -> None:
    """Write the new contents, then move the files with git so history follows them."""
    for path, (text, _) in changes.items():
        (ROOT / path).write_text(text, encoding="utf-8")
    clash = [new for old, new in moves.items() if (ROOT / new).exists() and new not in moves]
    if clash:
        raise SystemExit(f"a file already has the new name: {clash}")
    for old, new in moves.items():
        (ROOT / new).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "mv", old, new], cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    """Run the mode named on the command line; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("dry-run", "tokens", "apply", "check", "show"):
        mode.add_argument(f"--{name}", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated families to rewrite (default: all)")
    args = parser.parse_args(argv)
    vocab = Vocabulary(set(filter(None, args.only.split(","))) or None)
    if args.tokens:
        table = token_table(vocab)
        for old, news in sorted(table.items()):
            print(f"{sum(news.values()):5} {old} -> {', '.join(news)}")
        for old, new in collisions(vocab, table):
            print(f"COLLISION {old} -> {new} already exists")
        return 0
    changes, moves = plan(vocab)
    if args.show:
        show(changes)
        return 0
    if args.apply:
        apply(changes, moves)
        return 0
    report(changes, moves)
    return 1 if args.check and (changes or moves) else 0


if __name__ == "__main__":
    sys.exit(main())
