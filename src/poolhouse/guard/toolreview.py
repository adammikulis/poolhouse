"""What the arguments of a tool call say about credential files, addresses that carry data out and files that run by themselves.

`findings` reads the path, glob and address arguments of any tool and the files a write tool would change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from poolhouse import secretpaths
from poolhouse.guard import argscan, autoexec, execchain, fetchexec, urlscan
from poolhouse.guard.harm import Finding, resolve
from poolhouse.guard.verbs import words_of

__all__ = ["findings"]

U, D = "unsure", "destructive"
GLOBS = frozenset({"glob", "globs", "include", "includes"})
READ_KEYS = frozenset({"path", "paths", "file", "files", "filepath", "filename", "file_path", "dir", "directory", "src", "source", "folder",
                       "root", "from", "notebook_path"}) | GLOBS
URL_KEYS = frozenset({"url", "uri", "href", "link", "urls"})
LISTING = frozenset({"Glob", "LS", "list_dir"})
SEARCHES = frozenset({"grep", "search", "find"})
SHOWN = 80


def _values(value: Any) -> list[str]:
    return [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str) and v.strip() and len(v) < 1024]


def _read(name: str, key: str, value: str, roots: Sequence[str]) -> list[Finding]:
    base = roots[0] if roots else ""
    hit = secretpaths.glob_hit(value, base) if key in GLOBS else secretpaths.holds_secrets(value, base)
    if hit:
        return [execchain.closed(hit, roots)]
    full = resolve(secretpaths.expanded(value), roots)
    if key not in GLOBS and set(words_of(name)) & SEARCHES and secretpaths.inside_home(full) and secretpaths.beyond(full, roots):
        return [Finding(D, "searches the whole home folder, where credentials are kept", forbidden=True)]
    return []


def _address(value: str, roots: Sequence[str]) -> list[Finding]:
    shown = fetchexec.ascii_line(value, SHOWN)
    path = urlscan.file_path(value)
    if path:
        hit = secretpaths.holds_secrets(path, roots[0] if roots else "")
        return [execchain.closed(hit, roots)] if hit else [Finding(U, f"fetches a file on this machine ({shown})")]
    why = urlscan.reason(value) if urlscan.looks_like_url(value) else ""
    return [Finding(U, f"fetches an address that {why} ({shown})")] if why else []


def findings(name: str, args: Mapping[str, Any], roots: Sequence[str] = ()) -> list[Finding]:
    """The findings for the paths, patterns and addresses in ``args`` and for the files a write would change."""
    found = autoexec.write_findings(name, args, roots)
    writing = bool(autoexec.written(name, args))
    for key, value in argscan.walk(args):
        if key in URL_KEYS:
            for text in _values(value):
                found += _address(text, roots)
        elif key in READ_KEYS and name not in LISTING and not writing:
            for text in _values(value):
                found += _read(name, key, text, roots)
    return found
