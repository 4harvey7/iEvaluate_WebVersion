"""Pure-ASGI request body cap. Runs before routing, so oversized bodies are
rejected with 413 before JSON parsing and before station-key / bearer auth."""
from __future__ import annotations

import json

INGEST_PATH = "/scans/ingest"
INGEST_MAX_BYTES = 17 * 1024 * 1024       # 12 MB image, base64-encoded (~16 MB) + JSON
DEFAULT_MAX_BYTES = 1 * 1024 * 1024       # every other endpoint takes small JSON


def limit_for(path: str) -> int:
    return INGEST_MAX_BYTES if path.rstrip("/") == INGEST_PATH else DEFAULT_MAX_BYTES


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS", "DELETE"):
            return await self.app(scope, receive, send)

        limit = limit_for(scope.get("path", ""))

        # 1. Declared size.
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    return await self._reject(send, 400, "invalid_content_length", "Invalid Content-Length header.")
                if declared > limit:
                    return await self._reject(send, 413, "payload_too_large", f"Request body exceeds {limit // (1024 * 1024)} MB.")
                break

        # 2. Actual size (chunked uploads / lying Content-Length): buffer up to
        #    the limit, then replay to the app.
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > limit:
                return await self._reject(send, 413, "payload_too_large", f"Request body exceeds {limit // (1024 * 1024)} MB.")
            chunks.append(body)
            if not message.get("more_body", False):
                break

        data = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send, status: int, code: str, message: str):
        body = json.dumps({"detail": {"code": code, "message": message}}).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")],
        })
        await send({"type": "http.response.body", "body": body})
