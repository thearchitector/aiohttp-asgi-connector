from asyncio import Event, Queue, Transport, create_task, gather, sleep
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
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
        *,
        propagate_exceptions: bool = True,
    ) -> None:
        super().__init__()
        self.protocol = protocol
        self.app = app
        self.root_path = root_path
        self.request = request
        self.propagate_exceptions = propagate_exceptions
        self._request_buffer: List[bytes] = []
        self._closing: bool = False
        self._handler: Optional[Task[None]] = None

    def schedule_handler(self) -> None:
        # rather than await the request directly, schedule it onto the event loop. this
        # better mimics a third party remote, and somehow also ensures we process chunks
        # properly
        self._handler = create_task(self._handle_request())

    async def _handle_request(self) -> None:
        scope: Dict[str, Any] = {
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

        # skip processing the HTTP message headers, but keep coalesced chunks if they're
        # in the buffer
        coalesced_chunks = self._request_buffer.pop(0).split(b"\r\n\r\n")[1:]
        request_chunks: Iterator[bytes] = iter(coalesced_chunks + self._request_buffer)
        request_received: Event = Event()

        is_chunked: bool = False
        response_payload_queue: Queue[Optional[bytes]] = Queue()
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
                payload = f"{status_line}\r\n{header_line}\r\n\r\n".encode()

                # if we're streaming, or we don't have to capture application exceptions,
                # send the payload immediately
                if is_chunked or not self.propagate_exceptions:
                    response_payload_queue.put_nowait(payload)
                else:
                    response_body.extend(payload)
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and self.request.method != "HEAD":
                    if is_chunked:
                        response_payload_queue.put_nowait(
                            b"%X\r\n" % len(body) + body + b"\r\n"
                        )
                    elif not self.propagate_exceptions:
                        response_payload_queue.put_nowait(body)
                    else:
                        response_body.extend(body)

                more_body = message.get("more_body", False)
                if not more_body:
                    response_sent.set()
                    response_payload_queue.put_nowait(None)

        async def stream_response() -> None:
            while True:
                chunk = await response_payload_queue.get()
                if chunk is None:
                    break

                await self.write_chunk(chunk)

        try:
            # process the request. if the response is chunked, each chunk is sent as it
            # it processed. otherwise the chunks are buffered and sent once the response
            # is complete, unless exception propagation is disabled
            await gather(self.app(scope, receive, send), stream_response())

            # send the last chunk, or the entire payload if the request was not chunked.
            # this is here mainly for simplicity; if `propagate_exceptions`, we have to wait
            # until `app` returns finalizing the stream so that we can catch any exception
            # and pass it through the protocol. if we didn't, we'd send the full response to
            # the client before we could catch the exception, which would mean clients receiving
            # HTTP 500s instead. the simplification here is ALSO waiting for chunked responses
            # even if propagate is disabled; this defers stream finalization until the app is also
            # done, which is unnecesary, but I opted for it in favor of sending the terminal chunk
            # frame in only one place.
            if is_chunked or self.propagate_exceptions:
                await self.write_chunk(
                    b"0\r\n\r\n" if is_chunked else bytes(response_body)
                )
        except Exception as e:  # noqa: BLE001 - forward application errors to the client
            self.protocol.set_exception(e)
            # ensure the streaming task is cleaned up
            response_payload_queue.put_nowait(None)
        finally:
            # release the task to the GC
            self._handler = None

    async def write_chunk(self, data: bytes) -> None:
        self.protocol.data_received(data)
        await sleep(0)  # yield to ensure the session processes the incoming chunks

    def write(self, data: "Union[bytes, bytearray, memoryview]") -> None:
        self._request_buffer.append(cast(bytes, data))

    def close(self) -> None:
        if self._closing:
            return

        self._closing = True
        # delay closing the connection just as a precaution against triggering a premature EOF while aiohttp is still reading data
        self.request.loop.call_soon(self.protocol.connection_lost, None)

    def is_closing(self) -> bool:
        return self._closing
