"""Application entry point."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.approvals.routes import router as approval_router
from app.callbacks.routes import router as callback_router
from app.config import get_settings
from app.infrastructure.health import InfrastructureUnavailableError
from app.logging.config import configure_logging
from app.observability.health import router as observability_router
from app.observability.tracing import CORRELATION_ID_HEADER, correlation_context
from app.operator.routes import router as operator_router
from app.operator.ui import router as operator_ui_router

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", CORRELATION_ID_HEADER],
)


@app.exception_handler(InfrastructureUnavailableError)
async def infrastructure_unavailable_handler(
    _request: Request,
    exc: InfrastructureUnavailableError,
) -> JSONResponse:
    """Return a safe service unavailable response for dependency failures."""

    logger.exception(
        "Infrastructure dependency unavailable.",
        extra={"event_type": "infrastructure_unavailable", "status": "unavailable"},
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "infrastructure_unavailable",
            "success": False,
            "reason": str(exc),
        },
    )


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
app.include_router(operator_router)
app.include_router(operator_ui_router)
app.include_router(observability_router)
