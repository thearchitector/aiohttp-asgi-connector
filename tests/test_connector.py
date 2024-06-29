import pytest


async def test_bad_method(session):
    async with session.post("/get") as resp:
        assert (await resp.json()) == {"detail": "Method Not Allowed"}


async def test_get(session):
    async with session.get("/get") as resp:
        assert (await resp.json()) is True


async def test_post_json(session):
    async with session.post("/post_json", json={"message": "hello world"}) as resp:
        assert (await resp.json()) == {"broadcast": "hello world"}


async def test_post_form(session):
    async with session.post("/post_form", data={"message": "hello world"}) as resp:
        assert (await resp.json()) == {"broadcast": "hello world"}


async def test_app_failure_handled(session):
    async with session.get("/fail?handle=true") as resp:
        assert await resp.json()


async def test_app_failure_propagate(session):
    with pytest.raises(Exception, match="something bad happened"):
        async with session.get("/fail?handle=false") as resp:
            assert await resp.json()
