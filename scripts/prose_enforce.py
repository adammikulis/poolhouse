"""What already enforces a sentence of guidance, said only where a file and line can be shown.

``enforced`` and ``partly`` come from a link in ``scripts/prose_links.toml`` whose evidence is found in the tree now.
``prose`` means the sentence names something and no enforcement file names it. Everything else is ``unknown``.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from prose_extract import Item, matches, normal, text_of

LINKS = "scripts/prose_links.toml"
VERDICTS = ("enforced", "partly")
DECISIONS = ("keep", "encode", "delete")
SURFACES = ("scripts/hooks/*", "scripts/gates/*", "scripts/lead-review", "scripts/dev-branch-rule",
            "scripts/github-protection*", "scripts/test", ".github/workflows/*", ".claude/settings.json",
            ".codex/config.toml", "src/poolhouse/worktreerules.py", "src/poolhouse/protectedwrites.py",
            "src/poolhouse/harnesshook.py", "src/poolhouse/guard/*.py", "tests/*.py")
"""Hooks, gates, server rules, workflows, guard code and tests, best evidence first."""
NOT_SURFACES = ("tests/test_prose_to_code.py",)
WIRING = (".claude/settings.json", ".codex/config.toml", ".github/workflows/*", "scripts/install-hooks*",
          "scripts/hooks/*", "docs/landing.md")
CODE_SPAN = re.compile(r"`([^`]+)`")
BARE_NAME = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.(?:py|toml|json|yml|sh)|--[a-z][\w-]+|[A-Z][A-Z0-9]*_[A-Z0-9_]+)")
WORD = re.compile(r"[A-Za-z0-9_./-]+")
LONG_WORD = 8
SHOWN = 3


@dataclass
class Verdict:
    """The status of one sentence, what shows it, and what a reader decided to do about it."""

    status: str
    evidence: list[str] = field(default_factory=list)
    note: str = ""
    decision: str = ""

    @property
    def open(self) -> bool:
        """Whether the sentence still waits for work or a judgment."""
        return self.status != "enforced" and self.decision != "keep"


def load_records(path: Path) -> dict[str, list[dict]]:
    """The recorded links and decisions; none when there is no file yet."""
    held = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"link": held.get("link", []), "decision": held.get("decision", [])}


def append_record(path: Path, table: str, entry: dict[str, object]) -> None:
    """Add one ``[[table]]`` entry; a later entry for the same id replaces an earlier one when read."""
    lines = [f"\n[[{table}]]"]
    lines += [f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in entry.items() if value]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def anchors(sentence: str) -> list[str]:
    """The names in a sentence that code enforcing it would have to mention: commands, paths, flags, variables."""
    spans = CODE_SPAN.findall(sentence) + BARE_NAME.findall(CODE_SPAN.sub(" ", sentence))
    found: list[str] = []
    for span in spans:
        for word in WORD.findall(re.sub(r"<[^>]*>", " ", span)):
            word = word.lower().strip("./")
            distinct = len(word) >= LONG_WORD or any(mark in word.strip("-") for mark in "-_./") or word.startswith("--")
            if distinct and len(word) > 2 and not word.endswith(".md") and word not in found:
                found.append(word)
    return found


class Tree:
    """The enforcement files of one checkout, indexed by the words they hold."""

    def __init__(self, root: Path, files: list[str]):
        self.root = root
        self.files = files
        self.texts: dict[str, str] = {}
        self.lowered: dict[str, str] = {}
        self.found: dict[tuple[str, str], str] = {}
        self.surfaces = list(dict.fromkeys(name for pattern in SURFACES for name in files
                                           if matches(name, (pattern,)) and not matches(name, NOT_SURFACES)))

    def text(self, name: str) -> str:
        if name not in self.texts:
            self.texts[name] = text_of(self.root / name)
        return self.texts[name]

    def place(self, word: str, apart_from: str) -> str:
        """``path:line`` of the first enforcement file that holds the word anywhere, even inside a longer one."""
        if (word, apart_from) not in self.found:
            self.found[word, apart_from] = ""
            for name in self.surfaces:
                if name not in self.lowered:
                    self.lowered[name] = f"{name}\n{self.text(name)}".lower()
                at = self.lowered[name].find(word) if name != apart_from else -1
                if at >= 0:
                    self.found[word, apart_from] = f"{name}:{max(1, self.lowered[name].count(chr(10), 0, at))}"
                    break
        return self.found[word, apart_from]

    def wired(self, name: str) -> bool:
        """Whether a hook script is started by anything: a settings file, an installer, a workflow or another hook."""
        if not name.startswith("scripts/hooks/"):
            return True
        called = Path(name).name
        module = Path(name).stem
        return any(called in self.text(other) or (name.endswith(".py") and re.search(rf"\b{module}\b", self.text(other)))
                   for other in self.files if other != name and matches(other, WIRING))

    def show(self, evidence: str) -> tuple[str, str]:
        """(``path:line``, "") for a ``path::literal`` that holds now, or ("", why it does not)."""
        name, mark, literal = evidence.partition("::")
        if not mark or not literal:
            return "", f"{evidence!r} is not path::text"
        if name not in self.files:
            return "", f"{name} does not exist"
        for number, line in enumerate(self.text(name).split("\n"), 1):
            if literal in line:
                if not self.wired(name):
                    return "", f"nothing starts {name}"
                return f"{name}:{number}", ""
        return "", f"{name} no longer holds {literal!r}"


def link_applies(link: dict, item: Item) -> bool:
    """Whether a recorded link speaks of this sentence, by its id or by words it holds."""
    if link.get("source") and link["source"] != item.path:
        return False
    if link.get("id"):
        return link["id"] == item.id
    return bool(link.get("match")) and normal(link["match"]) in normal(item.text)


def _linked(links: list[dict], tree: Tree) -> Verdict | None:
    """The verdict of the last link that holds, or an ``unknown`` naming what went stale; None with no link."""
    stale = []
    for link in reversed(links):
        shown = [tree.show(evidence) for evidence in link.get("evidence", [])]
        broken = [why for _, why in shown if why]
        if shown and not broken and link.get("verdict") in VERDICTS:
            return Verdict(link["verdict"], [place for place, _ in shown], link.get("note", ""))
        stale += broken or [f"link with verdict {link.get('verdict')!r} and {len(shown)} evidence"]
    return Verdict("unknown", note="stale link: " + "; ".join(stale)) if stale else None


def _searched(item: Item, tree: Tree) -> Verdict:
    """What a search of the enforcement files shows about the names in the sentence."""
    names = anchors(item.text)
    if not names:
        return Verdict("unknown", note="names nothing to search for")
    found = [f"{name} at {place}" for name in names if (place := tree.place(name, item.path))]
    if not found:
        return Verdict("prose", note=f"none of {', '.join(names)} appears in {len(tree.surfaces)} enforcement files")
    return Verdict("unknown", found[:SHOWN], "named by enforcement files, which does not show it is enforced")


def judge(items: list[Item], tree: Tree, records: dict[str, list[dict]]) -> dict[str, Verdict]:
    """Item id -> verdict, with the recorded decision on it."""
    decisions = {entry["id"]: entry for entry in records["decision"]}
    verdicts = {}
    for item in items:
        verdict = _linked([x for x in records["link"] if link_applies(x, item)], tree) or _searched(item, tree)
        if item.id in decisions:
            verdict.decision = decisions[item.id]["decision"]
            verdict.note = decisions[item.id].get("note") or verdict.note
        verdicts[item.id] = verdict
    return verdicts


def unmatched(items: list[Item], records: dict[str, list[dict]], paths: set[str], everything: bool) -> list[str]:
    """Links no sentence answers to any more: all of them after a whole scan, else those naming a scanned file."""
    return [f"link {link.get('id') or link.get('match')!r}" for link in records["link"]
            if (everything or link.get("source") in paths) and not any(link_applies(link, item) for item in items)]
