"""Application entry point."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.approvals.routes import router as approval_router
from app.callbacks.routes import get_app_container, initialized_app_container
from app.callbacks.routes import router as callback_router
from app.config import get_settings
from app.config_validation import StartupConfigValidationError
from app.infrastructure.health import InfrastructureUnavailableError
from app.logging.config import configure_logging
from app.observability.health import router as observability_router
from app.observability.tracing import CORRELATION_ID_HEADER, correlation_context
from app.operator.routes import router as operator_router
from app.operator.ui import router as operator_ui_router
from app.startup import enforce_startup_safety

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration and dependencies, then build services, before serving.

    Running ``uvicorn app.main:app`` directly goes through this lifespan. If validation
    fails, startup aborts and the server never accepts connections. When the lifespan
    is disabled (``--lifespan off``) or unsupported, ``startup_safety_gate`` below and
    the validated container factory still refuse every request.
    """

    settings = get_settings()
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    enforce_startup_safety(settings)
    get_app_container()
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
async def startup_safety_gate(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Refuse every HTTP request until the validated application container exists.

    This protects all routes, including ones that never use the container (the HTML
    operator UI, OpenAPI docs), when the ASGI lifespan did not run. It resolves the
    same container provider FastAPI routes use, so a dependency override (tests) is
    honoured and the production path always goes through the validated factory. Network
    dependency checks are never repeated here.

    Fast path: once a validated container exists and no override is active, the
    request proceeds without a threadpool hop. A container only exists after successful
    validation, so this cannot admit an unvalidated configuration; overrides, cached
    rejections, and the first (possibly concurrent) requests take the full path.
    """

    override = request.app.dependency_overrides.get(get_app_container)
    if override is None and initialized_app_container() is not None:
        return await call_next(request)

    provider = override or get_app_container
    try:
        await run_in_threadpool(provider)
    except StartupConfigValidationError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "service_unavailable",
                "success": False,
                "reason": "Service configuration failed startup safety validation.",
            },
        )
    return await call_next(request)


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
