from typing import TYPE_CHECKING, cast

from aiohttp import BaseConnector, ClientRequest

from .transport import ASGITransport

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop
    from typing import Any, Optional

    from aiohttp import ClientResponse
    from aiohttp.client_proto import ResponseHandler
    from aiohttp.connector import Connection

    from .transport import Application


async def _send_dispatch(req: "ClientRequest", conn: "Connection") -> "ClientResponse":
    response: ClientResponse = await type(req).send(req, conn)

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
    chunked responses are streamed as they're produced.

    @param 'root_path' [""]: alters the root path of the constructed ASGI request scope.
    @param 'propagate_exceptions' [True]: Whether to propagate application exceptions through
        to the client directly, or permit a parent ASGI application (FastAPI) to potentially
        catch and return an HTTP exception instead. When True, this has the side-effect of
        buffering all known-length responses before sending them to the client, including
        streaming responses with an explicit Content-Length header. When set to False,
        responses are streamed as they're produced as expected.
    """

    def __init__(
        self,
        application: "Application",
        root_path: str = "",
        loop: "Optional[AbstractEventLoop]" = None,
        *,
        propagate_exceptions: bool = True,
    ) -> None:
        super().__init__(loop=loop, force_close=True)
        self.app = application
        self.root_path = root_path
        self.propagate_exceptions = propagate_exceptions

    async def _create_connection(
        self,
        req: "ClientRequest",
        *args: "Any",  # noqa: ANN401
        **kwargs: "Any",  # noqa: ANN401
    ) -> "ResponseHandler":
        protocol: ResponseHandler = self._factory()
        transport = ASGITransport(
            protocol,
            self.app,
            req,
            self.root_path,
            propagate_exceptions=self.propagate_exceptions,
        )
        req.send = _send_dispatch.__get__(req)  # type: ignore[method-assign]
        protocol.connection_made(transport)
        return protocol
