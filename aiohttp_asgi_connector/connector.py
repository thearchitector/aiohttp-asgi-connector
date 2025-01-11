from typing import TYPE_CHECKING, cast

from aiohttp import BaseConnector, ClientRequest

from .transport import ASGITransport

if TYPE_CHECKING:  # pragma: no cover
    from asyncio import AbstractEventLoop
    from typing import Any, Optional

    from aiohttp import ClientResponse
    from aiohttp.client_proto import ResponseHandler
    from aiohttp.connector import Connection

    from .transport import Application


async def _send_dispatch(req: "ClientRequest", conn: "Connection") -> "ClientResponse":
    response: "ClientResponse" = await type(req).send(req, conn)

    protocol = cast("ResponseHandler", conn.protocol)
    transport = cast(ASGITransport, protocol.transport)
    transport.schedule_handler()

    return response


class ASGIApplicationConnector(BaseConnector):
    """
    A Connector that replaces the underlying connection transport with one that
    intercepts and runs the provided ASGI application.

    Since requests are handled by the ASGI application directly, there is no concept of
    connection pooling with this connector; every request is processed immediately and
    response chunks are streamed as they're produced.

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
        super().__init__(loop=loop, force_close=True)
        self.app = application
        self.root_path = root_path

    async def _create_connection(
        self, req: "ClientRequest", *args: "Any", **kwargs: "Any"
    ) -> "ResponseHandler":
        protocol: "ResponseHandler" = self._factory()
        transport = ASGITransport(protocol, self.app, req, self.root_path)
        req.send = _send_dispatch.__get__(req)  # type: ignore[method-assign]
        protocol.connection_made(transport)
        return protocol
