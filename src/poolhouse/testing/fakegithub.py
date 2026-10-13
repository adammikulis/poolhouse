"""A GitHub on a loopback socket that answers the App manifest flow and the installation API, and a browser
stand-in that drives a loopback listener the way a person's browser would.

``FakeGitHub`` signs nothing and trusts nothing: it verifies each App JWT against the key it made, hands out
one-time manifest codes and installation tokens, and has a knob for each way the real thing can go wrong.
``FakeBrowser`` replaces `webbrowser.open`. Neither reaches a public endpoint.
"""

from __future__ import annotations

import html
import http.client
import itertools
import json
import re
import secrets
import threading
import urllib.parse
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from poolhouse.http import Server
from poolhouse.testing.fakes import http_handler

OWNER, NAME = "ownerlogin", "poolhouse-test"
REPO = f"{OWNER}/{NAME}"
ASKED = {"contents": "write", "metadata": "read", "pull_requests": "write", "workflows": "write"}
__all__ = ["ASKED", "KEYS", "NAME", "OWNER", "REPO", "FakeBrowser", "FakeGitHub", "loopback_get", "pem"]


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                             serialization.NoEncryption()).decode()


KEYS = [new_key(), new_key()]


class FakeGitHub:
    """The API host and the pages of github.com the flow touches, with a knob for each way it can go wrong."""

    def __init__(self, repo: str = REPO, kind: str = "User") -> None:
        self.repo, self.kind = repo, kind
        self.owner = repo.split("/")[0]
        self.apps: dict[int, dict] = {}
        self.codes: dict[str, int] = {}
        self.used: set[str] = set()
        self.installations: dict[int, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.log: list[dict] = []
        self.manifests: list[dict] = []
        self.exchanges = 0
        self.app_owner = self.owner
        self.app_permissions = dict(ASKED)
        self.selection = "selected"
        self.other_repos: list[str] = []
        self.token_repos: list[str] | None = None
        self.token_permissions: dict | None = None
        self.token_lifetime = timedelta(hours=1)
        self.keys = itertools.cycle(KEYS)
        self._ids = itertools.count(100)
        post = type("_Routed", (http_handler(self.route),), {"do_POST": lambda request: self.route(request)})
        self.server = Server(("127.0.0.1", 0), post)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- the pages a person's browser reaches ----------------------------------------------

    def create_app(self, manifest: dict) -> str:
        """The App GitHub's create page makes from ``manifest``; the one-time code its redirect carries."""
        self.manifests.append(manifest)
        app_id, key = next(self._ids), next(self.keys)
        self.apps[app_id] = {"id": app_id, "slug": manifest["name"], "client_id": f"Iv1.{secrets.token_hex(8)}",
                             "key": key, "permissions": self.app_permissions, "secret": secrets.token_hex(12)}
        code = secrets.token_hex(20)
        self.codes[code] = app_id
        return code

    def install(self, app_id: int | None = None) -> int:
        """The person installs the App: on this repository, and on what the knobs say."""
        app_id = app_id or max(self.apps)
        number = next(self._ids)
        self.installations[number] = {
            "id": number, "app_id": app_id, "account": {"login": self.owner, "type": self.kind},
            "repository_selection": self.selection, "permissions": self.apps[app_id]["permissions"],
            "repos": [self.repo, *self.other_repos]}
        return number

    # -- the API ------------------------------------------------------------------------------

    def app_of(self, bearer: str) -> dict | None:
        """The App whose key signed this JWT, when the JWT is sound."""
        try:
            claims = jwt.decode(bearer, options={"verify_signature": False})
            app = next(a for a in self.apps.values() if a["client_id"] == claims.get("iss"))
            jwt.decode(bearer, app["key"].public_key(), algorithms=["RS256"], issuer=app["client_id"],
                       options={"require": ["exp", "iat", "iss"]})
        except (jwt.PyJWTError, StopIteration, TypeError):
            return None
        return app if claims["exp"] - claims["iat"] <= 600 else None

    def handle(self, method: str, path: str, bearer: str, body: dict) -> tuple[int, dict]:
        self.log.append({"method": method, "path": path, "auth": "Bearer" if bearer else "", "body": body})
        route = urllib.parse.urlsplit(path).path
        if found := re.fullmatch(r"/app-manifests/([^/]+)/conversions", route):
            return self.convert(found[1])
        if found := re.fullmatch(r"/users/([^/]+)", route):
            return 200, {"login": found[1], "type": self.kind}
        if route == "/installation/repositories":
            held = self.tokens.get(bearer)
            return (200, {"repositories": [{"full_name": r} for r in held["repos"]]}) if held else (401, {})
        app = self.app_of(bearer)
        if app is None:
            return 401, {"message": "A JSON web token could not be decoded"}
        if found := re.fullmatch(r"/repos/([^/]+/[^/]+)/installation", route):
            hit = [i for i in self.installations.values() if i["app_id"] == app["id"] and found[1] in i["repos"]]
            return (200, {k: v for k, v in hit[0].items() if k != "repos"}) if hit else (404, {})
        if found := re.fullmatch(r"/app/installations/(\d+)/access_tokens", route):
            return self.mint(int(found[1]), app, body)
        return 404, {}

    def convert(self, code: str) -> tuple[int, dict]:
        self.exchanges += 1
        if code not in self.codes or code in self.used:
            return 404, {"message": "Not Found"}
        self.used.add(code)
        app = self.apps[self.codes[code]]
        return 201, {"id": app["id"], "slug": app["slug"], "client_id": app["client_id"], "pem": pem(app["key"]),
                     "webhook_secret": app["secret"], "client_secret": app["secret"],
                     "owner": {"login": self.app_owner}, "permissions": app["permissions"], "events": []}

    def mint(self, number: int, app: dict, body: dict) -> tuple[int, dict]:
        inst = self.installations.get(number)
        if inst is None or inst["app_id"] != app["id"]:
            return 404, {"message": "Not Found"}
        named = body.get("repositories")
        repos = self.token_repos if self.token_repos is not None else (
            [r for r in inst["repos"] if r.split("/")[1] in named] if named else inst["repos"])
        permissions = self.token_permissions or {k: v for k, v in inst["permissions"].items()
                                                 if k in (body.get("permissions") or inst["permissions"])}
        token = "ghs_" + secrets.token_hex(16)
        self.tokens[token] = {"repos": repos}
        ends = (datetime.now(UTC) + self.token_lifetime).strftime("%Y-%m-%dT%H:%M:%SZ")
        reply = {"token": token, "expires_at": ends, "permissions": permissions,
                 "repository_selection": inst["repository_selection"]}
        if named:
            reply["repositories"] = [{"full_name": r} for r in repos]
        return 201, reply

    def route(self, request: BaseHTTPRequestHandler) -> None:
        """Answer one request from ``handle``."""
        size = int(request.headers.get("Content-Length") or 0)
        body = json.loads(request.rfile.read(size) or b"{}") if size else {}
        auth = request.headers.get("Authorization", "")
        status, reply = self.handle(request.command, request.path, auth.removeprefix("Bearer "), body)
        data = json.dumps(reply).encode()
        request.send_response(status)
        request.send_header("Content-Type", "application/json")
        request.send_header("Content-Length", str(len(data)))
        request.end_headers()
        request.wfile.write(data)


def loopback_get(url: str, host: str | None = None, method: str = "GET") -> tuple[int, bytes]:
    """One request to a loopback address, with the Host header a test chooses."""
    parts = urllib.parse.urlsplit(url)
    link = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        link.putrequest(method, f"{parts.path}?{parts.query}" if parts.query else parts.path, skip_host=True)
        link.putheader("Host", host or f"{parts.hostname}:{parts.port}")
        link.endheaders()
        answer = link.getresponse()
        return answer.status, answer.read()
    finally:
        link.close()


class FakeBrowser:
    """Stands in for `webbrowser.open`: shows the start page to GitHub, creates the App, comes back to the
    listener with the code, and installs the App when it is sent to the install page."""

    def __init__(self, gh: FakeGitHub, *, install: bool = True, sends_first: tuple[str, ...] = ()) -> None:
        self.gh, self.installs, self.sends_first = gh, install, sends_first
        self.opened: list[str] = []
        self.pages: list[tuple[int, bytes]] = []
        self.sent: list[tuple[int, bytes]] = []
        self.callback = ""
        self.threads: list[threading.Thread] = []

    def __call__(self, url: str) -> bool:
        self.opened.append(url)
        self.threads.append(threading.Thread(target=self.go, args=(url,), daemon=True))
        self.threads[-1].start()
        return True

    def join(self) -> None:
        for thread in self.threads:
            thread.join(10)

    def go(self, url: str) -> None:
        if "/installations/new" in url:
            if self.installs:
                self.gh.install()
            return
        status, page = loopback_get(url)
        self.pages.append((status, page))
        text = page.decode()
        manifest = json.loads(html.unescape(re.search(r'name="manifest" value="([^"]*)"', text)[1]))
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(
            re.search(r'action="([^"]*)"', text)[1])).query)["state"][0]
        code = self.gh.create_app(manifest)
        base = manifest["redirect_url"]
        for extra in self.sends_first:
            self.sent.append(loopback_get(f"{base}{extra.format(code=code, state=state)}"))
        self.callback = f"{base}?code={code}&state={state}"
        self.sent.append(loopback_get(self.callback))
