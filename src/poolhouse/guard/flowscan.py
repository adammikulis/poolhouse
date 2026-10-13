"""Written statements that pass what they download or copy from the environment to a later statement that runs it or sends it out.

`flows` follows a name from the statement that assigns it a download or the environment to a statement that runs or sends it,
a file from the statement that downloads it to one that makes it executable or runs it, and finds a shell built from pieces.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

__all__ = ["flows"]

FETCHED = re.compile(r"(?i)(?:urlopen|urllib\.request|requests\.(?:get|post)|httpx\.|http\.client|\bfetch\s*\(|XMLHttpRequest|\bcurl\b|\bwget\b|aria2c)")
ENVIRONMENT = re.compile(r"(?i)(?:dict\(os\.environ\)|os\.environ\.copy\(\)|json\.dumps\(os\.environ|JSON\.stringify\(process\.env\)|\{\s*\.\.\.process\.env\s*\}|\$\(\s*env\s*\)|\bprintenv\b)")
ASSIGNED = re.compile(r"^\s*(?:(?:const|let|var|export|local|declare|readonly)\s+)?([A-Za-z_]\w*)\s*(?::\s*[\w\[\], .]+)?=(?!=)\s*(.*)$", re.S)
RUNS = re.compile(r"(?:\bexec\s*\(|\beval\b|\bFunction\s*\(|os\.system|os\.popen|subprocess\.\w+|\bsystem\s*\(|child_process|execSync|\bspawn\w*\("
                  r"|\b(?:sh|bash|zsh)\s+-\w*c\b|\bsource\s|\|\s*(?:sh|bash)\b)")
SENDS = re.compile(r"(?i)(?:\b(?:curl|wget|nc|ncat|socat|scp|sftp|ftp|telnet|sendmail)\b|requests\.(?:post|put)|urlopen|http\.client|\bfetch\s*\(|XMLHttpRequest|smtplib|\.send(?:all)?\()")
SAVED = (re.compile(r"(?:-o|--output|-O|--output-document)[ =]\s*['\"]?([^\s'\"<>|;&]+)"),
         re.compile(r"\bwget\s+(?:-\S+\s+)*['\"]?https?://[^\s'\"?#]*/([^/\s'\"?#]+)"),
         re.compile(r"urlretrieve\(\s*[^,]+,\s*['\"]([^'\"]+)['\"]"))
SOCKET = re.compile(r"(?i)\bsocket\b|\bconnect\s*\(")
DUPLICATES = re.compile(r"\bdup2\b|pty\.spawn")
SHELL_PATH = re.compile(r"/bin/(?:ba)?sh|cmd\.exe|powershell")
PROCESS = re.compile(r"Popen|subprocess|os\.system|execv")
PASSES = 3


def _refers(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(name)}(?!\w)|\$\{{?{re.escape(name)}\}}?(?!\w)", text) is not None


def _tainted(stmts: Sequence[tuple[int, str]], seed: re.Pattern[str]) -> set[str]:
    """The names assigned something ``seed`` matches, or something built from a name already in the set."""
    names: set[str] = set()
    for _ in range(PASSES):
        before = len(names)
        for _, text in stmts:
            found = ASSIGNED.match(text)
            if found and found.group(1) not in names and (seed.search(found.group(2)) or any(_refers(found.group(2), n) for n in names)):
                names.add(found.group(1))
        if len(names) == before:
            break
    return names


def _runs_file(text: str, name: str) -> bool:
    safe = re.escape(name)
    start = rf"chmod\s+[^;&|\n]*{safe}|^\s*(?:sudo\s+)?(?:\./)?{safe}(?![\w.-])"
    interpreter = rf"\b(?:sh|bash|zsh|dash|python[\d.]*|node|perl|ruby|php|source|exec)\s+(?:-\S+\s+)*(?:\./)?{safe}(?![\w.-])|(?<![\w.])\.\s+(?:\./)?{safe}(?![\w.-])"
    return re.search(f"{start}|{interpreter}", text) is not None or (RUNS.search(text) is not None and name in text)


def flows(stmts: Sequence[tuple[int, str]]) -> list[tuple[int, str, str, int]]:
    """``(line, text, why, at)`` for each statement that runs a download, runs a downloaded file, sends the environment out or builds a remote shell."""
    out: list[tuple[int, str, str, int]] = []
    fetched, envs = _tainted(stmts, FETCHED), _tainted(stmts, ENVIRONMENT)
    files = {name for _, text in stmts if FETCHED.search(text) for rule in SAVED for name in rule.findall(text) if name != "-" and "://" not in name}
    for number, text in stmts:
        run, send = RUNS.search(text), SENDS.search(text)
        if run and any(_refers(text, n) for n in fetched):
            out.append((number, text, "runs what it downloaded", run.start()))
        elif any(_runs_file(text, f) for f in files):
            out.append((number, text, "runs a file it downloaded", 0))
        elif send and any(_refers(text, n) for n in envs):
            out.append((number, text, "sends the environment out", send.start()))
    whole = "\n".join(text for _, text in stmts)
    if SOCKET.search(whole) and (DUPLICATES.search(whole) or (SHELL_PATH.search(whole) and PROCESS.search(whole))):
        hit = next((s for s in stmts if DUPLICATES.search(s[1]) or SHELL_PATH.search(s[1])), stmts[0] if stmts else (0, ""))
        out.append((hit[0], hit[1], "opens a shell to another machine", 0))
    return out
