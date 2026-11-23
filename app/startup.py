"""Single startup safety gate shared by every serving entry point.

Called by the FastAPI lifespan (so direct ``uvicorn app.main:app`` is covered), by
``scripts/start_app.py`` before migrations, and by the MCP server before it builds its
container. It must run before any payment provider is constructed or contacted.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.config_validation import StartupConfigValidationError, validate_startup_settings
from app.deployment import classify_deployment
from app.infrastructure.health import validate_startup_dependencies

logger = logging.getLogger(__name__)


def enforce_startup_safety(settings: Settings, *, check_dependencies: bool = True) -> None:
    """Fail closed unless the configuration is safe to serve.

    Raises ``StartupConfigValidationError`` for unsafe configuration and
    ``InfrastructureUnavailableError`` when required dependencies are unreachable.
    Only setting names are logged; configured values never are.
    """

    classification = classify_deployment(settings)
    try:
        validate_startup_settings(settings)
    except StartupConfigValidationError as exc:
        logger.error(
            "Startup blocked by unsafe configuration.",
            extra={
                "event_type": "startup_config_rejected",
                "deployment_tier": classification.tier.value,
                "rejected_settings": list(exc.settings),
            },
        )
        raise

    if check_dependencies:
        validate_startup_dependencies(settings)

    logger.info(
        "Startup configuration accepted.",
        extra={
            "event_type": "startup_config_accepted",
            "deployment_tier": classification.tier.value,
        },
    )
