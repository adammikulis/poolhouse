"""Commands that print a key or token, dump the environment, send a variable out or unpack files that can run by themselves."""

from __future__ import annotations

import re

from poolhouse.guard import urlscan
from poolhouse.guard.harm import Finding
from poolhouse.guard.shellscan import has_expansion

__all__ = ["review"]

U, D = "unsure", "destructive"
SECRET_VAR = re.compile(r"(?i)\$\{?\w*(?:key|token|secret|passw|credential|auth|cookie|session)")
SECRET_NAME = re.compile(r"(?i)key|token|secret|passw|credential|auth|cookie|session")
SECRET_CLI: dict[str, re.Pattern[str]] = {
    "aws": re.compile(r"configure (?:get|list|export)|get-secret-value|get-parameters?\b|get-session-token|assume-role|get-login-password|decrypt"),
    "gcloud": re.compile(r"print-(?:access|identity)-token|secrets versions access|auth list|config-helper"),
    "az": re.compile(r"get-access-token|keyvault secret (?:show|download)"),
    "kubectl": re.compile(r"config view\b.*--(?:raw|flatten)|get secrets?|describe secrets?"),
    "oc": re.compile(r"config view\b.*--(?:raw|flatten)|get secrets?|describe secrets?|whoami -t"),
    "gh": re.compile(r"auth (?:token|status)"),
    "docker": re.compile(r"^config|credential"),
    "heroku": re.compile(r"auth:token|config"),
    "vercel": re.compile(r"env (?:pull|ls)"),
    "fly": re.compile(r"secrets|tokens|auth token"),
    "terraform": re.compile(r"state (?:show|pull)|output -raw"),
    "pip": re.compile(r"config (?:list|get|debug)"),
    "pip3": re.compile(r"config (?:list|get|debug)"),
}
OPENERS = frozenset({"open", "xdg-open", "gnome-open", "kde-open", "start", "explorer", "wslview", "gio"})
PROGRAM_SUFFIXES = (".command", ".app", ".pkg", ".dmg", ".sh", ".scpt", ".workflow", ".terminal", ".jar", ".py", ".bat", ".cmd", ".ps1", ".exe", ".msi")
PACKAGE_CONFIG = re.compile(r"config (?:list|ls)\b|token\b|config get \S*(?:auth|token|password)")
NETWORK = frozenset({"curl", "wget", "http", "https", "xh", "aria2c"})
LOOKUPS = frozenset({"ping", "host", "dig", "nslookup", "traceroute", "tracepath", "whois", "mtr"})
LABEL = 32
APPLY = frozenset({"apply", "am"})
PASSIVE = frozenset({"--check", "--stat", "--numstat", "--summary"})


def _environment(name: str, args: list[str]) -> list[Finding]:
    words = [a for a in args if not a.startswith("-") and "=" not in a]
    flags = [a for a in args if a.startswith("-")]
    if (name == "env" and not words and "-i" not in flags) or (name == "printenv" and not words):
        return [Finding(U, f"{name} prints every environment variable, and some hold keys")]
    if name == "printenv" and any(SECRET_NAME.search(w) for w in words):
        return [Finding(U, "prints an environment variable that holds a key or password")]
    dump = (name == "set" and not args) or (name in ("export", "declare", "typeset") and ("-p" in flags or "-x" in flags or not args))
    if dump or (name == "compgen" and "-v" in flags):
        return [Finding(U, f"{name} lists the shell's variables, and some hold keys")]
    bsd = name == "ps" and any((re.fullmatch(r"[A-Za-z]*e[A-Za-z]*", a) and not a.startswith("-")) or a in ("-E", "--environment") for a in args)
    return [Finding(U, "ps shows each process's environment, where keys are kept")] if bsd else []


def _cli(name: str, args: list[str]) -> list[Finding]:
    text = " ".join(a.lower() for a in args)
    rule = SECRET_CLI.get(name)
    if rule is None and name in ("npm", "yarn", "pnpm"):
        rule = PACKAGE_CONFIG
    return [Finding(D, f"{name} prints a key or token")] if rule and rule.search(text) else []


def _unpacks(name: str, args: list[str]) -> list[Finding]:
    flags = [a for a in args if a.startswith("-")]
    words = [a for a in args if not a.startswith("-")]
    tar = name in ("tar", "bsdtar", "gtar") and (any(re.fullmatch(r"-?[A-Za-z]*x[A-Za-z]*", a) for a in args[:2]) or "--extract" in flags or "--get" in flags)
    zip_ = name in ("unzip", "7z", "7za", "7zr", "unar", "cpio") and not set(flags) & {"-l", "-t", "-p", "-v", "-Z", "-lt"} and (name != "7z" or words[:1] == ["x"])
    patch = name == "git" and next((a for a in args if not a.startswith("-")), "") in APPLY and not set(args) & PASSIVE
    if tar or zip_ or patch:
        return [Finding(U, f"{name} unpacks or applies files that are not read, and any of them could run by itself")]
    return []


def _opens(name: str, args: list[str]) -> list[Finding]:
    if name in ("code", "cursor") and any(a.startswith("--install-extension") for a in args):
        return [Finding(U, f"{name} installs an extension, which runs code")]
    if name not in OPENERS:
        return []
    for word in (a for a in args if not a.startswith("-")):
        if urlscan.looks_like_url(word):
            why = urlscan.reason(word)
            if why:
                return [Finding(U, f"{name} opens an address that {why}")]
        elif word.lower().endswith(PROGRAM_SUFFIXES):
            return [Finding(U, f"{name} starts a program or script")]
    return []


def review(name: str, args: list[str]) -> list[Finding]:
    """Findings for one command: a dump of keys or the environment, a variable holding a secret, a
    long encoded host name, a variable sent over the network or an archive or patch unpacked."""
    found = _environment(name, args) + _cli(name, args) + _unpacks(name, args) + _opens(name, args)
    if any(SECRET_VAR.search(a) for a in args):
        found.append(Finding(U, "uses a variable that holds a key, token or password"))
    if name in NETWORK and any(has_expansion(a) for a in args):
        found.append(Finding(U, f"{name} sends or fetches something a variable or command builds"))
    if name in LOOKUPS and any(len(label) >= LABEL for a in args if not a.startswith("-") for label in a.split(".")):
        found.append(Finding(U, f"{name} looks up a host name with a long encoded part"))
    return found
