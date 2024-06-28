async def test_bad_method(session):
    async with session.post("/ping") as resp:
        assert (await resp.json()) == {"detail": "Method Not Allowed"}


async def test_simple_get(session):
    async with session.get("/ping") as resp:
        assert (await resp.json()) is True


async def test_simple_post(session):
    async with session.post("/post_ping", json={"message": "hello world"}) as resp:
        assert (await resp.json()) == {"broadcast": "hello world"}
