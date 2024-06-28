from asyncio import Transport, create_task
from io import BytesIO
from typing import TYPE_CHECKING

from aiohttp import BaseConnector, Payload

if TYPE_CHECKING:  # pragma: no cover
    from asyncio import AbstractEventLoop
    from typing import Any, Awaitable, Callable, MutableMapping

    from aiohttp import ClientRequest, ClientTimeout
    from aiohttp.client_proto import ResponseHandler
    from aiohttp.client_reqrep import ConnectionKey
    from aiohttp.tracing import Trace

    Application = Callable[
        [
            dict[str, Any],
            Callable[[], Awaitable[dict[str, Any]]],
            Callable[[MutableMapping[str, Any]], Awaitable[None]],
        ],
        Awaitable[None],
    ]

STATUS_CODE_TO_REASON: dict[int, str] = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


class ASGITransport(Transport):
    def __init__(
        self,
        protocol: "ResponseHandler",
        app: "Application",
        request: "ClientRequest",
        root_path: str = "",
        raise_app_exceptions: bool = True,
    ):
        super().__init__()
        self.protocol = protocol
        self.app = app
        self.root_path = root_path
        self.raise_app_exceptions = raise_app_exceptions

        self.request = request
        self.request_size = -1
        self.request_handler = None

        self._closing = False

    async def handle_request(self) -> None:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": self.request.method,
            "headers": [(k.lower(), v) for (k, v) in self.request.headers.items()],
            "scheme": self.request.url.scheme,
            "path": self.request.url.path,
            "raw_path": self.request.url.raw_path.split("?")[0],
            "query_string": self.request.url.query,
            "server": (self.request.url.host, self.request.url.port),
            "client": ("127.0.0.1", 123),
            "root_path": self.root_path,
        }

        payload = BytesIO()

        if isinstance(self.request.body, Payload):

            class FalseWriter:
                async def write(self, chunk: bytes):
                    payload.write(chunk)

            await self.request.body.write(FalseWriter())  # type: ignore
        elif isinstance(self.request.body, tuple):
            for chunk in self.request.body:
                payload.write(chunk)
        else:
            payload.write(self.request.body)

        request_body_chunks = iter([payload.getvalue()])
        status_code: int | None = None
        response_headers: list[tuple[bytes, bytes]] | None = None
        response_body = bytearray()

        async def receive() -> dict[str, "Any"]:
            try:
                body = next(request_body_chunks)
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": body, "more_body": True}

        async def send(message: "MutableMapping[str, Any]") -> None:
            nonlocal status_code, response_headers

            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and self.request.method != "HEAD":
                    response_body.extend(body)

        try:
            await self.app(scope, receive, send)
        except Exception:
            if self.raise_app_exceptions:
                raise

        if status_code is None:
            status_code = 500
        if response_headers is None:
            response_headers = []

        response_payload = self.encode_response(
            status_code, response_headers, response_body
        )
        self.protocol.data_received(response_payload)

    def encode_response(
        self, status: int, headers: list[tuple[bytes, bytes]], body: bytearray
    ) -> bytes:
        status_line = (
            f"HTTP/1.1 {status} {STATUS_CODE_TO_REASON.get(status, 'Unknown')}"
        )
        header_line = "\r\n".join(
            f"{name.decode()}: {value.decode()}" for name, value in headers
        )
        response = f"{status_line}\r\n{header_line}\r\n\r\n{body.decode()}"
        return response.encode()

    def write(self, data: bytes) -> None:
        if self.request_size == -1:
            self.request_size = 0
            if size := getattr(self.request.body, "size", None):
                self.max_request_size = size
            else:
                self.max_request_size = len(self.request.body)
        else:
            self.request_size += len(data)

        if self.request_size == self.max_request_size:
            # schedule the request for processing. we have to save this to a task since
            # the event loop only holds weak refs and we don't want to GC in the middle
            # of an execution
            task = create_task(self.handle_request())
            self.request_handler = task
            task.add_done_callback(lambda _: setattr(self, "request_handler", None))

    def close(self) -> None:
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing


class ASGIApplicationConnector(BaseConnector):
    def __init__(
        self,
        application: "Application",
        loop: "AbstractEventLoop | None" = None,
        **kwargs: "Any",
    ) -> None:
        super().__init__(loop=loop)
        self.app = application
        self.kwargs = kwargs

    def _available_connections(self, key: "ConnectionKey") -> int:
        return 1

    def _get(self, key: "ConnectionKey"):
        return None

    async def _create_connection(
        self, req: "ClientRequest", traces: "list[Trace]", timeout: "ClientTimeout"
    ) -> "ResponseHandler":
        protocol = self._factory()
        transport = ASGITransport(protocol, self.app, req, **self.kwargs)
        protocol.connection_made(transport)
        return protocol
