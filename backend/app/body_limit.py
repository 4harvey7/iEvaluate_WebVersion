"""Pure-ASGI request body cap. Runs before routing, so oversized bodies are
rejected with 413 before JSON parsing and before station-key / bearer auth.

For the scan-ingest endpoint (the only one taking big bodies) it also:
  * answers 401 straight away when the X-Station-Key header is missing, so an
    anonymous caller can never make the server buffer a 17 MB body;
  * caps how many ingest calls run at once (INGEST_MAX_CONCURRENCY, default 4).
    Each one holds a decoded page in memory and waits for n8n; when all slots
    are taken the caller gets 503 server_busy with Retry-After, which the
    watcher treats as "try again shortly".
"""
from __future__ import annotations

import json
import threading

INGEST_PATH = "/scans/ingest"
INGEST_MAX_BYTES = 17 * 1024 * 1024       # 12 MB image, base64-encoded (~16 MB) + JSON
DEFAULT_MAX_BYTES = 1 * 1024 * 1024       # every other endpoint takes small JSON
DEFAULT_INGEST_CONCURRENCY = 4
BUSY_RETRY_AFTER_SECONDS = 10


def limit_for(path: str) -> int:
    return INGEST_MAX_BYTES if is_ingest(path) else DEFAULT_MAX_BYTES


def is_ingest(path: str) -> bool:
    return path.rstrip("/") == INGEST_PATH


class ConcurrencyLimiter:
    """Non-blocking counting semaphore (a full server answers 503 instead of queueing)."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.active = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self.active >= self.limit:
                return False
            self.active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self.active = max(0, self.active - 1)


def _configured_ingest_limit() -> int:
    try:
        from app.db import get_settings
        return int(getattr(get_settings(), "ingest_max_concurrency", DEFAULT_INGEST_CONCURRENCY))
    except Exception:  # pragma: no cover - settings missing in odd test setups
        return DEFAULT_INGEST_CONCURRENCY


class BodySizeLimitMiddleware:
    def __init__(self, app, ingest_concurrency: int | None = None):
        self.app = app
        self.ingest_slots = ConcurrencyLimiter(
            ingest_concurrency if ingest_concurrency is not None else _configured_ingest_limit()
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS", "DELETE"):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        limit = limit_for(path)
        headers = scope.get("headers", [])

        # 1. Declared size.
        for name, value in headers:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    return await self._reject(send, 400, "invalid_content_length", "Invalid Content-Length header.")
                if declared > limit:
                    return await self._reject(send, 413, "payload_too_large", f"Request body exceeds {limit // (1024 * 1024)} MB.")
                break

        if not is_ingest(path):
            return await self._buffer_and_call(scope, receive, send, limit)

        # 2. Ingest: no station key -> 401 before reading a single body byte.
        #    (The key itself is checked by require_station after routing.)
        key = next((v for n, v in headers if n == b"x-station-key"), b"").strip()
        if not key:
            return await self._reject(send, 401, "invalid_station_key", "Missing or invalid station key.")

        # 3. Ingest: bounded concurrency.
        if not self.ingest_slots.try_acquire():
            return await self._reject(
                send, 503, "server_busy", "The server is busy with other pages. Will retry shortly.",
                extra_headers=[(b"retry-after", str(BUSY_RETRY_AFTER_SECONDS).encode())],
            )
        try:
            return await self._buffer_and_call(scope, receive, send, limit)
        finally:
            self.ingest_slots.release()

    async def _buffer_and_call(self, scope, receive, send, limit: int):
        # Actual size (chunked uploads / lying Content-Length): buffer up to
        # the limit, then replay to the app.
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
    async def _reject(send, status: int, code: str, message: str, extra_headers: list | None = None):
        body = json.dumps({"detail": {"code": code, "message": message}}).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
                *(extra_headers or []),
            ],
        })
        await send({"type": "http.response.body", "body": body})
