"""Lines of written code or script that download and run something, send a credential out or hide what they do.

`scan` returns one `Flag` per such line (a call or pipeline written over several lines counts as one);
`describe` writes the first few as plain text with each line shown as ASCII, so a direction mark or a
lookalike letter shows as its escape.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from poolhouse import safetext, secretpaths
from poolhouse.guard import flowscan

__all__ = ["Flag", "ascii_line", "describe", "scan"]

MAX_LINE = 20_000
MAX_JOINED = 4000
MAX_LINES = 10
SHOWN = 110
SHELL = r"(?:sh|bash|zsh|dash|ksh|fish|ash|python[\d.]*|perl|ruby|node|php|source)"
FETCH = r"(?:curl|wget|aria2c)"
SEND = (r"(?:\b(?:curl|wget|nc|ncat|netcat|socat|scp|sftp|rsync|ftp|telnet|sendmail|mail)\b|requests\.(?:post|put)|urlopen|http\.client"
        r"|\bfetch\(|XMLHttpRequest|smtplib|\.send\(|upload)")
RUNS = r"(?:\bexec\s*\(|\beval\s*\(|os\.system|os\.popen|subprocess\.\w+|child_process|execSync|\bFunction\s*\(|__import__|\|\s*(?:sh|bash)\b)"
DECODES = r"(?:b64decode|base64|decompress|fromhex|marshal\.loads|codecs\.decode|rot_?13|atob\(|unescape\(|fromCharCode|Buffer\.from\()"
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("downloads and runs code", re.compile(
        rf"(?i)(?:\b(?:sh|bash|zsh|dash|ksh)\s+(?:-c\s+)?[\"']?|\bsource\s+|\beval\s+[\"']?|(?<![\w.])\.\s+)(?:<\(|\$\()\s*{FETCH}\b|`\s*{FETCH}\b")),
    ("downloads and runs code", re.compile(
        r"(?i)\b(?:iex|invoke-expression)\b[^\n]*(?:iwr|invoke-webrequest|downloadstring|\birm\b|invoke-restmethod|webclient)"
        r"|(?:iwr|invoke-webrequest|downloadstring|\birm\b|invoke-restmethod)[^\n]*\|\s*(?:iex|invoke-expression)\b")),
    ("decodes hidden text and runs it", re.compile(rf"(?i)\A(?=.*\b(?:exec|eval|os\.system|os\.popen|subprocess\.\w+|execSync)\b)(?=.*{DECODES})")),
    ("downloads code and runs it in the same line", re.compile(
        r"(?i)\A(?=.*(?:urlopen|urllib\.request|requests\.(?:get|post)|httpx2?\.|http\.client|\bfetch\(|XMLHttpRequest))"
        rf"(?=.*{RUNS})")),
    ("opens a shell to another machine", re.compile(
        r"/dev/(?:tcp|udp)/|\bbash\s+-i\s*>?&|\bnc\s+(?:-\w+\s+)*-e\b|\bncat\b[^\n]*\s-e\b|\bmkfifo\b[^\n]*\b(?:nc|ncat)\b|pty\.spawn")),
    ("sends the whole environment out", re.compile(
        r"(?i)\A(?=.*(?:dict\(os\.environ\)|os\.environ\.copy\(\)|json\.dumps\(os\.environ|JSON\.stringify\(process\.env\)|\bprintenv\b|\$\(\s*env\s*\)))"
        rf"(?=.*{SEND})")),
    ("holds a direction-changing character", re.compile("[\u202a-\u202e\u2066-\u2069]")),
)
PIPE = re.compile(rf"(?i)\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:env\s+)?(?:\S*/)?{SHELL}\b")
FETCH_AT = re.compile(rf"(?i)\b(?:{FETCH[3:-1]}|base64\s+(?:-d|-D|--decode)|openssl\s+enc\s+-d|xxd\s+-r)\b")
STRICT = (("holds a character you cannot see", re.compile("[\u200b-\u200d\u2060\ufeff]")),)
CREDENTIAL = secretpaths.sensitive_path()
SENDS = re.compile(SEND)
CONTINUES = re.compile(r"(?:\\|\||&&|,|\(|\[|\{)\s*$")
OPEN, CLOSE = "([{", ")]}"


@dataclass(frozen=True, slots=True)
class Flag:
    """A flagged ``line`` (counted from 1), its ``text``, why it was flagged and where in the text it was found (``at``)."""

    line: int
    text: str
    why: str
    at: int = 0


def _logical(text: str) -> Iterator[tuple[int, str]]:
    """The ``(first line, text)`` of each statement: lines are joined while one ends in a backslash, ``|``, ``&&`` or a comma, or leaves a bracket open."""
    held: list[str] = []
    start = depth = 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw[:MAX_LINE]
        if not held:
            start = number
        held.append(line.rstrip().rstrip("\\") if line.rstrip().endswith("\\") else line)
        depth += sum(line.count(c) for c in OPEN) - sum(line.count(c) for c in CLOSE)
        if (CONTINUES.search(line) or depth > 0) and len(held) < MAX_LINES and sum(map(len, held)) < MAX_JOINED:
            continue
        yield start, " ".join(held)
        held, depth = [], 0
    if held:
        yield start, " ".join(held)


def _piped(line: str) -> tuple[str, int]:
    first = FETCH_AT.search(line)
    if first and PIPE.search(line, first.end()):
        decoded = first.group(0).lower().startswith(("base64", "openssl", "xxd"))
        return ("decodes hidden text and runs it" if decoded else "downloads and runs code"), first.start()
    return "", 0


def scan(text: str, *, strict: bool = False) -> list[Flag]:
    """The statements of ``text`` that fetch and run code, send a credential out or hide what they do;
    ``strict`` adds characters that cannot be seen."""
    flags: list[Flag] = []
    rules = (*RULES, *STRICT) if strict else RULES
    statements = list(_logical(text))
    for number, line in statements:
        found = next(((why, m.start()) for why, rule in rules if (m := rule.search(line))), None) or _piped(line)
        if not found[0] and CREDENTIAL.search(line) and (hit := SENDS.search(line)):
            found = ("sends a credential file out", hit.start())
        if found[0]:
            flags.append(Flag(number, line, *found))
    seen = {f.line for f in flags}
    flags += [Flag(*flow) for flow in flowscan.flows(statements) if flow[0] not in seen]
    return sorted(flags, key=lambda f: f.line)


def ascii_line(text: str, limit: int = SHOWN, at: int = 0) -> str:
    """``text`` stripped, with every character outside ASCII written as its escape, cut to ``limit`` around position ``at``."""
    plain = " ".join(text.strip().encode("ascii", "backslashreplace").decode("ascii").split())
    start = max(0, min(at, len(plain) - limit))
    return safetext.escape(plain[start:start + limit + 8], limit).replace("\u2026", "...")


def describe(flags: Sequence[Flag], most: int = 3) -> str:
    """The first ``most`` flags as ``why (line N: text; line M: text)``, empty for none."""
    if not flags:
        return ""
    shown = "; ".join(f"line {f.line}: {ascii_line(f.text, SHOWN, f.at)}" for f in flags[:most])
    more = f"; {len(flags) - most} more" if len(flags) > most else ""
    return f"{flags[0].why} ({shown}{more})"
