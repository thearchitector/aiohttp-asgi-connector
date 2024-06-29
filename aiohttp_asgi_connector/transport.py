from asyncio import Transport
from http import HTTPStatus
from io import BytesIO
from typing import TYPE_CHECKING

from aiohttp import Payload

if TYPE_CHECKING:  # pragma: no cover
    from typing import (
        Any,
        Awaitable,
        Callable,
        Dict,
        Iterator,
        List,
        MutableMapping,
        Tuple,
    )

    from aiohttp import ClientRequest
    from aiohttp.client_proto import ResponseHandler

    Application = Callable[
        [
            Dict[str, Any],
            Callable[[], Awaitable[Dict[str, Any]]],
            Callable[[MutableMapping[str, Any]], Awaitable[None]],
        ],
        Awaitable[None],
    ]

STATUS_CODE_TO_REASON: "Dict[int, str]" = {hs.value: hs.phrase for hs in HTTPStatus}


class ASGITransport(Transport):
    def __init__(
        self,
        protocol: "ResponseHandler",
        app: "Application",
        request: "ClientRequest",
        root_path: str,
    ):
        super().__init__()
        self.protocol = protocol
        self.app = app
        self.root_path = root_path
        self.request = request
        self._closing: bool = False

    async def handle_request(self) -> None:
        scope: "Dict[str, Any]" = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": self.request.method,
            "headers": [
                (k.lower().encode(), v.encode())
                for k, v in self.request.headers.items()
            ],
            "scheme": self.request.url.scheme,
            "path": self.request.url.path,
            "raw_path": self.request.url.raw_path.split("?")[0].encode(),
            "query_string": self.request.url.raw_query_string.encode(),
            "server": (self.request.url.host, self.request.url.port),
            "client": ("127.0.0.1", 123),
            "root_path": self.root_path,
        }

        payload = BytesIO()

        if isinstance(self.request.body, Payload):

            class FalseWriter:
                async def write(self, chunk: bytes) -> None:
                    payload.write(chunk)

            await self.request.body.write(FalseWriter())  # type: ignore
        elif isinstance(self.request.body, tuple):
            for chunk in self.request.body:
                payload.write(chunk)
        else:
            payload.write(self.request.body)

        request_body_chunks: "Iterator[bytes]" = iter([payload.getvalue()])

        status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR.value
        response_headers: "List[Tuple[bytes, bytes]]" = []
        response_body: bytearray = bytearray()

        async def receive() -> "Dict[str, Any]":
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
        except Exception as e:
            self.protocol.set_exception(e)

        response_payload = self._encode_response(
            status_code, response_headers, response_body
        )
        self.protocol.data_received(response_payload)

    def _encode_response(
        self, status: int, headers: "List[Tuple[bytes, bytes]]", body: bytearray
    ) -> bytes:
        status_line = f"HTTP/1.1 {status} {STATUS_CODE_TO_REASON[status]}"
        header_line = "\r\n".join(
            f"{name.decode()}: {value.decode()}" for name, value in headers
        )
        response = f"{status_line}\r\n{header_line}\r\n\r\n{body.decode()}"
        return response.encode()

    def write(self, data: bytes) -> None:
        # we don't care about the data as it was serialized by aiohttp; we don't want to
        # have to dechunk or decompress data in order to pass it to the ASGI application
        pass

    def close(self) -> None:
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing
