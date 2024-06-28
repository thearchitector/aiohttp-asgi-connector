import pytest
from aiohttp import ClientSession
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pytest_asyncio import is_async_test
from typing_extensions import Annotated

from aiohttp_asgi_connector import ASGIApplicationConnector


def pytest_collection_modifyitems(items):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)


app = FastAPI(default_response_class=JSONResponse)


@app.get("/ping")
async def pong():
    return True


@app.post("/post_ping")
async def post_ping(message: Annotated[str, Body(embed=True)]):
    return {"broadcast": message}


@app.get("/fail")
async def fail(handle: bool):
    if handle:
        raise HTTPException(status_code=500, detail="something bad happened")
    raise Exception("something bad happened")


@pytest.fixture(scope="session")
async def asgi_connector():
    return ASGIApplicationConnector(app)


@pytest.fixture(scope="session")
async def session(asgi_connector):
    async with ClientSession(
        base_url="http://localhost", connector=asgi_connector
    ) as session:
        yield session
