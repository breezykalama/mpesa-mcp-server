"""Application dependency container."""

from __future__ import annotations

from dataclasses import dataclass

from app.analytics.service import AnalyticsService
from app.approvals.repository import InMemoryApprovalRepository
from app.approvals.service import ApprovalService
from app.audit.logger import InMemoryAuditLogger
from app.audit.repository import (
    AuditRepositoryProtocol,
    InMemoryAuditRepository,
    PostgresAuditRepository,
)
from app.callbacks.replay import (
    InMemoryReplayProtection,
    RedisReplayProtection,
    ReplayProtectionProtocol,
)
from app.config import Settings, get_settings
from app.daraja.client import DarajaClientProtocol, MockDarajaClient, RealDarajaClient
from app.observability.metrics import InMemoryMetricsRecorder
from app.policy.tool_policy import ToolPolicyEngine
from app.rate_limit.limiter import InMemoryRateLimiter, RateLimiterProtocol, RedisRateLimiter
from app.receipts.generator import ReceiptGenerator
from app.safety.policy import PaymentPolicy
from app.services.payment_service import PaymentService
from app.services.receipt_service import ReceiptService
from app.services.transaction_service import TransactionService
from app.storage.database import SessionFactory, create_database_engine, create_session_factory
from app.storage.repositories import (
    InMemoryTransactionRepository,
    PostgresTransactionRepository,
    TransactionRepositoryProtocol,
)

DEFAULT_MOCK_DATABASE_URL = "postgresql+asyncpg://mpesa:mpesa@localhost:5432/mpesa_mcp"


@dataclass(frozen=True)
class AppContainer:
    """Application dependency container."""

    settings: Settings
    payment_policy: PaymentPolicy
    daraja_client: DarajaClientProtocol
    transaction_repository: TransactionRepositoryProtocol
    approval_repository: InMemoryApprovalRepository
    audit_repository: AuditRepositoryProtocol
    audit_logger: InMemoryAuditLogger
    metrics_recorder: InMemoryMetricsRecorder
    tool_policy: ToolPolicyEngine
    rate_limiter: RateLimiterProtocol
    replay_protection: ReplayProtectionProtocol
    receipt_generator: ReceiptGenerator
    payment_service: PaymentService
    approval_service: ApprovalService
    transaction_service: TransactionService
    receipt_service: ReceiptService
    analytics_service: AnalyticsService

    @classmethod
    def mock(cls, settings: Settings | None = None) -> AppContainer:
        """Build a mock-backed container for local development and tests."""

        resolved_settings = settings or Settings(database_url=DEFAULT_MOCK_DATABASE_URL)
        session_factory = cls._create_session_factory(resolved_settings)
        transaction_repository = cls._create_transaction_repository(
            resolved_settings,
            session_factory,
        )
        approval_repository = InMemoryApprovalRepository()
        audit_repository = cls._create_audit_repository(resolved_settings, session_factory)
        audit_logger = InMemoryAuditLogger(repository=audit_repository)
        metrics_recorder = InMemoryMetricsRecorder()
        tool_policy = ToolPolicyEngine.from_settings(resolved_settings)
        rate_limiter = cls._create_rate_limiter(resolved_settings)
        replay_protection = cls._create_replay_protection(resolved_settings)
        daraja_client = cls._create_daraja_client(resolved_settings)
        payment_policy = PaymentPolicy(max_stk_amount=resolved_settings.max_stk_amount)
        receipt_generator = ReceiptGenerator()
        approval_service = ApprovalService(approval_repository=approval_repository)

        return cls(
            settings=resolved_settings,
            payment_policy=payment_policy,
            daraja_client=daraja_client,
            transaction_repository=transaction_repository,
            approval_repository=approval_repository,
            audit_repository=audit_repository,
            audit_logger=audit_logger,
            metrics_recorder=metrics_recorder,
            tool_policy=tool_policy,
            rate_limiter=rate_limiter,
            replay_protection=replay_protection,
            receipt_generator=receipt_generator,
            approval_service=approval_service,
            payment_service=PaymentService(
                policy=payment_policy,
                daraja_client=daraja_client,
                transaction_repository=transaction_repository,
                audit_logger=audit_logger,
                approval_service=approval_service,
                metrics_recorder=metrics_recorder,
            ),
            transaction_service=TransactionService(
                policy=payment_policy,
                daraja_client=daraja_client,
                transaction_repository=transaction_repository,
                audit_logger=audit_logger,
                metrics_recorder=metrics_recorder,
            ),
            receipt_service=ReceiptService(
                transaction_repository=transaction_repository,
                receipt_generator=receipt_generator,
                audit_logger=audit_logger,
                metrics_recorder=metrics_recorder,
            ),
            analytics_service=AnalyticsService(transaction_repository=transaction_repository),
        )

    @classmethod
    def from_environment(cls) -> AppContainer:
        """Build the default application container from environment settings."""

        return cls.mock(settings=get_settings())

    @staticmethod
    def _create_daraja_client(settings: Settings) -> DarajaClientProtocol:
        if settings.daraja_mode == "mock":
            return MockDarajaClient()

        if settings.daraja_mode == "sandbox":
            return RealDarajaClient(settings=settings)

        raise ValueError("DARAJA_MODE must be one of: mock, sandbox.")

    @staticmethod
    def _create_rate_limiter(settings: Settings) -> RateLimiterProtocol:
        if settings.rate_limit_mode == "memory":
            return InMemoryRateLimiter()

        if settings.rate_limit_mode == "redis":
            return RedisRateLimiter(redis_url=settings.redis_url)

        raise ValueError("RATE_LIMIT_MODE must be one of: memory, redis.")

    @staticmethod
    def _create_replay_protection(settings: Settings) -> ReplayProtectionProtocol:
        if settings.callback_replay_mode == "memory":
            return InMemoryReplayProtection()

        if settings.callback_replay_mode == "redis":
            return RedisReplayProtection(redis_url=settings.redis_url)

        raise ValueError("CALLBACK_REPLAY_MODE must be one of: memory, redis.")

    @staticmethod
    def _create_session_factory(settings: Settings) -> SessionFactory | None:
        if settings.storage_mode != "postgres":
            return None

        engine = create_database_engine(settings)
        return create_session_factory(engine)

    @staticmethod
    def _create_transaction_repository(
        settings: Settings,
        session_factory: SessionFactory | None,
    ) -> TransactionRepositoryProtocol:
        if settings.storage_mode == "memory":
            return InMemoryTransactionRepository()

        if settings.storage_mode == "postgres":
            if session_factory is None:
                raise ValueError("Postgres storage requires a session factory.")
            return PostgresTransactionRepository(session_factory)

        raise ValueError("STORAGE_MODE must be one of: memory, postgres.")

    @staticmethod
    def _create_audit_repository(
        settings: Settings,
        session_factory: SessionFactory | None,
    ) -> AuditRepositoryProtocol:
        if settings.storage_mode == "memory":
            return InMemoryAuditRepository()

        if settings.storage_mode == "postgres":
            if session_factory is None:
                raise ValueError("Postgres audit storage requires a session factory.")
            return PostgresAuditRepository(session_factory)

        raise ValueError("STORAGE_MODE must be one of: memory, postgres.")


def create_app_container() -> AppContainer:
    """Create the default application container from environment settings."""

    return AppContainer.from_environment()
