"""The one list of files and folders that hold credentials, and how a path or a pattern is matched to it.

`path_hit` reads a path (``~`` and ``$HOME`` resolved, links followed when the path exists) and
`glob_hit` reads a pattern the shell would expand; each returns the entry it matched or nothing.
`policy.SENSITIVE_PATH` and `harm.protected` are built from the tables here.
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home

__all__ = ["ABSOLUTE", "HOME_DIRS", "NAMES", "STATE_NAMES", "SUFFIXES", "TOO_LARGE", "WEAK", "Hit", "exists", "expanded", "find_hit", "glob_hit",
           "holds_secrets", "inside_home", "is_dir", "path_hit", "sensitive_path", "tree_hit"]

HOME_DIRS = (".ssh", ".aws", ".gnupg", ".kube", ".azure", ".password-store", ".docker", ".config/gh", ".config/gcloud",
             ".config/op", ".config/doctl", ".config/netlify", ".config/huggingface", ".terraform.d", ".vault", ".fly", ".vercel", ".huggingface",
             ".cache/huggingface/token", ".codex/auth.json", ".m2/settings.xml", ".local/share/keyrings", "Library/Keychains", "Library/Cookies",
             "Library/Safari", "Library/Application Support/Google/Chrome", "Library/Application Support/Firefox", ".mozilla",
             ".config/google-chrome", ".config/chromium", ".config/BraveSoftware")
"""Folders of credentials, matched as a run of names anywhere in a path."""
NAMES = frozenset({
    ".env", ".netrc", "_netrc", ".npmrc", ".pypirc", ".pgpass", ".my.cnf", ".git-credentials", ".htpasswd", ".vault-token",
    ".dockercfg", ".s3cfg", ".credentials.json", ".claude.json", ".mylogin.cnf", "login data", "cookies.sqlite", "key4.db", "logins.json", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_xmss", "authorized_keys", "known_hosts",
    "credentials", "credentials.json", "credentials.toml", "secrets.json", "secrets.yml", "secrets.yaml", "service-account.json",
    "master.key", "kubeconfig", ".bash_history", ".zsh_history", ".python_history",
})
"""File names that hold credentials wherever they are."""
SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".kdbx", ".tfvars")
STATE_NAMES = ("node", "keystore", "pool.key", "server-keys.json", "credentials.toml", "person", "authority.json", "harness")
"""What the state root holds that is a key, a credential or a launcher's session."""
ABSOLUTE = ("/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/etc/sudoers.d", "/etc/master.passwd", "/etc/ssh", "/etc/kubernetes",
            "/private/etc/sudoers", "/private/etc/ssh", "/private/var/db/dslocal", "/var/run/secrets", "/run/secrets")
PLACEHOLDER_ENV = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist", ".env.defaults", ".env.schema"})
PROC = re.compile(r"/proc/(?:self|\d+)/(?:environ|mem)")
KEY_PREFIX = re.compile(r"id_(?:rsa|dsa|ecdsa|ed25519|xmss)(?:_sk)?[-_.\w]*")
HOME_ENV = re.compile(r"^\$(?:\{HOME\}|HOME)(?=/|$)")
WILD_ABSOLUTE = ("/proc/self/environ", "/proc/1/environ", "/etc/shadow", "/etc/sudoers", "/etc/master.passwd")
MAX_ALTERNATIVES = 64
MAX_WALK = 20_000
WALK_SECONDS = 0.3
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache", ".ruff_cache", ".pytest_cache", "site-packages"})
TOO_LARGE = "too many files to check"
WILD = re.compile(r"[*?\[]")
GUESSES = (*NAMES, ".env.local", ".env.production", ".env.development", "id_rsa_work", *(f"x{s}" for s in SUFFIXES),
           *(entry.split("/")[0] for entry in HOME_DIRS), ".config")


WEAK = frozenset(n for n in NAMES if not n.startswith((".", "id_")) and n not in ("authorized_keys", "known_hosts"))
"""Names that are ordinary words, so a bare word spelled like one is not taken for a credential file that does not exist."""


@dataclass(frozen=True, slots=True)
class Hit:
    """The credential entry a path matched and the absolute ``path`` it was found at."""

    rule: str
    path: Path


def is_dir(path: Path) -> bool:
    """Whether ``path`` is a folder; a name the file system refuses is not."""
    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def exists(path: Path) -> bool:
    """Whether ``path`` exists; a name the file system refuses does not."""
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def expanded(text: str) -> str:
    """``text`` with a leading ``$HOME`` or ``${HOME}`` written out."""
    return HOME_ENV.sub(str(home.user_home()), text.strip())


def _normal(text: str, base: str) -> Path | None:
    text = expanded(text)
    if not text or "\0" in text or len(text) > 4096:
        return None
    path = home.expand(text)
    return Path(os.path.normpath(path if path.is_absolute() else (Path(base) if base else Path.cwd()) / path))


def _named(name: str) -> str:
    low = name.lower()
    if low in NAMES:
        return low
    if low.startswith(".env.") and low not in PLACEHOLDER_ENV:
        return ".env"
    if KEY_PREFIX.fullmatch(low) and not low.endswith(".pub"):
        return "id_*"
    return next((s for s in SUFFIXES if low.endswith(s) and len(low) > len(s)), "")


def _run(parts: tuple[str, ...], entry: str) -> bool:
    want = entry.lower().split("/")
    return any(parts[at:at + len(want)] == tuple(want) for at in range(len(parts) - len(want) + 1))


def _state(path: Path) -> str:
    for root in {home.home(), home.user_home() / home.DEFAULT_NAME}:
        if path == root or root in path.parents:
            first = path.relative_to(root).parts[:1]
            if first and first[0] in STATE_NAMES:
                return f"{root.name}/{first[0]}"
    return ""


def _rule(path: Path, directory: bool = False) -> str:
    low = str(path).lower()
    parts = tuple(p.lower() for p in path.parts)
    for entry in HOME_DIRS:
        if _run(parts, entry):
            return entry
    if any(low == a or low.startswith(a + "/") for a in ABSOLUTE) or PROC.fullmatch(low):
        return low
    return _state(path) or (_named(path.name) if path.name and not directory else "")


def path_hit(text: str, base: str = "") -> Hit | None:
    """The credential entry ``text`` names, by its own spelling or through a link that exists."""
    path = _normal(text, base)
    if path is None:
        return None
    try:
        real = Path(os.path.realpath(path))
    except (OSError, ValueError):
        real = path
    if real != path and (found := _rule(real, is_dir(real))):
        return Hit(found, real)
    rule = _rule(path, is_dir(path))
    return Hit(rule, path) if rule else None


def _braces(text: str) -> list[str]:
    match = re.search(r"\{([^{}]*,[^{}]*)\}", text)
    if not match:
        return [text]
    out: list[str] = []
    for option in match.group(1).split(","):
        out += _braces(text[:match.start()] + option + text[match.end():])
        if len(out) > MAX_ALTERNATIVES:
            return out[:MAX_ALTERNATIVES]
    return out


def _may_match(part: str) -> str:
    if not WILD.search(part) or not re.search(r"[^*?\[\]!^-]", part):
        return ""
    dotted = part.startswith(".")
    return next((name for name in GUESSES if (dotted or not name.startswith(".")) and fnmatch.fnmatchcase(name, part)), "")


def glob_hit(text: str, base: str = "") -> Hit | None:
    """The credential entry a pattern could expand to; a wildcard that matches no credential name is nothing."""
    for alt in _braces(expanded(text)):
        if alt.startswith("/") and any(fnmatch.fnmatchcase(g, alt) for g in WILD_ABSOLUTE):
            return Hit(alt, Path(alt))
        if hit := path_hit(alt, base):
            return hit
        parts = [p for p in alt.split("/") if p]
        for part in parts:
            if found := _may_match(part):
                return Hit(found, Path(alt))
    return None


def holds_secrets(text: str, base: str = "") -> Hit | None:
    """`path_hit` for a word that names a path, `glob_hit` for one that is a pattern."""
    return glob_hit(text, base) if WILD.search(text) or "{" in text else path_hit(text, base)


def sensitive_path() -> re.Pattern[str]:
    """A pattern for text that names a credential folder or file, the form `ToolPolicyRail` reads arguments with."""
    bare = {n for n in NAMES if n.startswith((".", "id_")) or n in ("authorized_keys", "known_hosts")}
    folders = "|".join(re.escape(e) for e in HOME_DIRS)
    always = "|".join(re.escape(n) for n in sorted(bare))
    inside = "|".join(re.escape(n) for n in sorted(NAMES - bare))
    tail = r"(?:$|[\s\"'/\\])"
    return re.compile(
        rf"(?i)(?:^|[\s\"'=:@/\\~])(?:{folders}|{always}|\.env(?:\.\w+)?|id_(?:rsa|dsa|ecdsa|ed25519|xmss)\w*){tail}"
        rf"|(?:^|[/\\~])(?:{inside}){tail}"
        r"|/etc/(?:passwd|shadow|gshadow|sudoers)|/proc/(?:self|\d+)/environ|\bKeychains?\b")


def find_hit(pattern: str) -> str:
    """The credential name a ``find -name`` pattern could match (``*`` matches dot files there), or nothing."""
    return next((name for name in GUESSES if fnmatch.fnmatchcase(name, pattern)), "")


def inside_home(path: Path) -> bool:
    """Whether ``path`` is the home folder, a folder above it or the root of the file system."""
    user = home.user_home()
    return path == user or path in user.parents


def tree_hit(path: Path) -> Hit | None:
    """The first credential file or folder under the folder ``path``, `TOO_LARGE` when the folder is too big
    to look through, nothing when it holds none; links are not followed and build caches are skipped."""
    stack, seen, stop = [path], 0, time.monotonic() + WALK_SECONDS
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    seen += 1
                    if seen > MAX_WALK or (seen % 256 == 0 and time.monotonic() > stop):
                        return Hit(TOO_LARGE, path)
                    here = Path(entry.path)
                    directory = entry.is_dir(follow_symlinks=False)
                    if rule := _rule(here, directory):
                        return Hit(rule, here)
                    if directory and entry.name not in SKIP_DIRS:
                        stack.append(here)
        except OSError:
            continue
    return None
