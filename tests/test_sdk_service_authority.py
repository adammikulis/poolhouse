"""SDK streaming keeps the selected node service authorized through each read."""

import asyncio
from dataclasses import replace
from email.message import Message
from types import SimpleNamespace

import httpx2
import pytest

from poolhouse.client import sdk
from poolhouse.testing.canned import CANNED_MARKER

URL = "https://device.invalid:8770/infer/v1/chat/completions"


@pytest.fixture
def transport(monkeypatch):
    state = {"revoked": False, "revoke_on_read": False, "revoke_on_open": False,
             "closed": 0, "opened": 0, "options": [], "reads": 0}

    class Service:
        def check_current(self):
            if state["revoked"]:
                raise PermissionError("service removed")

        def current_options(self, options):
            self.check_current()
            return replace(options, token="current-service-credential", follow_redirects=False)

    class Response:
        status = 200
        headers = Message()

        def read1(self, _count):
            state["reads"] += 1
            if state["revoke_on_read"]:
                state["revoked"] = True
            return CANNED_MARKER.encode() if state["reads"] == 1 else b""

        def close(self):
            state["closed"] += 1

    def opened(url, *, data, method, options):
        assert url == URL and method == "POST"
        state["opened"] += 1
        state["options"].append(options)
        if state["revoke_on_open"]:
            state["revoked"] = True
        return Response()

    monkeypatch.setattr(sdk, "open_stream", opened)
    state["service"] = Service()
    return state


def test_sdk_uses_live_service_options_instead_of_supplied_pool_token(transport):
    async def run():
        client = sdk.FleetTransport(URL, "stale-pool-token", service=transport["service"])
        response = await client.handle_async_request(httpx2.Request("POST", URL, json={}))
        assert await response.aread() == CANNED_MARKER.encode()
        assert transport["options"][0].token == "current-service-credential"
        assert transport["options"][0].follow_redirects is False
        assert transport["closed"] >= 1
        await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["before", "open", "read"])
def test_sdk_denies_removed_service_and_closes_any_open_response(transport, stage):
    transport["revoked"] = stage == "before"
    transport["revoke_on_open"] = stage == "open"
    transport["revoke_on_read"] = stage == "read"
    async def run():
        client = sdk.FleetTransport(URL, "stale-pool-token", service=transport["service"])
        with pytest.raises(PermissionError, match="removed"):
            response = await client.handle_async_request(httpx2.Request("POST", URL, json={}))
            await anext(response.aiter_raw())
        assert transport["opened"] == (0 if stage == "before" else 1)
        assert transport["closed"] == (0 if stage == "before" else 1)
    asyncio.run(run())


def test_local_client_uses_checked_target_service(transport):
    target = SimpleNamespace(url=URL, token="stale-pool-token", member_client=lambda: transport["service"])
    async def run():
        client = sdk.local_client(target, "plumbing-only")
        try:
            response = await client._client.post(URL, json={})
            assert response.content == CANNED_MARKER.encode()
            assert transport["options"][0].token == "current-service-credential"
        finally:
            await client.close()
    asyncio.run(run())
