import pytest


async def test_bad_method(session):
    async with session.post("/ping") as resp:
        assert (await resp.json()) == {"detail": "Method Not Allowed"}


async def test_simple_get(session):
    async with session.get("/ping") as resp:
        assert (await resp.json()) is True


async def test_simple_post(session):
    async with session.post("/post_ping", json={"message": "hello world"}) as resp:
        assert (await resp.json()) == {"broadcast": "hello world"}


async def test_app_failure_handled(session):
    async with session.get("/fail?handle=true") as resp:
        assert await resp.json()


async def test_app_failure_propagate(session):
    with pytest.raises(Exception, match="something bad happened"):
        async with session.get("/fail?handle=false") as resp:
            assert await resp.json()
