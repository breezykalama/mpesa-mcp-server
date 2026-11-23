"""Authoritative deployment safety classification.

Every security-critical decision about whether a configuration may use development
shortcuts, or must meet production-grade controls, is derived from
``classify_deployment``. Classification does not rely on ``APP_ENV`` alone:

- ``DARAJA_MODE=production`` always requires production-grade controls, whatever
  ``APP_ENV`` says.
- Daraja endpoints are an allowlist (``app.daraja.configuration``). A sandbox or
  production base URL that is not the official endpoint for its mode is a
  contradiction, and is treated as real-money capable and production-grade: a custom
  gateway, proxy, IP literal, or host alias can never earn development shortcuts.
- ``APP_ENV=production`` requires production-grade controls even with mock or sandbox
  Daraja.
- Missing, unknown, or contradictory values fail closed: they are reported as
  contradictions and classified as production-grade for any runtime decision.

There are deliberately only three tiers, matching how the repository already uses
``APP_ENV``: local development, non-production testing (``staging``), and production.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config import Settings
from app.daraja.configuration import (
    BASE_URL_SETTINGS,
    OFFICIAL_DARAJA_BASE_URLS,
    trusted_base_url,
)

DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "dev", "local", "test"})
NON_PRODUCTION_ENVIRONMENTS = frozenset({"staging"})
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})
RECOGNIZED_ENVIRONMENTS = (
    DEVELOPMENT_ENVIRONMENTS | NON_PRODUCTION_ENVIRONMENTS | PRODUCTION_ENVIRONMENTS
)

DARAJA_MODES = frozenset({"mock", "sandbox", "production"})


class DeploymentTier(StrEnum):
    """Safety tier that determines which controls a configuration must meet."""

    DEVELOPMENT = "development"
    NON_PRODUCTION = "non_production"
    PRODUCTION = "production"


@dataclass(frozen=True)
class DeploymentContradiction:
    """A missing, unknown, or contradictory classification input."""

    setting: str
    reason: str


@dataclass(frozen=True)
class DeploymentClassification:
    """Result of classifying a configuration."""

    tier: DeploymentTier
    real_money_capable: bool
    app_env: str | None
    contradictions: tuple[DeploymentContradiction, ...]

    @property
    def requires_production_controls(self) -> bool:
        """Return whether production-grade controls are mandatory."""

        return self.tier is DeploymentTier.PRODUCTION

    @property
    def allows_development_shortcuts(self) -> bool:
        """Return whether development-only shortcuts may be used.

        Only a contradiction-free development classification qualifies.
        """

        return self.tier is DeploymentTier.DEVELOPMENT and not self.contradictions


def classify_deployment(settings: Settings) -> DeploymentClassification:
    """Classify a configuration into a deployment safety tier."""

    contradictions: list[DeploymentContradiction] = []
    app_env = _normalized(settings.app_env)
    daraja_mode = _normalized(settings.daraja_mode)

    if daraja_mode not in DARAJA_MODES:
        contradictions.append(
            DeploymentContradiction("DARAJA_MODE", "must be one of mock, sandbox, production")
        )

    untrusted_endpoint = (
        daraja_mode in OFFICIAL_DARAJA_BASE_URLS and trusted_base_url(settings) is None
    )
    if untrusted_endpoint and daraja_mode is not None:
        contradictions.append(
            DeploymentContradiction(
                BASE_URL_SETTINGS[daraja_mode],
                f"must be the official Daraja {daraja_mode} endpoint "
                f"{OFFICIAL_DARAJA_BASE_URLS[daraja_mode]}; custom gateways, proxies, "
                "IP literals, ports, userinfo, and non-https URLs are not supported "
                "(use DARAJA_MODE=mock for local stubs)",
            )
        )

    # An untrusted endpoint might reach production infrastructure, so it is treated as
    # real-money capable (fail closed) and can never receive development shortcuts.
    real_money_capable = daraja_mode == "production" or untrusted_endpoint

    if app_env is None:
        if daraja_mode != "mock":
            contradictions.append(
                DeploymentContradiction(
                    "APP_ENV",
                    "must be set explicitly unless DARAJA_MODE=mock",
                )
            )
    elif app_env not in RECOGNIZED_ENVIRONMENTS:
        contradictions.append(
            DeploymentContradiction(
                "APP_ENV",
                "must be one of development, dev, local, test, staging, production, prod",
            )
        )

    if daraja_mode == "production" and app_env is not None and app_env not in (
        PRODUCTION_ENVIRONMENTS
    ):
        contradictions.append(
            DeploymentContradiction(
                "APP_ENV",
                "must be production when DARAJA_MODE=production",
            )
        )

    tier = _tier(
        app_env=app_env,
        daraja_mode=daraja_mode,
        real_money_capable=real_money_capable,
    )
    return DeploymentClassification(
        tier=tier,
        real_money_capable=real_money_capable,
        app_env=app_env,
        contradictions=tuple(contradictions),
    )


def _tier(
    *,
    app_env: str | None,
    daraja_mode: str | None,
    real_money_capable: bool,
) -> DeploymentTier:
    # Fail closed: anything real-money capable, explicitly production, or not
    # positively recognized requires production-grade controls.
    if real_money_capable or daraja_mode not in DARAJA_MODES:
        return DeploymentTier.PRODUCTION
    if app_env is not None and app_env not in RECOGNIZED_ENVIRONMENTS:
        return DeploymentTier.PRODUCTION
    if app_env in PRODUCTION_ENVIRONMENTS:
        return DeploymentTier.PRODUCTION
    if app_env in NON_PRODUCTION_ENVIRONMENTS:
        return DeploymentTier.NON_PRODUCTION
    if app_env in DEVELOPMENT_ENVIRONMENTS:
        return DeploymentTier.DEVELOPMENT
    # APP_ENV unset: only mock Daraja may be treated as local development; the
    # contradiction recorded above prevents any other combination from starting.
    if daraja_mode == "mock":
        return DeploymentTier.DEVELOPMENT
    return DeploymentTier.NON_PRODUCTION


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().lower()
    return stripped or None
