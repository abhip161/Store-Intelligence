from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    request.state.trace_id = trace_id
    started = time.perf_counter()
    status_code = 500
    event_count = None

    try:
        if request.url.path == "/events/ingest":
            body = await request.body()
            # Rehydrate body for FastAPI after reading it for logging.
            async def receive() -> dict[str, object]:
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = receive  # noqa: SLF001 - FastAPI-compatible body replay.
            event_count = body.count(b"event_id")
        response = await call_next(request)
        status_code = response.status_code
        response.headers["x-trace-id"] = trace_id
        return response
    finally:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        store_id = request.path_params.get("store_id") or request.path_params.get("id")
        logging.getLogger("app.requests").info(
            "request_completed",
            extra={
                "trace_id": trace_id,
                "store_id": store_id,
                "endpoint": request.url.path,
                "latency_ms": latency_ms,
                "event_count": event_count,
                "status_code": status_code,
            },
        )

