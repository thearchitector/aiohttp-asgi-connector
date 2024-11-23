import pytest
from aiohttp import ClientSession
from fastapi import Body, FastAPI, Form, HTTPException
from fastapi.responses import JSONResponse
from pytest_asyncio import is_async_test
from typing_extensions import Annotated

from aiohttp_asgi_connector import ASGIApplicationConnector


def pytest_collection_modifyitems(items):
    pytest_asyncio_tests = (item for item in items if is_async_test(item))
    session_scope_marker = pytest.mark.asyncio(loop_scope="session")
    for async_test in pytest_asyncio_tests:
        async_test.add_marker(session_scope_marker, append=False)


app = FastAPI(default_response_class=JSONResponse)


@app.get("/get")
async def get():
    return True


@app.post("/post_json")
async def post_json(message: Annotated[str, Body(embed=True)]):
    return {"broadcast": message}


@app.post("/post_form")
async def post_form(message: Annotated[str, Form()]):
    return {"broadcast": message}


@app.get("/fail")
async def fail(handle: bool):
    if handle:
        raise HTTPException(status_code=500, detail="something bad happened")
    raise Exception("something bad happened")


@pytest.fixture(scope="session")
def application():
    return app


@pytest.fixture(scope="session")
async def asgi_connector(application):
    return ASGIApplicationConnector(application)


@pytest.fixture(scope="session")
async def session(asgi_connector):
    async with ClientSession(
        base_url="http://localhost", connector=asgi_connector
    ) as session:
        yield session
