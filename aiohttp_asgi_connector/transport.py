from asyncio import Event, Queue, QueueEmpty, Transport, create_task, gather, sleep
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:  # pragma: no cover
    from asyncio import Task
    from collections.abc import Awaitable, Callable, Coroutine, Iterator, MutableMapping
    from typing import Any, Dict, List, Optional, Union

    from aiohttp import ClientRequest
    from aiohttp.client_proto import ResponseHandler

    Application = Callable[
        [
            Dict[str, Any],
            Callable[[], Awaitable[Dict[str, Any]]],
            Callable[[MutableMapping[str, Any]], Awaitable[None]],
        ],
        Coroutine[Any, Any, None],
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
        self._handler: "Optional[Task[None]]" = None

    def schedule_handler(self) -> None:
        # rather than await the request directly, schedule it onto the event loop. this
        # better mimics a third party remote, and somehow also ensures we process chunks
        # properly
        self._handler = create_task(self._handle_request())

    async def _handle_request(self) -> None:
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

        # skip processing the HTTP message headers, but keep coaleced chunks if they're
        # in the buffer
        coalesced_chunks = self._request_buffer.pop(0).split(b"\r\n\r\n")[1:]
        request_chunks: "Iterator[bytes]" = iter(
            coalesced_chunks + self._request_buffer
        )
        request_received: Event = Event()

        is_chunked: bool = False
        response_payload_queue: "Queue[bytes]" = Queue()
        response_body = bytearray()
        response_sent: Event = Event()

        async def receive() -> "Dict[str, Any]":
            if request_received.is_set():
                await response_sent.wait()
                return {"type": "http.disconnect"}

            try:
                body = next(request_chunks)
                return {"type": "http.request", "body": body, "more_body": True}
            except StopIteration:
                request_received.set()
                return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: "MutableMapping[str, Any]") -> None:
            nonlocal is_chunked

            if message["type"] == "http.response.start":
                status = message["status"]
                headers = message.get("headers", [])

                # if there is no content length, we're streaming back a response in
                # chunks
                if is_chunked := not any(
                    b"content-length" in h.lower() for h, _ in headers
                ):
                    headers.append((b"Transfer-Encoding", b"chunked"))

                status_line = f"HTTP/1.1 {status} {STATUS_CODE_TO_REASON[status]}"
                header_line = "\r\n".join(
                    f"{name.decode()}: {value.decode()}" for name, value in headers
                )

                await response_payload_queue.put(
                    f"{status_line}\r\n{header_line}\r\n\r\n".encode()
                )
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and self.request.method != "HEAD":
                    await response_payload_queue.put(body)

                more_body = message.get("more_body", False)
                if not more_body:
                    response_sent.set()

        async def stream_or_buffer_response() -> None:
            is_body: bool = False

            while not response_sent.is_set() or not response_payload_queue.empty():
                try:
                    response_body.extend(response_payload_queue.get_nowait())
                except QueueEmpty:
                    await sleep(0)  # yield to allow the response event to be set
                    continue

                # the first message are the http headers, so we know this flag will be
                # accurate
                if is_chunked:
                    chunk = bytes(response_body)

                    if is_body:
                        await self.write_chunk(f"{len(chunk):X}\r\n".encode())
                        await self.write_chunk(chunk)
                        await self.write_chunk(b"\r\n")
                    else:
                        # do not chunk the HTTP headers
                        await self.write_chunk(chunk)
                        is_body = True

                    response_body.clear()

        try:
            # process the request. if the response is chunked, each chunk is sent as it
            # it processed. otherwise the chunks are buffered and sent once the response
            # is complete
            await gather(self.app(scope, receive, send), stream_or_buffer_response())

            # send the last chunk, or the entire payload if the request was not chunked
            await self.write_chunk(b"0\r\n\r\n" if is_chunked else bytes(response_body))
        except Exception as e:
            self.protocol.set_exception(e)

    async def write_chunk(self, data: bytes) -> None:
        self.protocol.data_received(data)
        await sleep(0)  # yield to ensure the session processes the incoming chunks

    def write(self, data: "Union[bytes, bytearray, memoryview]") -> None:
        self._request_buffer.append(cast(bytes, data))

    def close(self) -> None:
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing
