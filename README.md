# aiohttp-asgi-connector

![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/thearchitector/aiohttp-asgi-connector/CI.yaml?label=tests&style=flat-square)
![PyPI - Downloads](https://img.shields.io/pypi/dw/aiohttp-asgi-connector?style=flat-square)
![GitHub](https://img.shields.io/github/license/thearchitector/aiohttp-asgi-connector?style=flat-square)

AIOHTTP Connector for running ASGI applications.

This library intends to increase the parity between AIOHTTP and HTTPX, specifically with HTTPX's `AsyncClient`. It is primarily intended to be used in test suite scenarios, or other situations where one would want to interface with an ASGI application directly instead of through a web server.

Supports Python 3.8+ and AIOHTTP 3+.

## Installation

```sh
$ pdm add aiohttp-asgi-connector
# or
$ python -m pip install --user aiohttp-asgi-connector
```

## Usage

This library replaces the entire connection stack and pool underlying HTTP transport. AIOHTTP exposes custom connectors via the `connector` argument supplied when creating a `ClientSession` instance.

To use the `ASGIApplicationConnector`:

```py
import asyncio
from typing import Annotated  # or from typing_extensions

from aiohttp_asgi_connector import ASGIApplicationConnector
from aiohttp import ClientSession
from fastapi import FastAPI, Body

app = FastAPI()

@app.post("/ping")
async def pong(message: Annotated[str, Body(embed=True)]):
    return {"broadcast": f"Application says '{message}'!"}

async def main():
    connector = ASGIApplicationConnector(app)
    async with ClientSession(base_url="http://localhost", connector=connector) as session:
        async with session.post("/ping", json={"message": "hello"}) as resp:
            print(await resp.json())
            # ==> {'broadcast': "Application says 'hello'!"}

asyncio.run(main())
```

Exceptions raised within the ASGI application that are not handled by the application, like `HTTPException` in FastAPI for example, are propagated.

This library does not handle ASGI lifespan events. If you want to run those events, use this library in conjunction with something like [asgi-lifespan](https://pypi.org/project/asgi-lifespan/).

## License

This software is licensed under the [BSD 3-Clause License](LICENSE).
