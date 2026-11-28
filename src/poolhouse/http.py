"""Stdlib HTTP: JSON, bytes, open bodies a caller reads itself, and which URLs may be
fetched at all."""

from __future__ import annotations

import ipaddress
import json
import queue
import re
import socket
import socketserver
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from http.client import HTTPConnection, HTTPSConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Any

from poolhouse import errors, lazy, macauth, sealing, serverkeys
from poolhouse.httpguard import Limits, Refused, resolve, split
from poolhouse.log import warn

USER_AGENT = "poolhouse"

# 5xx from a server means "busy / still loading", not "your request is wrong".
RETRY_STATUS = frozenset({500, 502, 503, 504})

MOST_BODY = 32 << 20
"""The most bytes of a request or response body any part of poolhouse takes or sends in one go."""


class ServerError(RuntimeError, errors.Error):
    """The server answered, but with an error."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "",
                 headers: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        # An HTTPError's headers, which look a name up whatever its case.
        self.headers: Any = headers if headers is not None else {}


class ServerUnreachable(ServerError):
    """Nothing is listening, or the connection died mid-request."""


class Server(ThreadingHTTPServer):
    """A threading HTTP server that names itself by the address it bound."""

    def server_bind(self) -> None:
        # http.server's bind asks socket.getfqdn(host): a reverse lookup that took 35s
        # on a macOS runner.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), int(port)


@dataclass(frozen=True, slots=True)
class Retry:
    """How many attempts a request gets, and what it retries."""

    tries: int = 1
    backoff: float = 0.5
    on_status: frozenset[int] = field(default=RETRY_STATUS)
    when_unreachable: bool = True


ONCE = Retry()


@dataclass(frozen=True, slots=True)
class Options:
    """Authentication, delivery and destination policy for an HTTP request."""

    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    token: str = field(default='', repr=False)
    timeout: float | None = 180.0
    retry: Retry = ONCE
    guard: Callable[[str], str] | None = field(default=None, repr=False)
    error_body_limit: int | None = None
    tls_context: ssl.SSLContext | None = field(default=None, repr=False)
    follow_redirects: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, 'headers', MappingProxyType(dict(self.headers)))
        if self.tls_context is not None and not isinstance(self.tls_context, ssl.SSLContext):
            raise TypeError('HTTP TLS context must be a software-owned SSLContext')
        if type(self.follow_redirects) is not bool:
            raise TypeError('HTTP redirect policy must be a boolean')
        limit = self.error_body_limit
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
            raise ValueError('HTTP error body limit must be a nonnegative integer')


DEFAULT_OPTIONS = Options()

_TRUSTED: ssl.SSLContext | None = None


def trust(cafile: str | Path | None) -> None:
    """Verify HTTPS against this certificate authority from now on, or None for the default.

    A development server signs its own certificate, and the store shipped with Python does
    not know it. One call says where that certificate is, and every request this module
    makes afterwards accepts it.
    """
    global _TRUSTED
    _TRUSTED = ssl.create_default_context(cafile=str(cafile)) if cafile else None


_PINNED: dict[str, ssl.SSLContext] = {}


def pin(netloc: str, context: ssl.SSLContext | None) -> None:
    """Talk to ``host:port`` over HTTPS with ``context``, which trusts that machine's one
    certificate; None forgets it. A host that is not pinned is verified against the usual
    authorities, which a self-signed certificate does not satisfy."""
    if context is None:
        _PINNED.pop(netloc.lower(), None)
    else:
        _PINNED[netloc.lower()] = context


def _https_context(url: str) -> ssl.SSLContext | None:
    if not url.lower().startswith("https://"):
        return None
    return _PINNED.get(urllib.parse.urlsplit(url).netloc.lower(), _TRUSTED)


@dataclass(frozen=True, slots=True)
class Reply:
    """The status, body and headers of one answer."""

    status: int
    body: bytes
    headers: Any


def json_body(raw: bytes) -> dict[str, Any]:
    """A request or response body as the object it holds; empty for anything else."""
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


SENSITIVE = re.compile(r"token|key|secret|passw|auth|sig|credential|session", re.IGNORECASE)


def shown(url: str) -> str:
    """``url`` as it may appear in a message or a log: no user or password, and no value in a
    query parameter whose name says it is a secret."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    netloc += f":{parts.port}" if parts.port else ""
    query = urllib.parse.urlencode(
        [(k, "***" if SENSITIVE.search(k) else v)
         for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)], safe="*")
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, ""))


@dataclass(frozen=True, slots=True)
class Sealed:
    """What opens the answer to a request that was signed with a fleet secret."""

    key: bytes = b""
    nonce: str = ""

    def open(self, status: int, headers: Any, body: bytes) -> bytes:
        """``body`` opened when the server sealed it, else as it came."""
        if not self.key:
            return body
        if not headers.get(sealing.HEADER):
            plain = status >= 400 or not body or "json" not in str(headers.get("Content-Type", ""))
            if plain:
                return body
            raise ServerError("the answer was not sealed", status=status)
        try:
            return sealing.open_(self.key, body, sealing.response_data(self.nonce, status))
        except sealing.SealError as exc:
            raise ServerError(f"the answer did not authenticate ({exc})", status=status) from exc


class _Request(urllib.request.Request):
    """A request that remembers what opens its answer."""

    sealed = Sealed()


def _at_the_server(url: str) -> str:
    """``url`` written to ``localhost`` as the address a keyed server of this computer holds, when one does, so that its key goes there and to no
    other listener the name could lead to."""
    parts = urllib.parse.urlsplit(url)
    host, _, port = parts.netloc.partition(":")
    if host.lower() != "localhost" or not port.isdigit():
        return url
    there = urllib.parse.urlunsplit(parts._replace(netloc=f"127.0.0.1:{port}"))
    return there if serverkeys.for_url(there) else url


def build_request(url: str, *, data: bytes | None = None, method: str | None = None,
                  headers: dict[str, str] | None = None,
                  token: str = "") -> urllib.request.Request:
    """A request carrying the caller's headers, a user agent and its credential.

    A ``token`` (or ``Authorization: Bearer`` header) that is a fleet MAC secret signs the
    request instead of being sent, and seals its body (`poolhouse.sealing`). Any other token
    is sent as a bearer token.
    """
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ServerError(f"only http(s) is fetched, not {shown(url)}")
    sent = {"User-Agent": USER_AGENT}
    sent.update(headers or {})
    if not token and "Authorization" not in sent:
        url = _at_the_server(url)
    opens = Sealed()
    verb = method or ("POST" if data is not None else "GET")
    secret = token if token.startswith(macauth.PREFIX) else macauth.unwrap(
        sent.get("Authorization", ""))
    if secret:
        sent.pop("Authorization", None)
        stamp = macauth.Stamp.now()
        key = sealing.box_key(secret)
        host, target = macauth.parts(url)
        if data:
            data = sealing.seal(key, data, sealing.request_data(
                verb, target, host, f"{stamp.at:.0f}", stamp.nonce))
        sent.update(macauth.sign(secret, verb, url, data, stamp))
        sent.setdefault(sealing.HEADER, "1")
        opens = Sealed(key, stamp.nonce)
    elif token:
        sent["Authorization"] = f"Bearer {token}"
    elif "Authorization" not in sent and (leased := serverkeys.for_url(url)):
        sent["Authorization"] = f"Bearer {leased}"
    if not secret and (data or "Authorization" in sent):
        _say_readable(url)
    request = _Request(url, data=data, method=verb, headers=sent)
    request.sealed = opens
    return request


_READABLE: set[str] = set()
"""The hosts already named by `_say_readable`."""


def _say_readable(url: str) -> None:
    """Warn, once a host, that what is sent in plain http to another machine, and any key with it, can be read on the network.
    Not refused: the address is the user's own choice, and a model server on their network may offer nothing else."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "http" or _this_computer(url) or parts.netloc.lower() in _READABLE:
        return
    _READABLE.add(parts.netloc.lower())
    warn(f"plain http to {urllib.parse.urlsplit(shown(url)).netloc}: what is sent, and any key with it, can be read on the network; "
         "use https where that server offers it")


class _Guarded(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to a URL ``guard`` accepts; ``guard`` raises to refuse."""

    def __init__(self, guard: Callable[[str], str]) -> None:
        self.guard = guard

    def redirect_request(self, req: Any, *rest: Any) -> Any:
        """``rest`` is urllib's ``fp, code, msg, headers, newurl``."""
        self.guard(urllib.parse.urljoin(req.full_url, rest[-1]))
        return super().redirect_request(req, *rest)


def _this_computer(url: str) -> bool:
    """Whether ``url`` names this computer, in any spelling the system's resolver takes."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.inet_aton(host))   # the short and numeric spellings of an IPv4 address
        except (OSError, ValueError):
            return False
    address = getattr(address, "ipv4_mapped", None) or address
    return address.is_loopback or address.is_unspecified


class _Proxies(urllib.request.ProxyHandler):
    """The environment's proxies for every address but this computer's own: a request to this computer may carry a server's key and goes
    straight there, whatever the environment says, a redirected one too."""

    def proxy_open(self, req: Any, proxy: str, type: str) -> Any:
        return None if _this_computer(req.full_url) else super().proxy_open(req, proxy, type)


def _proxies_for(url: str) -> list[Any]:
    """The proxy handlers of an opener for ``url``: none used for this computer; elsewhere the environment's, which still pass this computer by on
    a redirect."""
    if _this_computer(url):
        return [urllib.request.ProxyHandler({})]
    return [_Proxies()] if urllib.request.getproxies() else []


def _open(request: urllib.request.Request, timeout: float | None, guard: Callable[[str], str] | None,
          context: ssl.SSLContext | None = None, follow_redirects: bool = True) -> Any:
    context = context if context is not None else _https_context(request.full_url)
    proxies = _proxies_for(request.full_url)
    if guard is not None:
        try:
            loopback = ipaddress.ip_address(urllib.parse.urlsplit(request.full_url).hostname or '').is_loopback
        except ValueError:
            loopback = False
        if loopback:
            guard(request.full_url)
    redirects = ([_NoRedirect()] if not follow_redirects else [_Guarded(guard)] if guard is not None else [])
    if control := active_cancellation():
        handlers = [*proxies, *cancel_handlers(control, context)]
        handlers.extend(redirects)
        return urllib.request.build_opener(*handlers).open(request, timeout=timeout)
    if guard is None and follow_redirects and not urllib.request.getproxies():
        return urllib.request.urlopen(request, timeout=timeout, context=context)  # noqa: S310
    opener = urllib.request.build_opener(*proxies, *redirects, urllib.request.HTTPSHandler(
        context=context))
    return opener.open(request, timeout=timeout)


def open_stream(url: str, *, data: bytes | None = None, method: str | None = None,
                options: Options = DEFAULT_OPTIONS) -> Any:
    """The open response for ``url``, for a caller that reads the body itself; only http(s).

    ``guard`` is called with every redirect target before it is followed (``check`` is the
    usual one); what it raises ends the request.
    """
    delay = options.retry.backoff
    tries = max(1, options.retry.tries)
    last: ServerError | None = None

    for attempt in range(tries):
        request = build_request(url, data=data, method=method, headers=dict(options.headers), token=options.token)
        try:
            response = _open(request, options.timeout, options.guard, options.tls_context, options.follow_redirects)
            response.sealed = request.sealed
            return response

        except urllib.error.HTTPError as exc:
            with exc:
                content = exc.read() if options.error_body_limit is None else exc.read(options.error_body_limit + 1)
            if options.error_body_limit is not None and len(content) > options.error_body_limit:
                raise ServerError("HTTP error body exceeds the byte limit", status=exc.code) from exc
            raw = request.sealed.open(exc.code, exc.headers, content)
            detail = raw.decode("utf-8", "replace")[:500]
            last = ServerError(f"{shown(url)} -> HTTP {exc.code}: {detail}", status=exc.code,
                               body=detail, headers=exc.headers)
            if exc.code not in options.retry.on_status:
                raise last from exc

        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = ServerUnreachable(f"cannot reach {shown(url)} ({exc})")
            if not options.retry.when_unreachable:
                raise last from exc

        if attempt < tries - 1:
            time.sleep(delay)
            delay = min(delay * 1.5, 2.0)

    if last is None:
        raise ServerError(f"{shown(url)}: no attempt was made")
    raise last


@contextmanager
def _queued(url: str) -> Iterator[None]:
    """Wait for this request's turn when it runs a model on a server in the lease registry."""
    gate = lazy.load("poolhouse.gate")   # the node's request queue: a caller that only asks for health never loads it
    if not gate.is_generation(url):
        yield
        return
    try:
        control = active_cancellation()
        with gate.turn(url, cancelled=control.is_set if control is not None else None):
            yield
    except gate.QueueTimeout as exc:
        raise ServerError(str(exc), status=429) from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def head_once(url: str, *, headers: dict[str, str] | None = None, token: str = "",
              timeout: float = 30.0) -> Reply:
    """A HEAD request whose redirect is returned, not followed.

    A caller that sends a bearer token to a host which redirects to another one (a model
    hub handing out a signed CDN address) reads the ``Location`` here and asks the second
    host without the token.
    """
    request = build_request(url, method="HEAD", headers=headers, token=token)
    opener = urllib.request.build_opener(_NoRedirect, *_proxies_for(request.full_url))
    try:
        with opener.open(request, timeout=timeout) as response:
            return Reply(int(response.status), b"", response.headers)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            return Reply(exc.code, b"", exc.headers)
        raise ServerError(f"{url} -> HTTP {exc.code}", status=exc.code,
                          headers=exc.headers) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise ServerUnreachable(f"cannot reach {url} ({exc})") from exc


def request_bytes(url: str, *, data: bytes | None = None, method: str | None = None,
                  options: Options = DEFAULT_OPTIONS) -> Reply:
    """Send a request and read the whole answer, opened when the server sealed it."""
    asking = {**options.headers, sealing.HEADER: "2"}
    with _queued(url), open_stream(url, data=data, method=method,
                                   options=replace(options, headers=asking)) as response:
        status = int(response.status)
        opens = getattr(response, "sealed", Sealed())
        return Reply(status, opens.open(status, response.headers, response.read()), response.headers)


def request_json(url: str, *, payload: dict[str, Any] | None = None,
                 method: str | None = None, options: Options = DEFAULT_OPTIONS) -> Any:
    """Send a JSON request and parse the JSON response."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    sent = {"Content-Type": "application/json"}
    sent.update(options.headers)

    reply = request_bytes(url, data=data, method=method, options=replace(options, headers=sent))
    body = reply.body.decode("utf-8")
    try:
        return json.loads(body) if body else None
    except json.JSONDecodeError as exc:
        raise ServerError(f"{shown(url)} returned non-JSON: {exc}") from exc


def request_stream(url: str, *, payload: dict[str, Any], timeout: float | None = 180.0,
                   headers: dict[str, str] | None = None, token: str = ""):
    """POST a JSON request and yield each SSE ``data:`` payload, parsed, until ``[DONE]``."""
    sent = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    sent.update(headers or {})
    data = json.dumps(payload).encode("utf-8")
    try:
        with _queued(url), open_stream(url, data=data, method="POST", options=Options(headers=sent, token=token, timeout=timeout)) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    return
                try:
                    event = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and "error" in event and "choices" not in event:
                    said = event["error"]
                    said = said.get("message", said) if isinstance(said, dict) else said
                    raise ServerError(f"{shown(url)} -> error mid-stream: {said}", body=body)
                yield event
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise ServerUnreachable(f"cannot reach {shown(url)} ({exc})") from exc


# --- what may be fetched -------------------------------------------------------------------


def _addresses(host: str) -> list[str]:
    """Every address a host name resolves to. Separate so a test can answer for DNS."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        raise Refused(f"cannot resolve {host!r}: {exc}") from exc


def check(url: str) -> str:
    """The URL, if it may be fetched; ``Refused`` otherwise.

    http(s) only, and only to a host whose every address is a public one. ``file:``,
    ``localhost``, ``127.0.0.0/8``, ``10.0.0.0/8``, ``192.168.0.0/16``, ``172.16.0.0/12``,
    link-local and the IPv6 equivalents are all refused, by what the name resolves to.
    `poolhouse.httpguard.fetch` is the fetch that holds to this at connection time.
    """
    parts, host, port = split(url)
    resolve(host, port, Limits(resolver=lambda name, _port: _addresses(name)))
    return urllib.parse.urlunsplit(parts)


# --- cancelling a request ------------------------------------------------------------------


_CURRENT: ContextVar[Cancellation | None] = ContextVar('http_cancellation', default=None)

_DNS_WORKERS = 2
_DNS_JOBS: queue.Queue[_Resolution] = queue.Queue(maxsize=2)
_DNS_START = threading.Lock()
_DNS_STARTED = False


@dataclass
class _Resolution:
    address: tuple[str, int]
    resolver: Callable[..., Any]
    cancelled: Callable[[], bool]
    ready: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Exception | None = None


def _resolve_worker() -> None:
    while True:
        job = _DNS_JOBS.get()
        try:
            if not job.cancelled():
                job.result = job.resolver(*job.address, 0, socket.SOCK_STREAM)
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            job.error = error
        finally:
            job.ready.set()
            _DNS_JOBS.task_done()


def _resolve(control: Cancellation, address: tuple[str, int], timeout: float | None) -> Any:
    global _DNS_STARTED
    if control.is_set():
        raise OSError('HTTP request cancelled')
    try:
        ipaddress.ip_address(address[0].split('%', 1)[0])
    except ValueError:
        pass
    else:
        return socket.getaddrinfo(*address, 0, socket.SOCK_STREAM, 0, socket.AI_NUMERICHOST)
    with _DNS_START:
        if not _DNS_STARTED:
            for index in range(_DNS_WORKERS):
                threading.Thread(target=_resolve_worker, name=f'poolhouse-dns-{index}', daemon=True).start()
            _DNS_STARTED = True
    deadline = None if timeout is None else time.monotonic() + timeout
    def wait_interval() -> float:
        if deadline is None:
            return 0.05
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('HTTP hostname resolution timed out')
        return min(0.05, remaining)
    job = _Resolution(address, socket.getaddrinfo,
        lambda: control.is_set() or (deadline is not None and time.monotonic() >= deadline))
    while not control.is_set():
        try:
            _DNS_JOBS.put(job, timeout=wait_interval())
            break
        except queue.Full:
            continue
    while not control.is_set():
        if job.ready.wait(wait_interval()):
            wait_interval()
            if job.error is not None:
                raise job.error
            return job.result
    raise OSError('HTTP request cancelled')


class Cancellation:
    """Cancellable sockets for the maintained urllib requests above: close request sockets when their consumer cancels."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.sockets: list[socket.socket] = []

    def is_set(self) -> bool:
        return self.event.is_set()

    def bind(self, sock: socket.socket) -> None:
        with self.lock:
            self.sockets.append(sock)
            if self.is_set():
                self._close(sock)
                raise OSError('HTTP request cancelled')

    def set(self) -> None:
        with self.lock:
            self.event.set()
            for sock in self.sockets:
                self._close(sock)
            self.sockets.clear()

    @staticmethod
    def _close(sock: socket.socket) -> None:
        with suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
        with suppress(OSError):
            sock.close()

    def connect(self, address: tuple[str, int], timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
                source_address: Any = None) -> socket.socket:
        resolver_timeout = socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
        for family, kind, protocol, _, target in _resolve(self, address, resolver_timeout):
            if self.is_set():
                raise OSError('HTTP request cancelled')
            sock = socket.socket(family, kind, protocol)
            self.bind(sock)
            try:
                if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(target)
                if self.is_set():
                    raise OSError('HTTP request cancelled')
                return sock
            except OSError as exc:
                sock.close()
                last = exc
        raise last if 'last' in locals() else OSError('no addresses for HTTP endpoint')


@contextmanager
def cancel_scope(control: Cancellation):
    """Bind one request-opening thread to its cancellation handle."""
    token = _CURRENT.set(control)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def active_cancellation() -> Cancellation | None:
    return _CURRENT.get()


def cancel_handlers(control: Cancellation, context: Any) -> tuple[Any, Any]:
    """HTTP handlers retaining urllib routing and the caller's TLS context."""
    class HTTP(HTTPConnection):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._create_connection = control.connect

    class HTTPS(HTTPSConnection):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._create_connection = control.connect

        def connect(self) -> None:
            HTTPConnection.connect(self)
            self.sock = self._context.wrap_socket(self.sock,
                server_hostname=self._tunnel_host or self.host, do_handshake_on_connect=False)
            control.bind(self.sock)
            self.sock.do_handshake()

    class Plain(urllib.request.HTTPHandler):
        def http_open(self, request: Any) -> Any:
            return self.do_open(HTTP, request)

    class Secure(urllib.request.HTTPSHandler):
        def https_open(self, request: Any) -> Any:
            return self.do_open(HTTPS, request, context=self._context)

    return Plain(), Secure(context=context)
