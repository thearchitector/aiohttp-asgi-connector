from typing import TYPE_CHECKING

from aiohttp import BaseConnector

from .transport import ASGITransport

if TYPE_CHECKING:  # pragma: no cover
    from asyncio import AbstractEventLoop
    from typing import List, Optional

    from aiohttp import ClientRequest, ClientTimeout
    from aiohttp.client_proto import ResponseHandler
    from aiohttp.client_reqrep import ConnectionKey
    from aiohttp.tracing import Trace

    from .transport import Application


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
        protocol = self._factory()
        transport = ASGITransport(protocol, self.app, req, self.root_path)
        protocol.connection_made(transport)
        return protocol

    def _available_connections(self, key: "ConnectionKey") -> int:
        return 1

    def _get(self, key: "ConnectionKey") -> None:
        return None
