"""Explicit local SDK models over the maintained fleet HTTP transport."""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress
from typing import Any

import httpx2
from agents import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from poolhouse.http import Options, ServerError, open_stream
from poolhouse.http_cancel import Cancellation, scope


class _Body(httpx2.AsyncByteStream):
    def __init__(self, response: Any, limit: int | None = None) -> None:
        self.response, self.limit, self.received = response, limit, 0

    async def __aiter__(self):
        try:
            while block := await asyncio.to_thread(self.response.read1, 4096):
                self.received += len(block)
                if self.limit is not None and self.received > self.limit:
                    raise ValueError("SDK response exceeds its byte limit")
                yield block
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        fp = getattr(self.response, "fp", None)
        raw = getattr(fp, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
                taken = sock.detach()  # -1 when an earlier close already took it
                if taken >= 0:
                    with socket.socket(fileno=taken):
                        pass
        self.response.close()


def _open_response(url: str, data: bytes, cancelled: Cancellation, options: Options) -> Any:
    with scope(cancelled):
        response = open_stream(url, data=data, method="POST", options=options)
    if cancelled.is_set():
        response.close()
    return response


class FleetTransport(httpx2.AsyncBaseTransport):
    """Send SDK requests with fleet signatures, server keys and pinned TLS."""

    def __init__(self, url: str, token: str, timeout: float | None = None, *, response_limit: int | None = None) -> None:
        if response_limit is not None and (type(response_limit) is not int or response_limit < 1):
            raise ValueError("SDK response limit must be a positive integer")
        self.url, self.token, self.timeout, self.response_limit = url, token, timeout, response_limit
        self.options = Options(token=token, headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                                                   "Accept-Encoding": "identity"}, timeout=timeout,
                               follow_redirects=False, error_body_limit=response_limit)
        self.bodies: list[_Body] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url) != self.url or request.method != "POST":
            raise ValueError("SDK requests must use the selected model endpoint")
        data = await request.aread()
        cancelled = Cancellation()
        opening = asyncio.create_task(asyncio.to_thread(
            _open_response, self.url, data, cancelled, self.options))
        try:
            response = await asyncio.shield(opening)
        except asyncio.CancelledError:
            cancelled.set()
            with suppress(Exception):
                await opening
            raise
        except ServerError as exc:
            return httpx2.Response(exc.status or 502, content=exc.body or str(exc), request=request)
        encodings = response.headers.get_all("Content-Encoding", [])
        if encodings and (len(encodings) != 1 or encodings[0].strip().lower() != "identity"):
            await _Body(response).aclose()
            raise ValueError("SDK responses must use identity content encoding")
        body = _Body(response, self.response_limit)
        self.bodies.append(body)
        return httpx2.Response(response.status, headers=dict(response.headers), stream=body,
                              request=request)

    async def aclose(self) -> None:
        for body in self.bodies:
            await body.aclose()
        self.bodies.clear()


def local_client(target: Any, model: str, *, token: str = "", timeout: float | None = None,
                 response_limit: int | None = None) -> AsyncOpenAI:
    """Create an explicit client for the selected local or member chat endpoint."""
    suffix = "/chat/completions"
    url = str(target.url)
    if not url.endswith(suffix) or not model:
        raise ValueError("a model and complete chat endpoint are required")
    transport = FleetTransport(url, token or target.token, timeout, response_limit=response_limit)
    return AsyncOpenAI(base_url=url[:-len(suffix)], api_key="local-transport",
                       max_retries=0, timeout=timeout, http_client=httpx2.AsyncClient(transport=transport,
                                                                  trust_env=False))


def local_model(target: Any, model: str, *, token: str = "",
                timeout: float | None = None) -> OpenAIChatCompletionsModel:
    """Create an SDK model backed by the selected maintained HTTP transport."""
    return OpenAIChatCompletionsModel(model, local_client(target, model, token=token, timeout=timeout))
