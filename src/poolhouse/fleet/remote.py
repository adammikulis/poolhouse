"""Client for one fleet daemon on another machine."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from poolhouse import home
from poolhouse.files import promote, sha256_file
from poolhouse.http import (
    ONCE,
    Options,
    Retry,
    ServerError,
    ServerUnreachable,
    open_stream,
    request_bytes,
)

from .discovery import (
    Beacon,
    DiscoveryError,
    derive_token,
    discover,
    key_path,
    load_pool_key,
    primary_ip,
)
from .onboard.lan import secure_scheme

if TYPE_CHECKING:
    from poolhouse.speech.protocols import Transcript

CHUNK = 8 << 20          # 8MB: big enough to be fast, small enough to resume cheaply
DIGEST_HEADER = "X-Poolhouse-SHA256"

# A read is safe to send again; an unreachable member is not worth waiting on twice.
READS = Retry(tries=3, when_unreachable=False)


@dataclass(frozen=True, slots=True)
class Discovery:
    timeout_s: float = 2.0
    key: bytes | None = field(default=None, repr=False)
    pool_key_path: Path | str | None = None
    group: str | None = None
    port: int | None = None


DEFAULT_DISCOVERY = Discovery()
DEFAULT_REQUEST = Options(timeout=None)
AGENT_ENV = "POOLHOUSE_WORKSPACE_AGENT"
AGENT_HEADER = "X-Poolhouse-Agent"


def device_address(member, declared: str) -> bool:
    """Whether an origin names the authenticated device endpoint."""
    beacon = member.beacon
    if not beacon or not beacon.cert or not beacon.machine:
        return False
    try:
        observed, named = urllib.parse.urlsplit(member.base_url), urllib.parse.urlsplit(declared)
        if (named.scheme != 'https' or observed.scheme != 'https' or named.port != observed.port
                or named.username or named.password or named.path not in ('', '/')
                or named.query or named.fragment):
            return False
        if declared.rstrip('/') == member.base_url.rstrip('/'):
            return True
        if beacon.machine != home.machine_id():
            return False
        return same_machine_host(member.base_url, declared)  # this machine's beacon under loopback or LAN name
    except ValueError:
        return False


def same_machine_host(first: str, second: str) -> bool:
    """Whether two origins on one port name this machine, by loopback or by its LAN address."""
    try:
        one, two = urllib.parse.urlsplit(first), urllib.parse.urlsplit(second)
        if one.port != two.port:
            return False
        here = {primary_ip(), *(h for h in (one.hostname, two.hostname)
                                if ipaddress.ip_address(h).is_loopback)}
        return one.hostname in here and two.hostname in here
    except ValueError:
        return False


def range_total(content_range: str) -> int | None:
    """Total size out of a ``bytes */N`` or ``bytes a-b/N`` header."""
    _, _, tail = content_range.partition("/")
    try:
        return int(tail)
    except ValueError:
        return None


class MemberError(ServerError):
    """One request to one member did not work."""


def _member_error(where: str, exc: ServerError) -> MemberError:
    """One member's failure, said the way its callers read it."""
    if isinstance(exc, ServerUnreachable):
        return MemberError(f"{where} -> unreachable: {exc}")
    return MemberError(f"{where} -> {exc.status}: {exc.body[:400]}",
                     status=exc.status, body=exc.body, headers=exc.headers)


class Member:
    def __init__(self, base_url: str, token: str, *, timeout: float = 60.0,
                 beacon: Beacon | None = None) -> None:
        if not secure_scheme(base_url):
            raise ValueError(f"a member is reached over https, or http on this machine only: {base_url}")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.beacon = beacon

    @property
    def name(self) -> str:
        return self.beacon.name if self.beacon else self.base_url

    # -- discovery -------------------------------------------------------
    @classmethod
    def discover(cls, *, discovery: Discovery = DEFAULT_DISCOVERY,
                 timeout: float = 60.0) -> list[Member]:
        """Every daemon on the LAN that proves it holds the pool key."""
        key = discovery.key or load_pool_key(discovery.pool_key_path)
        if key is None:
            raise DiscoveryError(
                f"no pool key at {key_path(discovery.pool_key_path)} -- "
                "run 'poolhouse-members init' here and copy the key to each box")
        token = derive_token(key)
        return [cls(b.base_url, token, timeout=timeout, beacon=b)
                for b in discover(key, timeout_s=discovery.timeout_s, group=discovery.group, port=discovery.port)]

    @classmethod
    def find_one(cls, *, require: str = "", name: str = "",
                 discovery: Discovery = DEFAULT_DISCOVERY, timeout: float = 60.0) -> Member:
        """The one member to use, or an error saying exactly why there isn't one."""
        members = cls.discover(discovery=discovery, timeout=timeout)
        if not members:
            raise DiscoveryError(
                "no members answered -- is traind running there, is it on this "
                "LAN, and does it hold the same pool key?")
        pool = members
        if name:
            pool = [p for p in pool if p.beacon and p.beacon.name == name]
        if require:
            pool = [p for p in pool
                    if p.beacon and (p.beacon.device.get(require)
                                     or require in (p.beacon.device.get("backends") or []))]
        if not pool:
            seen = ", ".join(f"{p.name} {sorted(p.beacon.device.get('backends') or [])}"
                             for p in members if p.beacon)
            raise DiscoveryError(
                f"no member matches name={name!r} require={require!r}; found: {seen}")
        if len(pool) > 1:
            raise DiscoveryError(
                "several members match, name one: "
                + ", ".join(p.name for p in pool))
        return pool[0]

    # -- plumbing --------------------------------------------------------
    def _request(self, method: str, path: str, *, data: bytes | None = None,
                 options: Options = DEFAULT_REQUEST) -> tuple[int, bytes, Any]:
        agent = {AGENT_HEADER: os.environ[AGENT_ENV]} if os.environ.get(AGENT_ENV) else {}
        try:
            reply = request_bytes(f"{self.base_url}{path}", data=data, method=method,
                                  options=replace(options, headers={**options.headers, **agent}, token=self.token,
                                                  timeout=options.timeout or self.timeout))
        except ServerError as exc:
            raise _member_error(f"{method} {path}", exc) from None
        return reply.status, reply.body, reply.headers

    def _json(self, method: str, path: str, payload: Any = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        _, body, _ = self._request(method, path, data=data, options=Options(headers=headers, retry=READS if method == "GET" else ONCE, timeout=None))
        return json.loads(body or b"{}")

    # -- status ----------------------------------------------------------
    def health(self) -> dict:
        _, body, _ = self._request("GET", "/health", options=Options(timeout=10, retry=READS))
        return json.loads(body or b"{}")

    def members(self, group: str, rows: list[dict]) -> dict:
        """Give this member our record of a pool's devices and take back its own."""
        return self._json("POST", "/fleet/v1/members", {"group": group, "rows": rows})

    def availability(self, action: str = "", **fields: Any) -> dict:
        """Read or change when this member takes work."""
        if not action:
            return self._json("GET", "/availability")
        return self._json("POST", "/availability", {"action": action, **fields})

    def models(self) -> list[dict]:
        """Model files this member holds."""
        return self._json("GET", "/models")["models"]

    def get_model(self, name: str, *, source: str = "") -> dict:
        """Have this member obtain a model, from the network or the internet."""
        return self._json("POST", "/models/get", {"name": name, "source": source})

    def jobs(self) -> list[dict]:
        return self._json("GET", "/jobs")["jobs"]

    def job(self, job_id: str) -> dict:
        return self._json("GET", f"/jobs/{job_id}")

    def log(self, job_id: str, tail: int = 200) -> str:
        return self._json("GET", f"/jobs/{job_id}/log?tail={tail}")["log"]

    def metrics(self, job_id: str, since: int = 0) -> tuple[list[dict], int]:
        out = self._json("GET", f"/jobs/{job_id}/metrics?since={since}")
        return out["metrics"], out["next"]

    # -- jobs ------------------------------------------------------------
    def submit(self, argv: list[str] | str, *, name: str = "") -> dict:
        """Run an allowed poolhouse command here (`fleet.commands`); anything else is refused."""
        return self._json("POST", "/jobs", {"argv": argv, "name": name})

    def stop(self, job_id: str) -> dict:
        return self._json("POST", f"/jobs/{job_id}/stop", {})

    def wait(self, job_id: str, *, poll_s: float = 20.0,
             on_metric: Callable[[dict], None] | None = None,
             timeout_s: float | None = None) -> dict:
        """Block until the job leaves the running/queued states."""
        since, deadline = 0, (time.time() + timeout_s) if timeout_s else None
        while True:
            job = self.job(job_id)
            if on_metric is not None:
                rows, since = self.metrics(job_id, since)
                for row in rows:
                    on_metric(row)
            if job["state"] not in ("queued", "running"):
                return job
            if deadline and time.time() > deadline:
                raise MemberError(f"job {job_id} still {job['state']} after timeout")
            time.sleep(poll_s)

    # -- test shards -----------------------------------------------------
    def shard_caps(self) -> dict:
        """Whether this member takes test shards (and why not), its platform and its free slots."""
        return self._json("GET", "/shards/v1/caps")

    def shard_upload(self, shard: str, tree: bytes) -> dict:
        """Send the packed tree of ``shard`` in order, 8 MB at a time; the last answer."""
        total, sent, last = len(tree), 0, b"{}"
        while sent < total:
            chunk = tree[sent:sent + CHUNK]
            span = {"Content-Range": f"bytes {sent}-{sent + len(chunk) - 1}/{total}"}
            _, last, _ = self._request("PUT", f"/shards/v1/{shard}/tree", data=chunk, options=Options(headers=span, timeout=300))
            sent += len(chunk)
        return json.loads(last or b"{}")

    def shard_start(self, header: dict) -> dict:
        """Run the shard ``header`` names on the tree already sent with `shard_upload`."""
        return self._json("POST", "/shards/v1/start", header)

    def shard_state(self, shard: str, since: int = 0) -> dict:
        """A shard's state, its output from byte ``since`` and, once it has ended, its result."""
        return self._json("GET", f"/shards/v1/{shard}?since={since}")

    def shard_cancel(self, shard: str) -> dict:
        """Stop a shard and what it started."""
        return self._json("POST", f"/shards/v1/{shard}/cancel", {})

    # -- speech ----------------------------------------------------------
    def speech(self) -> dict:
        """What this member can hear and say: every engine it has, and which it would use."""
        return self._json("GET", "/speech/providers")

    def transcribe(self, audio: Path | str | bytes, *, language: str | None = None,
                   provider: str | None = None, timeout: float = 600.0) -> Transcript:
        """Have this member turn audio into text, so a machine with no speech model can still
        transcribe. ``audio`` is a path or the bytes of an audio file."""
        from poolhouse.speech.protocols import Segment, Transcript

        data = audio if isinstance(audio, bytes) else Path(audio).expanduser().read_bytes()
        query = urllib.parse.urlencode(
            {k: v for k, v in (("language", language or ""), ("provider", provider or ""))
             if v})
        _, body, _ = self._request("POST", "/speech/transcribe" + (f"?{query}" if query else ""), data=data, options=Options(headers={"Content-Type": "audio/wav"}, timeout=timeout))
        got = json.loads(body or b"{}")
        return Transcript(
            text=str(got.get("text", "")),
            language=got.get("language"),
            duration_s=float(got.get("duration_s") or 0.0),
            segments=tuple(Segment(text=str(s.get("text", "")),
                                   start_s=float(s.get("start_s") or 0.0),
                                   end_s=float(s.get("end_s") or 0.0))
                           for s in got.get("segments") or ()),
            model=got.get("model"))

    # -- files -----------------------------------------------------------
    def push(self, local: Path | str, remote: str, *,
             on_progress: Callable[[int, int], None] | None = None) -> dict:
        """Upload, resuming from whatever the daemon already holds."""
        local = Path(local).expanduser()
        total = local.stat().st_size
        sent = 0
        digest = hashlib.sha256()
        with local.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk and total:
                    break
                digest.update(chunk)
                last = fh.tell() >= total
                headers = {"X-Poolhouse-Complete": "1" if last else "0"}
                if chunk:
                    headers["Content-Range"] = f"bytes {sent}-{sent+len(chunk)-1}/{total}"
                if last:
                    headers[DIGEST_HEADER] = digest.hexdigest()
                _, body, _ = self._request("PUT", f"/files/{remote}", data=chunk, options=Options(headers=headers, timeout=300))
                sent += len(chunk)
                if on_progress:
                    on_progress(sent, total)
                if not chunk:
                    break
        return json.loads(body or b"{}")

    def pull(self, remote: str, local: Path | str, *,
             on_progress: Callable[[int, int], None] | None = None,
             timeout: float = 600.0, route: str = "/files/") -> Path:
        """Download, resuming a partial file rather than restarting it."""
        local = Path(local).expanduser()
        local.parent.mkdir(parents=True, exist_ok=True)
        partial = local.with_suffix(local.suffix + ".part")
        start = partial.stat().st_size if partial.exists() else 0

        try:
            resp = open_stream(f"{self.base_url}{route}{remote}", method="GET", options=Options(headers={"Range": f"bytes={start}-"} if start else {}, token=self.token, timeout=timeout))
        except ServerError as exc:
            if exc.status == 416:
                size = range_total(exc.headers.get("Content-Range", ""))
                if size is not None and start == size:
                    promote(partial, local)
                    return local
                raise MemberError(
                    f"{partial} is {start} bytes but the remote file is "
                    f"{size}; delete it and pull again") from None
            raise _member_error(f"GET {route}{remote}", exc) from None

        with resp:
            want = (resp.headers.get(DIGEST_HEADER) or "").strip().lower()
            expected = resp.headers.get("Content-Length")
            total = start + int(expected) if expected is not None else None
            done = start
            with partial.open("ab" if start else "wb") as fh:
                while True:
                    buf = resp.read(CHUNK)
                    if not buf:
                        break
                    fh.write(buf)
                    done += len(buf)
                    if on_progress:
                        on_progress(done, total if total is not None else done)
        got = partial.stat().st_size
        if total is not None and got != total:
            raise MemberError(
                f"{remote}: got {got} of {total} bytes; left {partial.name} "
                "in place to resume from")
        if want:
            actual = sha256_file(partial)
            if not hmac.compare_digest(actual, want):
                partial.unlink(missing_ok=True)
                raise MemberError(
                    f"{remote}: checksum mismatch (expected {want[:16]}..., "
                    f"got {actual[:16]}...); discarded the download")
        promote(partial, local)
        return local

