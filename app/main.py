"""Application entry point."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.approvals.routes import router as approval_router
from app.callbacks.routes import router as callback_router
from app.config import get_settings
from app.logging.config import configure_logging
from app.observability.health import router as observability_router
from app.observability.tracing import CORRELATION_ID_HEADER, correlation_context

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


@app.middleware("http")
async def correlation_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach a correlation ID to each request and response."""

    inbound_correlation_id = request.headers.get(CORRELATION_ID_HEADER)
    with correlation_context(inbound_correlation_id) as correlation_id:
        response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


app.include_router(callback_router)
app.include_router(approval_router)
app.include_router(observability_router)
