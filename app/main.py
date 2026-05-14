"""Application entry point."""

from fastapi import FastAPI

from app.callbacks.routes import router as callback_router
from app.observability.health import router as observability_router

app = FastAPI(title="M-Pesa MCP Server")
app.include_router(callback_router)
app.include_router(observability_router)
