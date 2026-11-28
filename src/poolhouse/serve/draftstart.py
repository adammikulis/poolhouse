"""Starting a llama.cpp server with a draft head: which head is tried, in what order, and what a start that
fails is retried with. Every head is chosen here, before it is verified and recorded, so both name the file served."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from poolhouse import home, hub
from poolhouse.hub import hfstore
from poolhouse.serve import drafthead, guarded, mtp
from poolhouse.serve.backend import (
    LlamaServerBackend,
    ServerBackend,
    ServerFailed,
    ServerInfo,
    ServerSpec,
    UnknownFlag,
)
from poolhouse.serve.binary import BinaryNotFound, borrows
from poolhouse.serve.preflight import PreflightFailed

__all__ = ["Attempt", "Start"]


@dataclass(frozen=True, slots=True)
class Attempt:
    """One way to start the server: its spec, the fork build it needs ('' for the backend asked), and ``note``,
    what is said when this is not what the caller asked for."""

    spec: ServerSpec
    build: str = ""
    note: str = ""


def _same(one: object, other: object) -> bool:
    return bool(one and other) and home.expand(str(one)).resolve() == home.expand(str(other)).resolve()


def _managed(head: str) -> bool:
    """Whether Poolhouse can find another copy of ``head``: an ``hf:`` reference, or a file of a Hub cache."""
    found = None if head.startswith("hf:") else hfstore.identify(home.expand(head))
    return head.startswith("hf:") or bool(found and found[2])


def _headless(spec: ServerSpec) -> bool:
    """Whether ``spec`` asks for a draft method with no head to do it: no draft, and weights on disk that carry none."""
    model = home.expand(str(spec.model))
    return (not spec.draft and spec.spec_type.startswith("draft-") and spec.mtp is not True
            and model.is_file() and not mtp.embeds_head(model))


def _without(spec: ServerSpec, why: str) -> Attempt:
    return Attempt(replace(spec, draft=None, spec_type="", mtp=False),
                   note=f"MTP off: {why}; SERVING WITHOUT DRAFTING (docs/serving.md, 'Multi-token prediction', has the "
                        "one-command check)")


def _instead(spec: ServerSpec, binary: Path | None, spare: bool, why: str) -> Iterator[Attempt]:
    """What is served when no head for the asked build will do: a borrowing head on the fork build that loads it,
    where the caller named no build of its own, then no drafting."""
    if spare:
        forked = next((one for one in hub.heads_for(str(spec.model), binary=binary)
                       if one.build and one.spec_type == mtp.KIND), None)
        if forked is not None:
            yield Attempt(replace(spec, draft=forked.path, spec_type=forked.spec_type), build=forked.build,
                          note=f"{why}; drafting with {forked.name} on the fork build {forked.build}")
    yield _without(spec, why)


def _attempts(spec: ServerSpec, binary: Path | None, spare: bool) -> Iterator[Attempt]:
    if not spec.draft:
        if _headless(spec):
            yield from _instead(spec, binary, spare, "the draft head asked for could not be had")
            return
        yield Attempt(spec)
    else:
        asked = str(spec.draft)
        if not borrows(binary) and not hub.borrowed_head(Path(asked).name) and _managed(asked):
            for head in drafthead.copies(asked):
                changed = "" if _same(head, asked) or asked.startswith("hf:") else f"drafting with {head}"
                yield Attempt(replace(spec, draft=head), note=changed)
            yield from _instead(spec, binary, spare, f"no copy of {Path(asked).name} would serve")
            return
        yield Attempt(spec)
    if spec.mtp is True:
        yield _without(spec, "the server would not start with it")


class Start:
    """The attempts to start ``spec`` on ``backend``. ``verify`` is asked about each head before it is used and may
    raise `guarded.SentinelRefused`: for the head asked for that refuses the start, and another is passed over.
    ``say`` is told each time something else than asked is served."""

    def __init__(self, spec: ServerSpec, backend: ServerBackend, *, verify: Callable[[object], object],
                 say: Callable[[str], None]) -> None:
        self.asked, self.backend, self.verify, self.say = spec, backend, verify, say
        self._queue = self._verified(self._plan())
        self.first = next(self._queue)

    def _plan(self) -> Iterator[Attempt]:
        backend = self.backend
        if type(backend) is not LlamaServerBackend:
            yield Attempt(self.asked)
            if self.asked.mtp is True:
                yield _without(self.asked, "the server would not start with it")
            return
        try:
            binary: Path | None = backend.binary
        except (BinaryNotFound, OSError):
            binary = None  # not here yet: the backend installs it as it starts
        chosen = backend.options()
        spare = not ({"binary", "build"} & set(chosen) or os.environ.get("LLAMA_CPP_SERVER"))
        yield from _attempts(self.asked, binary, spare)

    def _verified(self, planned: Iterator[Attempt]) -> Iterator[Attempt]:
        for one in planned:
            head = one.spec.draft
            try:
                if head:
                    self.verify(head)
            except guarded.SentinelRefused as refused:
                if _same(head, self.asked.draft):
                    raise
                self.say(f"the head {Path(str(head)).name} is not used: {refused}")
                continue
            yield one

    def _on(self, attempt: Attempt) -> ServerBackend:
        if not attempt.build:
            return self.backend
        backend = self.backend
        return LlamaServerBackend(build=attempt.build, sandboxed=getattr(backend, "sandboxed", None),
                                  policy=getattr(backend, "policy", None))

    def run(self, **starting: Any) -> tuple[ServerInfo, ServerSpec]:
        """The server that started and the spec it started with, trying each attempt in turn. Raises the first
        attempt's failure when none starts. A head is remembered as failing only once another attempt starts."""
        attempt, failed = self.first, []
        while True:
            try:
                info = self._on(attempt).start(attempt.spec, **starting)
                break
            except (ServerFailed, UnknownFlag, PreflightFailed) as why:
                failed.append((attempt, why))
                attempt = next(self._queue, None)
                if attempt is None:
                    first = failed[0][1]
                    raise first from first.__cause__
                self.say(f"port {self.asked.port}: the server would not start with it "
                         f"({str(why).splitlines()[0]}); trying again")
        for one, _why in failed:
            mtp.failed(one.spec.model, one.spec.draft, getattr(self.backend, "binary", None))
        served = attempt.spec
        if attempt.note:
            self.say(f"port {served.port}: {attempt.note}")
            reason = f" ({str(failed[-1][1]).splitlines()[0]})" if failed else ""
            info = replace(info, mtp_note=f"{attempt.note}{reason}")
        if served.mtp is True:
            info = replace(info, mtp=Path(str(served.draft)).name if served.draft else "embedded",
                           mtp_note=info.mtp_note if attempt.note else f"MTP on: {served.spec_type}")
        return info, served
