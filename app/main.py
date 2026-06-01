from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.config import get_settings
from app.db import initialize_database
from app.errors import unhandled_exception_handler, validation_exception_handler
from app.logging import configure_logging, request_logging_middleware
from app.routers import events, health, stores


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize_database()
        logging.getLogger(__name__).info("api_started", extra={"env": settings.env})
        yield

    app = FastAPI(
        title="Store Intelligence API",
        version="0.1.0",
        description="Event ingestion and live store analytics for CCTV-derived visitor events.",
        lifespan=lifespan,
    )

    app.middleware("http")(request_logging_middleware)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.include_router(events.router)
    app.include_router(stores.router)
    app.include_router(health.router)

    return app


app = create_app()
