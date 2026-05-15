"""Application entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.callbacks.routes import router as callback_router
from app.config import get_settings
from app.logging.config import configure_logging
from app.observability.health import router as observability_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Configure application services on startup."""

    settings = get_settings()
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    logger.info(
        "FastAPI application started.",
        extra={"event_type": "fastapi_started"},
    )
    yield


app = FastAPI(title="M-Pesa MCP Server", lifespan=lifespan)
app.include_router(callback_router)
app.include_router(observability_router)
