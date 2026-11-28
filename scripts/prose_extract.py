"""Where agent guidance lives, and the sentences in it that tell an agent to do or not do something."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

SEEDS = ("AGENTS.md", "CLAUDE.md")
DOCUMENTS = ("AGENTS.md", "CLAUDE.md", "*/AGENTS.md", "*/CLAUDE.md", "CONTRIBUTING.md", ".claude/agents/*.md",
             ".claude/commands/*.md", ".claude/skills/*.md", ".agents/*.md")
"""Documents that are guidance by where they are; the ones the seeds link or name, two steps deep, are added."""
NOT_FOLLOWED = ("docs/notes/*", "docs/research/*", "docs/experiments/*", "docs/design-*", "README.md", "CHANGELOG.md")
LINK_DEPTH = 2
SOURCES = ("src/poolhouse/briefing.py", "src/poolhouse/delegate*.py", "src/poolhouse/worktreerules.py",
           "src/poolhouse/protectedwrites.py", "src/poolhouse/harnesshook.py", "src/poolhouse/rules.py",
           "src/poolhouse/coding_policy.py", "src/poolhouse/agent_hooks.py", "src/poolhouse/guard/*.py",
           "src/poolhouse/workspace/*nudge*.py", "src/poolhouse/workspace/*brief*.py",
           "src/poolhouse/workspace/onboard.py", "src/poolhouse/workspace/codex_hooks.py", "scripts/hooks/*",
           "scripts/lead-review")
"""Source files whose strings reach an agent: briefs, delegate briefs, hook and guard messages."""

VERBS = frozenset(re.findall(r"[a-z']+", """account act add always announce answer apply ask avoid brief build call
cancel check cite claim classify clean collect commit complete confirm consider copy count cover create declare define
delegate delete diagnose do don't document ensure explain fetch finish fix follow give go hand include inspect install
keep land lease leave list load make mark measure merge minimize name never no note only open own pass pick post prefer
preserve push put quote read rebuild recheck reconcile record refresh remove report retain return review run say scan
send serve set show spawn split stage start state stop sync take tell test treat update use verify wait work write"""))
"""The words a sentence opens with when it is an order."""
MODAL = re.compile(r"\b(must|never|always|do not|don't|should|shouldn't|may not|may only|not allowed|forbidden|"
                   r"required to|requires?\b|needs? (a|an|the)\b|no (agent|worker|one)\b)", re.I)
ADDRESSED = re.compile(r"^(agents?|workers?|subagents?|delegates?|you)\b", re.I)
LEAD = re.compile(r"^(before|after|when|if|while|first|then|for|on|at|during|until|once|otherwise)\b[^,:]*[,:]\s*", re.I)
SENTENCE_END = re.compile(r"[.!?](?:[\"')\]*_`]*)\s+(?=[\"'(\[*_`]*[A-Z0-9`])")
ABBREVIATION = re.compile(r"\b(e\.g|i\.e|vs|etc|cf)\.$", re.I)
BULLET = re.compile(r"^\s*([-*+]|\d+\.)\s+")
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
TARGET = re.compile(r"\]\(([^)#\s]+\.md)(#[^)]*)?\)")
NAMED = re.compile(r"(?<![\w./(-])(?:docs|tests)/[\w./-]+\.md\b")
"""A document named by its path from the repository root, outside a link."""
SHELL_STRING = re.compile(r"\"([^\"\n]{20,})\"")
FEWEST_WORDS = 3
FEWEST_SOURCE_WORDS = 5


@dataclass
class Item:
    """One sentence of guidance, where it first stands and every other line of the same file that repeats it."""

    id: str
    path: str
    line: int
    text: str
    kind: str
    also: list[int] = field(default_factory=list)


def repository_files(root: Path) -> list[str]:
    """Every file git does not ignore under ``root``, as paths from it; every file when ``root`` is no checkout."""
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root, capture_output=True,
                            check=False)
    if listed.returncode == 0:
        names = listed.stdout.decode(errors="replace").split("\0")
    else:
        names = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    return sorted(n for n in names if n and (root / n).is_file())


def text_of(path: Path) -> str:
    """The file's text, empty when it cannot be read as UTF-8."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _linked(root: Path, name: str, known: set[str]) -> set[str]:
    """The repository documents ``name`` links to or names by path."""
    content, found = text_of(root / name), set()
    places = [(root / name).parent / target for target, _anchor in TARGET.findall(content)]
    for place in [*places, *(root / named for named in NAMED.findall(content))]:
        try:
            linked = place.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if linked in known and not matches(linked, NOT_FOLLOWED):
            found.add(linked)
    return found


def guidance_files(root: Path, files: list[str]) -> dict[str, str]:
    """Path -> ``document`` or ``source`` for every file that holds agent guidance."""
    known = set(files)
    documents = {name for name in files if matches(name, DOCUMENTS)}
    frontier = {name for name in SEEDS if name in known}
    for _ in range(LINK_DEPTH):
        frontier = set().union(*(_linked(root, name, known) for name in frontier)) - documents
        documents |= frontier
    found = {name: "source" for name in files if matches(name, SOURCES)}
    found.update(dict.fromkeys(documents, "document"))
    return found


def scoped(root: Path, files: list[str], scope: list[str]) -> dict[str, str]:
    """The guidance under the named paths; a path holding none of the known guidance is read whole."""
    every = guidance_files(root, files)
    if not scope:
        return every
    chosen: dict[str, str] = {}
    for wanted in scope:
        held = {n: k for n, k in every.items() if under(n, wanted)}
        if not held:
            held = {n: "document" if n.endswith(".md") else "source" for n in files
                    if under(n, wanted) and n.endswith((".md", ".py"))}
        chosen.update(held)
    return chosen


def under(name: str, wanted: str) -> bool:
    """Whether the file is the named path or inside it."""
    wanted = wanted.rstrip("/")
    return wanted in ("", ".") or name == wanted or name.startswith(wanted + "/")


def blocks(text: str) -> Iterator[list[tuple[int, str]]]:
    """The prose units of a markdown text (a paragraph, a bullet, a table cell) as numbered lines."""
    unit: list[tuple[int, str]] = []
    fenced = False
    lines = text.split("\n")
    skip = 0
    if lines and lines[0].strip() == "---":
        skip = next((i + 1 for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0)
    for number, line in enumerate(lines[skip:], skip + 1):
        bare = line.strip()
        fence = bare.startswith(("```", "~~~"))
        apart = fenced or fence or not bare or bare.startswith(("#", "|", "<!--"))
        if unit and (apart or BULLET.match(line)):
            yield unit
            unit = []
        if fence:
            fenced = not fenced
        elif not fenced and bare.startswith("|"):
            cells = [c.strip() for c in bare.strip("|").split("|")]
            yield from ([(number, cell)] for cell in cells if cell.strip("-: "))
        elif not apart:
            unit.append((number, BULLET.sub("", line).strip()))
    if unit:
        yield unit


def sentences(unit: list[tuple[int, str]]) -> Iterator[tuple[int, str]]:
    """Each sentence of one unit with the line it starts on."""
    joined, starts = "", []
    for number, line in unit:
        starts.append((len(joined), number))
        joined += line + " "
    begin = 0
    ends = [m.end() for m in SENTENCE_END.finditer(joined)
            if not ABBREVIATION.search(joined[:m.start() + 1]) and joined[:m.start()].count("`") % 2 == 0]
    for end in [*ends, len(joined)]:
        piece = joined[begin:end].strip()
        if piece:
            line = [number for offset, number in starts if offset <= begin][-1]
            yield line, clean(piece)
        begin = end


def clean(sentence: str) -> str:
    """The sentence without link targets and emphasis marks, on one line; code spans stay."""
    sentence = MARKDOWN_LINK.sub(r"\1", sentence).replace("**", "")
    return " ".join(sentence.split())


def instructs(sentence: str, fewest: int = FEWEST_WORDS) -> bool:
    """Whether the sentence tells an agent to do or not do something."""
    words = sentence.split()
    if len(words) < fewest or sentence.endswith("?"):
        return False
    if MODAL.search(sentence) or ADDRESSED.match(sentence):
        return True
    head = LEAD.sub("", sentence).lstrip("\"'(*_ ")
    first = re.match(r"[A-Za-z']+", head)
    return bool(first) and first.group(0).lower() in VERBS


def normal(sentence: str) -> str:
    """The words of a sentence, lower case, without markup: what its id and a link's ``match`` read."""
    return " ".join(sentence.lower().replace("`", "").replace("*", "").split())


def identity(path: str, sentence: str) -> str:
    """An id that stays while the sentence stays in its file, wherever it moves."""
    return "p-" + hashlib.sha256(f"{path}\0{normal(sentence)}".encode()).hexdigest()[:10]


def _strings(source: str) -> Iterator[tuple[int, int, str]]:
    """(first line, last line, value) of every string a Python file uses; docstrings are not used."""
    tree = ast.parse(source)
    unused = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    inside = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for part in node.values}
    for node in ast.walk(tree):
        if id(node) in unused or id(node) in inside:
            continue
        if isinstance(node, ast.JoinedStr):
            value = "".join(str(p.value) if isinstance(p, ast.Constant) else "{...}" for p in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
        else:
            continue
        yield node.lineno, node.end_lineno or node.lineno, value


def _line_of(lines: list[str], first: int, last: int, sentence: str) -> int:
    """The source line inside a string's span that holds the sentence's first words."""
    opening = " ".join(sentence.split()[:3])
    return next((n for n in range(first, last + 1) if opening in lines[n - 1]), first)


def source_sentences(source: str) -> Iterator[tuple[int, str]]:
    """The sentences of a source file's strings; a file that is not Python is read for its quoted strings."""
    lines = source.split("\n")
    try:
        found = list(_strings(source))
    except (SyntaxError, ValueError):
        found = [(n, n, m) for n, line in enumerate(lines, 1) for m in SHELL_STRING.findall(line)]
    for first, last, value in sorted(found):
        for unit in blocks(value):
            for _, sentence in sentences(unit):
                yield _line_of(lines, first, last, sentence), sentence


def extract(root: Path, chosen: dict[str, str]) -> list[Item]:
    """Every instruction in the chosen files, one item per sentence and file."""
    items: dict[str, Item] = {}
    for path, kind in sorted(chosen.items()):
        content = text_of(root / path)
        if kind == "document":
            found = (numbered for unit in blocks(content) for numbered in sentences(unit))
            fewest = FEWEST_WORDS
        else:
            found, fewest = source_sentences(content), FEWEST_SOURCE_WORDS
        for line, sentence in found:
            if not instructs(sentence, fewest):
                continue
            key = identity(path, sentence)
            if key not in items:
                items[key] = Item(key, path, line, sentence, kind)
            elif line not in (items[key].line, *items[key].also):
                items[key].also.append(line)
    return list(items.values())
