import json
from typing import Any, Awaitable, Callable, Dict, MutableMapping

import pytest
from aiohttp import ClientSession, ClientTimeout
from fastapi import FastAPI

from aiohttp_asgi_connector import ASGIApplicationConnector


async def test_app_failure_handled(session: ClientSession) -> None:
    async with session.get("/fail?handle=true") as resp:
        assert await resp.json()


async def test_app_failure_propagate(session: ClientSession) -> None:
    with pytest.raises(RuntimeError, match="something bad happened"):
        async with session.get("/fail?handle=false") as resp:
            assert await resp.json()


async def test_bad_method(session: ClientSession) -> None:
    async with session.post("/get") as resp:
        assert (await resp.json()) == {"detail": "Method Not Allowed"}


async def test_get(session: ClientSession) -> None:
    async with session.get("/get") as resp:
        assert (await resp.json()) is True


async def test_post_json(session: ClientSession) -> None:
    async with session.post("/post_json", json={"message": "hello world"}) as resp:
        assert (await resp.json()) == {"broadcast": "hello world"}


async def test_close_session_with_unread_response(application: FastAPI) -> None:
    async with ClientSession(
        connector=ASGIApplicationConnector(application), base_url="http://localhost"
    ) as session:
        resp = await session.post("/post_json", json={"message": "x" * 2**20})
        assert resp.status == 200


async def test_post_form(session: ClientSession) -> None:
    async with session.post("/post_form", data={"message": "hello world"}) as resp:
        assert (await resp.json()) == {"broadcast": "hello world"}


async def test_app_stream(session: ClientSession) -> None:
    async with session.get("/stream", timeout=ClientTimeout(total=3)) as resp:
        content = [i async for i in resp.content.iter_any()]
        assert json.loads(b"".join(content).decode())
        assert len(content) == 4


async def test_disconnect_after_response_sent() -> None:
    async def app(
        scope: Dict[str, Any],
        receive: Callable[[], Awaitable[Dict[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        while (await receive()).get("more_body", False):
            pass

        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

        # After the response is sent we should get a 'disconnected' event, see
        # https://asgi.readthedocs.io/en/latest/specs/www.html#disconnect-receive-event
        assert (await receive()).get("type") == "http.disconnect"

    async with ClientSession(
        connector=ASGIApplicationConnector(app)
    ) as session, session.post("http://localhost") as resp:
        assert resp.status == 204
