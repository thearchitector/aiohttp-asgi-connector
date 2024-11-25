from asyncio import Event, Transport
from http import HTTPStatus
from typing import TYPE_CHECKING

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
        self._request_buffer: "List[bytes]" = []
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

        request_chunks: "Iterator[bytes]" = iter(self._request_buffer)
        request_received: Event = Event()

        status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR.value
        response_headers: "List[Tuple[bytes, bytes]]" = []
        response_body: "List[bytes]" = []
        response_sent: Event = Event()

        async def receive() -> "Dict[str, Any]":
            if request_received.is_set():
                await response_sent.wait()
                return {"type": "http.disconnect"}

            try:
                body = next(request_chunks)
            except StopIteration:
                request_received.set()
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
                    response_body.append(body)

                more_body = message.get("more_body", False)
                if not more_body:
                    response_sent.set()

        try:
            # skip processing the HTTP message headers
            next(request_chunks)
            await self.app(scope, receive, send)
        except Exception as e:
            self.protocol.set_exception(e)

        response_payload = self._encode_response(
            status_code, response_headers, response_body
        )

        for packet in response_payload:
            self.protocol.data_received(packet)

    def _encode_response(
        self, status: int, headers: "List[Tuple[bytes, bytes]]", body: "List[bytes]"
    ) -> "Iterator[bytes]":
        # if there is no content length, we're streaming back a response in chunks
        if is_chunked := not any(b"content-length" in h.lower() for h, _ in headers):
            headers.append((b"Transfer-Encoding", b"chunked"))

        status_line = f"HTTP/1.1 {status} {STATUS_CODE_TO_REASON[status]}"
        header_line = "\r\n".join(
            f"{name.decode()}: {value.decode()}" for name, value in headers
        )

        yield f"{status_line}\r\n{header_line}\r\n\r\n".encode()

        if is_chunked:
            for chunk in body:
                yield f"{len(chunk):X}\r\n".encode()
                yield chunk
                yield b"\r\n"

            yield b"0\r\n\r\n"
        else:
            yield b"".join(body)

    def write(self, data: bytes) -> None:  # pyright: ignore
        self._request_buffer.append(data)

    def close(self) -> None:
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing
