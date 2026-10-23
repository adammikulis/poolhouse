#!/usr/bin/env python3
"""Give agents their own GitHub identity: a GitHub App made from a manifest, its key kept in the OS keystore.

    scripts/agent-identity setup [--repo OWNER/NAME]               make the App, install it on the repository
    scripts/agent-identity token                                   print a one-hour installation token
    scripts/agent-identity check [--repo OWNER/NAME] [--online]    one line: whether the identity is ready

Setup may run in the selected installed holder for an authorized lead agent; GitHub consent remains a person's.
Token and check --online remain a person's, at a terminal. The repository is the one gh or the origin remote names. docs/github-protection.md, "Agent identity".
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.machinery
import importlib.util
import json
import re
import secrets
import signal
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

if importlib.util.find_spec("jwt") is None:
    sys.exit(f"agent-identity: PyJWT is not installed in {sys.prefix}; run `poolhouse runtime ensure`, "
             "which installs the github-app extra")

from poolhouse import (
    credentials,
    home,
    person,
    real_machine,
    runtime,
)
from poolhouse.command import Group, flag
from poolhouse.fleet import runtime_wheel, updates
from poolhouse.fleet.onboard import web
from poolhouse.lock import Busy, only_one
from poolhouse.log import say, warn
from poolhouse.net import github_release as remote
from poolhouse.workspace import github_release
from poolhouse.workspace.github_app import (
    Failed,
    Identity,
    app_jwt,
    decode,
    encode,
    mint,
    within,
)

ROOT = runtime_wheel.source_checkout() or Path(__file__).resolve().parents[3]

API = "https://api.github.com"
GITHUB = "https://github.com"
LOOPBACK = "127.0.0.1"
ITEM = "POOLHOUSE_AGENT_APP"
PERMISSIONS = {"contents": "write", "metadata": "read", "pull_requests": "write", "workflows": "write"}
RANK = {"read": 1, "write": 2, "admin": 3}
CREATE_WAIT_S = 900.0
INSTALL_WAIT_S = 900.0
POLL_S = 3.0
TOKEN_LIFE_S = 3700
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
CODE = re.compile(r"[A-Za-z0-9_-]{8,128}")
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
RESET = f"remove it with `poolhouse credentials unset {ITEM} --keychain` and run scripts/agent-identity setup again"
SUBMIT = "document.forms[0].submit()"
POLICY = ("default-src 'none'; base-uri 'none'; script-src 'sha256-"
          + base64.b64encode(hashlib.sha256(SUBMIT.encode()).digest()).decode() + "'")
OPEN: Callable[[str], bool] = webbrowser.open




class Gone(Failed):
    """GitHub answered 404."""




# -- the stored record --------------------------------------------------------------------





def load(check: Callable[[], None] | None = None) -> Identity | None:
    """The stored identity, or None when nothing is stored. Only the keychain is read."""
    if check is not None:
        check()
        held = open_stored()
        if held is None and ITEM in credentials._wrapped():
            raise Failed("the configured repository App could not be opened; its identity was preserved")
        return decode(held) if held is not None else None
    try:
        credentials._wrapped(strict=True)
    except credentials.WrappedCredentialError as exc:
        raise Failed(f"repository App: {exc}; the existing App identity was preserved") from None
    state = credentials.status(ITEM)
    if state.get("error"):
        raise Failed(f"{state['error']}; fix that and run the command again")
    if not state["present"]:
        return None
    if state["source"] != "keychain":
        raise Failed(f"{ITEM} is set in the {state['source']}; the identity is read from the keychain only, "
                     "so remove it there")
    held = open_stored()
    if held is None:
        raise Failed("the repository App changed while it was being opened; its existing configuration was preserved")
    return decode(str(held))


def open_stored() -> str | None:
    """Open the exact App item without conflating unreadable state with first setup."""
    try:
        return credentials._open(ITEM, strict=True)
    except credentials.WrappedCredentialError as exc:
        raise Failed(f"repository App: {exc}; the existing App identity was preserved") from None


def save(who: Identity, undo: str, check: Callable[[], None] | None = None, expected: Identity | None = None) -> None:
    """Store ``who`` in the keychain and read it back; ``undo`` is the next step when that fails."""
    if check is not None:
        check()
        if load(check) != expected:
            raise Failed("the configured repository App changed; its identity was preserved")
        check()
    try:
        credentials.set(ITEM, encode(who), keychain=True)
    except credentials.CredentialError as exc:
        raise Failed(f"{exc}; {undo}") from None
    if load(check) != who:
        raise Failed(f"the keychain did not keep the identity; {undo}")


# -- GitHub ---------------------------------------------------------------------------------

def api(method: str, path: str, *, hint: str, bearer: str = "", payload: dict | None = None) -> Any:
    """One request to the GitHub API through the project's client."""
    get = re.fullmatch(r"(?:/users/[A-Za-z0-9_.-]+|/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/installation|/installation/repositories\?per_page=100)", path)
    post = re.fullmatch(r"(?:/app-manifests/[A-Za-z0-9_-]{8,128}/conversions|/app/installations/[1-9][0-9]*/access_tokens)", path)
    if '..' in path or not ((method == "GET" and get) or (method == "POST" and post)):
        raise Failed("unsupported GitHub App setup operation")
    headers = {**HEADERS, "Content-Type": "application/json"}
    if bearer != "":
        headers["Authorization"] = f"Bearer {bearer}"
    try:
        return remote._fetch(API + path, method, json.dumps(payload).encode() if payload is not None else None, headers)
    except remote.Gone:
        raise Gone(f"GitHub answered 404; {hint}") from None
    except Failed as exc:
        raise Failed(f"{exc}; {hint}") from None


def find(path: str, *, hint: str, bearer: str) -> Any:
    """What GET ``path`` returns, or None when GitHub says there is nothing there."""
    try:
        return api("GET", path, hint=hint, bearer=bearer)
    except Gone:
        return None












def settings_page(owner: str, kind: object, tail: str) -> str:
    """A settings page of the account that owns the repository: a user's or an organization's."""
    base = f"{GITHUB}/organizations/{owner}/settings" if kind == "Organization" else f"{GITHUB}/settings"
    return f"{base}/{tail}"


def owner_kind(owner: str) -> str:
    """`User` or `Organization`: what the repository's owner is."""
    kind = (api("GET", f"/users/{owner}", hint="check the network and run it again") or {}).get("type")
    if kind not in ("User", "Organization"):
        raise Failed(f"GitHub does not know {owner} as a user or an organization; check the repository name")
    return kind


# -- the loopback listener ---------------------------------------------------------------------

def from_here(caller: str, hosts: list[str], port: int) -> bool:
    """Whether a request comes from this machine and was addressed to this listener."""
    return caller == LOOPBACK and hosts == [f"{LOOPBACK}:{port}"]


def state_ok(query: dict[str, list[str]], state: str) -> bool:
    """Whether the query carries exactly the state this run made."""
    given = query.get("state", [])
    return len(given) == 1 and secrets.compare_digest(given[0].encode(), state.encode())


def page(manifest: dict, action: str) -> bytes:
    """The page that posts the manifest to GitHub's App creation form."""
    return (f'<!doctype html><meta charset="utf-8"><title>Agent identity</title>'
            f'<form method="post" action="{html.escape(action)}">'
            f'<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest))}">'
            "<p>Taking you to GitHub to create the App your agents sign in as. If nothing happens, press "
            f'the button.</p><button type="submit">Continue to GitHub</button></form>'
            f"<script>{SUBMIT}</script>").encode()


DONE = b"<!doctype html><meta charset=utf-8><title>Agent identity</title><p>GitHub sent the App back. " \
       b"You can close this tab and return to Poolhouse.</p>"


class Listener:
    """A listener on this machine's loopback address that serves the manifest page once and takes GitHub's redirect once."""

    def __init__(self, state: str, render: Callable[[int], bytes]) -> None:
        self.state, self.code = state, ""
        self._served, self._lock, self._done = False, threading.Lock(), threading.Event()
        self._listener = web.Listener(self.answer, (LOOPBACK, 0))
        self.server = self._listener.httpd
        self.port = self._listener.port
        self.page = render(self.port)

    def __enter__(self) -> Listener:
        self._listener.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._listener.stop()

    def wait(self, seconds: float) -> str:
        """The code GitHub sent, or an empty string when none came within ``seconds``."""
        self._done.wait(seconds)
        return self.code

    @staticmethod
    def reply(status: int, body: bytes = b"", policy: str = "") -> web.Reply:
        headers = {"Referrer-Policy": "no-referrer"}
        if policy:
            headers["Content-Security-Policy"] = policy
        return web.Reply(status, body, headers, "text/html; charset=utf-8")

    def answer(self, h: web.Call) -> web.Reply:
        """Serve one request: the start page once, the redirect once, a refusal for everything else."""
        if h.method != "GET":
            return self.reply(405)
        url = urllib.parse.urlsplit(h.path)
        query = urllib.parse.parse_qs(url.query)
        if not from_here(h.client, h.headers.get_all("Host") or [], self.port):
            return self.reply(403)
        if url.path not in ("/start", "/callback") or not state_ok(query, self.state):
            return self.reply(404)
        with self._lock:
            if self._done.is_set() or (url.path == "/start" and self._served):
                return self.reply(410)
            if url.path == "/start":
                self._served = True
                return self.reply(200, self.page, POLICY)
            code = query.get("code", [])
            if len(code) != 1 or not CODE.fullmatch(code[0]):
                return self.reply(400)
            self.code = code[0]
            self._done.set()
            return self.reply(200, DONE)


# -- setup ---------------------------------------------------------------------------------

def manifest(repo: str, port: int) -> dict:
    """The App GitHub is asked to create: this repository's, private to its owner, no webhook."""
    return {"name": f"{repo.split('/')[1]}-agents"[:34], "url": f"{GITHUB}/{repo}",
            "description": f"Agents working on {repo}.", "public": False,
            "redirect_url": f"http://{LOOPBACK}:{port}/callback", "default_events": [],
            "request_oauth_on_install": False, "default_permissions": dict(PERMISSIONS)}


def show(url: str, what: str) -> None:
    say(what)
    if not OPEN(url):
        say(f"Open this address in your browser: {url}")


def exchange(code: str, repo: str, kind: str, check: Callable[[], None] | None = None) -> Identity:
    """Turn GitHub's one-time code into the App and its key, and keep the key."""
    if check is not None:
        check()
    owner = repo.split("/")[0]
    again = "and run scripts/agent-identity setup again"
    gone = f"delete any App it made at {settings_page(owner, kind, 'apps')} {again}"
    made = api("POST", f"/app-manifests/{urllib.parse.quote(code, safe='')}/conversions", payload={},
               hint=f"GitHub would not give the App for this code; {gone}")
    row = made if isinstance(made, dict) else {}
    slug, pem = str(row.get("slug", "")), row.get("pem")
    who = Identity(repo, row.get("id"), str(row.get("client_id") or ""), slug,
                   pem if isinstance(pem, str) else "")
    undo = f"delete the App {slug} at {settings_page(owner, kind, f'apps/{slug}')} {again}"
    maker = row.get("owner") if isinstance(row.get("owner"), dict) else {}
    if not (type(who.app_id) is int and SLUG.fullmatch(slug) and who.key.startswith("-----BEGIN")):
        raise Failed(f"GitHub's answer was not an App; {gone}")
    if str(maker.get("login", "")).lower() != owner.lower():
        raise Failed(f"the App belongs to {maker.get('login')}, not {owner}; {undo}")
    if not within(row.get("permissions"), PERMISSIONS):
        raise Failed(f"the App was made with permissions that were not asked for; {undo}")
    save(who, undo, check)
    return who


def new_identity(repo: str, check: Callable[[], None] | None = None) -> Identity:
    """Have the person create the App in the browser and return it, its key stored."""
    if check is not None:
        check()
    owner = repo.split("/")[0]
    kind, state = owner_kind(owner), secrets.token_urlsafe(32)
    action = f"{settings_page(owner, kind, 'apps/new')}?state={state}"
    with Listener(state, lambda port: page(manifest(repo, port), action)) as listening:
        show(f"http://{LOOPBACK}:{listening.port}/start?state={state}",
             "Opening GitHub. On the page it shows, press the green Create GitHub App button.")
        code = listening.wait(CREATE_WAIT_S)
    if not code:
        raise Failed("GitHub did not send the App back in time; run scripts/agent-identity setup again")
    return exchange(code, repo, kind, check)


def verify_installation(found: object, who: Identity) -> dict:
    """The installation record when it is this App's, on this account, for selected repositories, and no wider."""
    row = found if isinstance(found, dict) else {}
    account = row.get("account") if isinstance(row.get("account"), dict) else {}
    owner = who.repo.split("/")[0]
    where = settings_page(owner, account.get("type"), f"installations/{row.get('id')}")
    if type(row.get("id")) is not int or row.get("app_id") != who.app_id:
        raise Failed(f"the installation is not this App's; open {where} or run scripts/agent-identity setup again")
    if str(account.get("login", "")).lower() != owner.lower():
        raise Failed(f"the installation is on {account.get('login')}, not {owner}; uninstall it at {where}")
    if not within(row.get("permissions"), PERMISSIONS):
        raise Failed(f"the installation holds permissions that were not asked for; uninstall it at {where}")
    if row.get("repository_selection") != "selected":
        raise Failed(f"the installation covers every repository; open {where}, choose only {who.repo}, "
                     "then run scripts/agent-identity setup again")
    return row


def only_this_repository(who: Identity) -> None:
    """Refuse an installation that reaches any repository besides this one."""
    grant = mint(who, {"metadata": "read"}, None)
    seen = api("GET", "/installation/repositories?per_page=100", bearer=grant["token"],
               hint="run scripts/agent-identity setup again")
    rows = seen.get("repositories") if isinstance(seen, dict) else None
    names = sorted(str(r.get("full_name", "")).lower() for r in rows or [] if isinstance(r, dict))
    if names != [who.repo.lower()]:
        raise Failed(f"the installation also reaches {', '.join(n for n in names if n != who.repo.lower())}; "
                     f"open {GITHUB}/settings/installations, choose only {who.repo}, then run "
                     "scripts/agent-identity setup again")


def install(who: Identity, check: Callable[[], None] | None = None) -> Identity:
    """Have the person install the App on the repository, wait for it, and record the installation."""
    if check is not None:
        check()
    previous = who
    url = f"{GITHUB}/apps/{who.slug}/installations/new"
    show(url, f"Now install it: on the GitHub page choose Only select repositories, pick {who.repo}, "
              "then press Install.")
    if check is not None:
        check()
    deadline = time.monotonic() + INSTALL_WAIT_S
    while (found := find(f"/repos/{who.repo}/installation", bearer=app_jwt(who),
                         hint="run scripts/agent-identity setup again")) is None:
        if time.monotonic() >= deadline:
            raise Failed(f"the App is not installed on {who.repo} yet; open {url}, install it, then run "
                         "scripts/agent-identity setup again")
        time.sleep(POLL_S)
        if check is not None:
            check()
    if check is not None:
        check()
    who = replace(who, installation_id=verify_installation(found, who)["id"])
    only_this_repository(who)
    save(who, "run scripts/agent-identity setup again", check, previous)
    return who


def ready(who: Identity) -> str:
    return f"App {who.slug} (id {who.app_id}), installation {who.installation_id} on {who.repo}"


def valid_repo(text: str) -> str:
    if not REPO.fullmatch(text) or set(text.split("/")) & {".", ".."}:
        raise Failed("name the repository as OWNER/NAME with --repo")
    return text


def default_repo() -> str:
    """OWNER/NAME of the repository gh is pointed at, else of the origin remote."""
    loader = importlib.machinery.SourceFileLoader("github_protection_default", str(ROOT / "scripts" / "github-protection"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module.default_repo()


@dataclass(frozen=True)
class Preparation:
    """One eligible actor and selected installed holder bound to a fixed primary source."""

    actor: str
    selected: runtime.Runtime
    source: github_release.Source
    deadline: float

    @classmethod
    def capture(cls, named: str | None) -> Preparation:
        actor = github_release.authorize("prepare the repository GitHub App")
        selected = runtime.available()
        if selected is None or not Path(__file__).resolve().is_relative_to(selected.prefix.resolve()):
            raise Failed("App setup uses the selected installed credential holder")
        repo = updates.REPO
        if named and named != repo:
            raise Failed("App setup uses only the installed repository policy")
        checkout = runtime_wheel.source_checkout()
        if checkout is None:
            raise Failed("the installed repository source is unavailable")
        source = github_release.source(checkout, repo)
        return cls(actor.name, selected, source, time.monotonic() + CREATE_WAIT_S + INSTALL_WAIT_S)

    def check(self) -> None:
        actor = github_release.authorize("continue repository GitHub App setup")
        if (actor.name != self.actor or runtime.available() != self.selected
                or not Path(__file__).resolve().is_relative_to(self.selected.prefix.resolve())
                or github_release.source(self.source.checkout, updates.REPO) != self.source
                or time.monotonic() >= self.deadline):
            raise Failed("App setup authorization changed or expired; the configured identity was preserved")


def setup(args: argparse.Namespace) -> int:
    """Make the App, install it on the repository and record both."""
    prepared = getattr(args, "preparation", None)
    if person.marked() and prepared is None:
        prepared = Preparation.capture(args.repo)
    check = prepared.check if prepared is not None else None
    repo = prepared.source.repo if prepared is not None else valid_repo(args.repo or default_repo())
    with only_one(home.state("github-app-setup.lock"), wait=False, note="GitHub App setup"):
        who = load(check)
        if who and who.repo.lower() != repo.lower():
            raise Failed("an identity for another repository is stored; it was preserved" if check is not None
                         else f"an identity for {who.repo} is stored, not {repo}; {RESET}")
        if who and who.installation_id:
            say(f"agent identity: already set up: {ready(who)}")
            return 0
        who = install(who or new_identity(repo, check), check)
        say(f"agent identity: ready: {ready(who)}")
    return 0


# -- token and check -----------------------------------------------------------------------

def token(_args: argparse.Namespace) -> int:
    """Print a one-hour installation token for this repository, and nothing else on stdout."""
    who = load()
    if who is None or who.installation_id is None:
        raise Failed("no identity is ready; run scripts/agent-identity setup")
    say(mint(who, PERMISSIONS, who.repo)["token"])
    return 0


def describe(args: argparse.Namespace) -> str:
    """The ready line for the stored identity; ``Failed`` says what is not ready and the next step."""
    who = load()
    if who is None:
        raise Failed("not set up; run scripts/agent-identity setup")
    if args.repo and who.repo.lower() != args.repo.lower():
        raise Failed(f"stored for {who.repo}, not {args.repo}; {RESET}")
    if who.installation_id is None:
        raise Failed(f"the App {who.slug} is made but not installed on {who.repo}; run "
                     "scripts/agent-identity setup to finish")
    if args.online:
        found = verify_installation(api("GET", f"/repos/{who.repo}/installation", bearer=app_jwt(who),
                                        hint=f"the App may be deleted or uninstalled; {RESET}"), who)
        if found["id"] != who.installation_id:
            raise Failed(f"the installation on {who.repo} is {found['id']}, not the stored {who.installation_id}; "
                         f"{RESET}")
    return f"ready: {ready(who)}"


def check(args: argparse.Namespace) -> int:
    """Print one line saying whether the identity is ready."""
    try:
        line, code = describe(args), 0
    except Failed as exc:
        line, code = str(exc), 1
    say(f"agent identity: {line}")
    return code


def publication(args: argparse.Namespace) -> int:
    """Push the exact reviewed development tip through the installed repository App."""

    previous = signal.getsignal(signal.SIGTERM)

    def cancel(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, cancel)
    try:
        operation = github_release.push_development if args.cmd == "push-development" else github_release.publish_release
        result = operation(Path(args.source).resolve())
    except real_machine.Refused as exc:
        raise Failed(str(exc)) from None
    finally:
        signal.signal(signal.SIGTERM, previous)
    say(json.dumps(result, sort_keys=True))
    return 0


# -- entry ---------------------------------------------------------------------------------

def guard(args: argparse.Namespace, terminal: tuple[bool, bool] | None) -> None:
    """Authenticate delegated setup; keep token and online checks at the person floor."""
    tty = terminal or (person.is_terminal(sys.stdin), person.is_terminal(sys.stdout))
    if args.cmd == "token":
        tty = (tty[0], tty[1] or person.is_terminal(sys.stderr))
    if args.cmd == "setup" and person.marked():
        args.preparation = Preparation.capture(args.repo)
        return
    if args.cmd in ("setup", "token") or getattr(args, "online", False):
        person.require_person(f"scripts/agent-identity {args.cmd}", tty)


def command(argv: list[str] | None = None, terminal: tuple[bool, bool] | None = None) -> int:
    group = Group("agent-identity", __doc__.splitlines()[0])
    for name, fn in (("setup", setup), ("token", token), ("check", check), ("push-development", publication), ("publish-release", publication)):
        options = []
        if name in {"push-development", "publish-release"}:
            options.append(flag("--source", default=str(ROOT), help="primary development checkout"))
        elif name != "token":
            options.append(flag("--repo", help="OWNER/NAME" + ("; defaults to the repository gh or origin names"
                                                              if name == "setup" else "")))
        if name == "check":
            options.append(flag("--online", action="store_true", help="also ask GitHub (a person's, at a terminal)"))
        group.add(name, fn, help=fn.__doc__, options=options)
    args = group.parser().parse_args(argv)
    try:
        guard(args, terminal)
        return args.run(args)
    except (Failed, person.HumanRequired, credentials.CredentialError, real_machine.Refused, Busy) as exc:
        warn(f"agent-identity: {exc}")
        if "the owned Git transport cleanup is unconfirmed" in getattr(exc, "__notes__", []):
            warn("agent-identity: the owned Git transport cleanup is unconfirmed")
        return 1
    except KeyboardInterrupt as exc:
        if args.cmd in {"push-development", "publish-release"}:
            warn("agent-identity: publication stopped")
        else:
            warn("agent-identity: stopped; run scripts/agent-identity setup again to continue")
        if "the owned Git transport cleanup is unconfirmed" in getattr(exc, "__notes__", []):
            warn("agent-identity: the owned Git transport cleanup is unconfirmed")
        return 130


if __name__ == "__main__":
    sys.exit(command())
