import warnings
from asyncio import create_task
from typing import TYPE_CHECKING, Any, cast

from aiohttp import BaseConnector, ClientRequest
from aiohttp.http import StreamWriter

from .transport import ASGITransport

if TYPE_CHECKING:  # pragma: no cover
    from asyncio import AbstractEventLoop
    from typing import List, Optional

    from aiohttp import ClientTimeout
    from aiohttp.abc import AbstractStreamWriter
    from aiohttp.client_proto import ResponseHandler
    from aiohttp.connector import Connection
    from aiohttp.tracing import Trace

    from .transport import Application


async def _write_bytes_dispatch(
    req: "ClientRequest", writer: "AbstractStreamWriter", conn: "Connection"
) -> None:
    writer = cast("StreamWriter", writer)

    if writer.chunked:
        warnings.warn(
            "Chunking direct ASGI requests has no effect. To avoid confusion,"
            " disabling it is recommended.",
            stacklevel=0,
        )

    if writer._compress:
        warnings.warn(
            "Compressing direct ASGI requests has no effect.  To avoid confusion,"
            " disabling it is recommended.",
            stacklevel=0,
        )

    # we've hit EOF. schedule the request for processing. we have to save this
    # to a task since the event loop only holds weak refs and we don't want to
    # GC in the middle of an execution
    protocol = cast("ResponseHandler", conn.protocol)
    transport = cast(ASGITransport, protocol.transport)
    task = create_task(transport.handle_request())
    req._request_handler = task  # type: ignore[attr-defined]

    return await ClientRequest.write_bytes(req, writer, conn)


class ASGIApplicationConnector(BaseConnector):
    """
    A Connector that replaces the underlying connection transport with one that
    intercepts and runs the provided ASGI application.

    Since requests are handled by the ASGI application directly, there is no concept of
    connection pooling with this connector; every request is processed immediately.

    Exceptions raised within the ASGI application that are not handled by the ASGI
    application are reraised, since translating an error into a HTTP payload is not
    generalizable across all expectations.

    @param 'root_path' [""]: alters the root path of the constructed ASGI request scope.
    """

    def __init__(
        self,
        application: "Application",
        root_path: str = "",
        loop: "Optional[AbstractEventLoop]" = None,
    ) -> None:
        super().__init__(loop=loop)
        self.app = application
        self.root_path = root_path

    async def _create_connection(
        self, req: "ClientRequest", traces: "List[Trace]", timeout: "ClientTimeout"
    ) -> "ResponseHandler":
        protocol: "ResponseHandler" = self._factory()
        transport = ASGITransport(protocol, self.app, req, self.root_path)
        req.write_bytes = _write_bytes_dispatch.__get__(req)  # type: ignore[method-assign]
        protocol.connection_made(transport)
        return protocol

    def _available_connections(self, *args: "Any", **kwargs: "Any") -> int:
        return 1

    async def _get(self, *args: "Any", **kwargs: "Any") -> None:
        return None
