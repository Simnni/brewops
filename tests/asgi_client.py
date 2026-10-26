"""Minimal ASGI test client (stdlib only — the repo deliberately has no httpx,
so starlette's TestClient is not available)."""

import asyncio
import json
from typing import Any


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode()


def request(app, method: str, path: str, body: dict | None = None) -> Response:
    payload = json.dumps(body).encode() if body is not None else b""
    path, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    received = {"sent": False}
    messages: list[dict] = []

    async def receive():
        if received["sent"]:
            return {"type": "http.disconnect"}
        received["sent"] = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    status = 500
    headers: dict[str, str] = {}
    chunks: list[bytes] = []
    for m in messages:
        if m["type"] == "http.response.start":
            status = m["status"]
            headers = {k.decode(): v.decode() for k, v in m.get("headers", [])}
        elif m["type"] == "http.response.body":
            chunks.append(m.get("body", b""))
    return Response(status, headers, b"".join(chunks))
