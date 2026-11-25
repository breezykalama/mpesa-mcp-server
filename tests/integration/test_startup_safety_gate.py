"""Startup enforcement tests for every serving entry point.

Covers the FastAPI lifespan (in-process and a real ``uvicorn app.main:app``
subprocess), MCP server startup (in-process and a real ``scripts/run_mcp_server.py``
subprocess), and ``scripts/start_app.py``. No real Daraja endpoint is ever contacted:
rejected configurations never build a container, and outbound HTTP is trapped.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from app import main as app_main
from app.bootstrap import container as bootstrap_container
from app.bootstrap.container import AppContainer
from app.callbacks import routes as callback_routes
from app.config import Settings
from app.config_validation import APPROVAL_PERSISTENCE_ISSUE, StartupConfigValidationError
from app.main import app
from app.mcp import server as mcp_server
from fastapi.testclient import TestClient
from scripts import start_app
from tests.unit.test_production_config_gate import HARDENED_PRODUCTION, SECRET_STRINGS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNSAFE_PRODUCTION: dict[str, Any] = {
    **HARDENED_PRODUCTION,
    "operator_auth_enabled": False,
    "storage_mode": "memory",
    "rate_limit_mode": "memory",
}
SAFE_DEVELOPMENT: dict[str, Any] = {
    "app_env": "development",
    "daraja_mode": "mock",
    "operator_auth_enabled": False,
}


@pytest.fixture
def outbound_http(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record (and refuse) any outbound HTTP request made through httpx."""

    calls: list[str] = []
    original_send = httpx.Client.send

    def refuse(self: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.url.host == "testserver":
            # In-process requests from FastAPI's TestClient never leave the process.
            return original_send(self, request, **kwargs)
        calls.append(str(request.url))
        raise AssertionError("No outbound HTTP is allowed during startup.")

    monkeypatch.setattr(httpx.Client, "send", refuse)
    return calls


@pytest.fixture
def container_builds(monkeypatch: pytest.MonkeyPatch) -> list[Settings]:
    """Record container construction for the FastAPI and MCP serving paths."""

    built: list[Settings] = []

    def build() -> AppContainer:
        settings = app_main.get_settings()
        built.append(settings)
        return AppContainer.mock(settings=settings)

    monkeypatch.setattr(callback_routes, "_container", None)
    monkeypatch.setattr(callback_routes, "_container_rejection", None)
    monkeypatch.setattr(callback_routes, "create_app_container", build)
    monkeypatch.setattr(mcp_server, "create_app_container", build)
    return built


def use_settings(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> Settings:
    settings = Settings(**kwargs)
    # configure_logging replaces root handlers, which would detach pytest's log capture.
    monkeypatch.setattr(app_main, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(mcp_server, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(app_main, "get_settings", lambda: settings)
    monkeypatch.setattr(bootstrap_container, "get_settings", lambda: settings)
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    monkeypatch.setattr(start_app, "get_settings", lambda: settings)
    return settings


# --- FastAPI lifespan --------------------------------------------------------------


def test_lifespan_rejects_unsafe_configuration_before_serving(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outbound_http: list[str],
    container_builds: list[Settings],
) -> None:
    use_settings(monkeypatch, **UNSAFE_PRODUCTION)

    with caplog.at_level(logging.INFO):
        with pytest.raises(StartupConfigValidationError) as exc_info:
            with TestClient(app):
                pytest.fail("The application must not start serving.")

    rejected = set(exc_info.value.settings)
    assert {"OPERATOR_AUTH_ENABLED", "STORAGE_MODE", "RATE_LIMIT_MODE"} <= rejected
    assert container_builds == []
    assert outbound_http == []
    rejection_logs = [
        r for r in caplog.records if getattr(r, "event_type", None) == "startup_config_rejected"
    ]
    assert len(rejection_logs) == 1
    assert "OPERATOR_AUTH_ENABLED" in rejection_logs[0].rejected_settings  # type: ignore[attr-defined]
    for secret in SECRET_STRINGS:
        assert secret not in caplog.text
        assert secret not in str(exc_info.value)


def test_lifespan_blocks_hardened_production_on_durable_approvals(
    monkeypatch: pytest.MonkeyPatch,
    outbound_http: list[str],
    container_builds: list[Settings],
) -> None:
    use_settings(monkeypatch, **HARDENED_PRODUCTION)

    with pytest.raises(StartupConfigValidationError) as exc_info:
        with TestClient(app):
            pytest.fail("The application must not start serving.")

    assert exc_info.value.settings == (APPROVAL_PERSISTENCE_ISSUE,)
    assert container_builds == []
    assert outbound_http == []


def test_lifespan_validates_before_building_container_for_safe_config(
    monkeypatch: pytest.MonkeyPatch,
    outbound_http: list[str],
    container_builds: list[Settings],
) -> None:
    use_settings(monkeypatch, **SAFE_DEVELOPMENT)
    order: list[str] = []
    real_enforce = app_main.enforce_startup_safety

    def recording_enforce(settings: Settings, **kwargs: Any) -> None:
        order.append("validated")
        real_enforce(settings, **kwargs)

    monkeypatch.setattr(app_main, "enforce_startup_safety", recording_enforce)
    real_build = callback_routes.create_app_container

    def recording_build() -> AppContainer:
        order.append("container_built")
        return real_build()

    monkeypatch.setattr(callback_routes, "create_app_container", recording_build)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert order == ["validated", "container_built"]
    assert outbound_http == []


# --- MCP server ------------------------------------------------------------------------


def test_mcp_server_rejects_unsafe_configuration_before_building_container(
    monkeypatch: pytest.MonkeyPatch,
    outbound_http: list[str],
    container_builds: list[Settings],
) -> None:
    use_settings(monkeypatch, **UNSAFE_PRODUCTION)

    with pytest.raises(StartupConfigValidationError) as exc_info:
        mcp_server.create_mcp_server()

    assert "OPERATOR_AUTH_ENABLED" in exc_info.value.settings
    assert container_builds == []
    assert outbound_http == []


def test_mcp_run_rejects_unsafe_configuration_before_serving(
    monkeypatch: pytest.MonkeyPatch,
    container_builds: list[Settings],
) -> None:
    use_settings(monkeypatch, **UNSAFE_PRODUCTION)
    served: list[bool] = []
    monkeypatch.setattr(mcp_server.FastMCP, "run", lambda self, *a, **k: served.append(True))

    with pytest.raises(StartupConfigValidationError):
        mcp_server.run()

    assert served == []
    assert container_builds == []


def test_mcp_server_builds_after_validation_for_safe_config(
    monkeypatch: pytest.MonkeyPatch,
    container_builds: list[Settings],
) -> None:
    use_settings(monkeypatch, **SAFE_DEVELOPMENT)

    server = mcp_server.create_mcp_server()

    assert server is not None
    assert len(container_builds) == 1


# --- scripts/start_app.py --------------------------------------------------------------


def test_start_app_rejects_unsafe_configuration_before_migrations_and_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_settings(monkeypatch, **UNSAFE_PRODUCTION)
    steps: list[str] = []
    monkeypatch.setattr(start_app, "configure_logging", lambda **kwargs: None)
    monkeypatch.setattr(start_app, "run_migrations_if_needed", lambda s: steps.append("migrate"))
    monkeypatch.setattr(start_app, "start_uvicorn", lambda s: steps.append("uvicorn"))

    with pytest.raises(StartupConfigValidationError):
        start_app.main()

    assert steps == []


# --- Real process startup --------------------------------------------------------------


def isolated_env(**settings: Any) -> dict[str, str]:
    """Return an environment with every application setting replaced by ``settings``.

    Inherited application variables are dropped so a developer's real credentials can
    never reach the subprocess. The production Daraja URL points at a closed local
    port, so even an unexpected provider call could not leave the machine.
    """

    app_keys = {name.upper() for name in Settings.model_fields}
    env = {key: value for key, value in os.environ.items() if key.upper() not in app_keys}
    for name, value in settings.items():
        if value is not None:
            env[name.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
    env["DARAJA_PRODUCTION_BASE_URL"] = "https://127.0.0.1:9"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_process(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_direct_uvicorn_startup_fails_for_unsafe_configuration() -> None:
    result = run_process(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "0"],
        isolated_env(**UNSAFE_PRODUCTION),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "Application startup failed" in output
    assert "OPERATOR_AUTH_ENABLED" in output
    assert "Uvicorn running on" not in output
    [rejection] = [
        json.loads(line)
        for line in output.splitlines()
        if line.startswith("{") and "startup_config_rejected" in line
    ]
    assert rejection["deployment_tier"] == "production"
    assert "OPERATOR_AUTH_ENABLED" in rejection["rejected_settings"]
    assert APPROVAL_PERSISTENCE_ISSUE in rejection["rejected_settings"]
    for secret in SECRET_STRINGS:
        assert secret not in output


def test_mcp_process_startup_fails_for_unsafe_configuration() -> None:
    result = run_process(
        [sys.executable, "scripts/run_mcp_server.py"],
        isolated_env(**UNSAFE_PRODUCTION),
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "StartupConfigValidationError" in output
    assert "OPERATOR_AUTH_ENABLED" in output
    for secret in SECRET_STRINGS:
        assert secret not in output


# --- H1: serving without the ASGI lifespan ----------------------------------------------

SAFE_DEVELOPMENT_SANDBOX_TRICK: dict[str, Any] = {
    "app_env": "development",
    "daraja_mode": "sandbox",
    "daraja_sandbox_base_url": "https://daraja-gateway.internal.test",
    "operator_auth_enabled": False,
}
GENERIC_REJECTION = {
    "status": "service_unavailable",
    "success": False,
    "reason": "Service configuration failed startup safety validation.",
}


@pytest.fixture
def real_factory(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Use the real, validated container factory and count what it does.

    Unlike ``container_builds``, nothing here skips validation. ``attempts`` counts
    factory calls, ``built`` counts containers produced, and ``daraja_clients`` counts
    real Daraja client constructions.
    """

    counts = {"attempts": 0, "built": 0, "daraja_clients": 0}
    monkeypatch.setattr(callback_routes, "_container", None)
    monkeypatch.setattr(callback_routes, "_container_rejection", None)
    real_create = callback_routes.create_app_container

    def counting_create() -> AppContainer:
        counts["attempts"] += 1
        container = real_create()
        counts["built"] += 1
        return container

    monkeypatch.setattr(callback_routes, "create_app_container", counting_create)
    real_client = bootstrap_container.RealDarajaClient

    def counting_client(*args: Any, **kwargs: Any) -> Any:
        counts["daraja_clients"] += 1
        return real_client(*args, **kwargs)

    monkeypatch.setattr(bootstrap_container, "RealDarajaClient", counting_client)
    return counts


NO_LIFESPAN_REQUESTS = (
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("GET", "/operator/transactions"),
    ("GET", "/operator/system/status"),
    ("POST", "/operator/reconciliation/run"),
    ("POST", "/approvals/any-id/approve"),
    ("POST", "/durable-approvals/any-id/approve"),
    ("POST", "/callbacks/mpesa/stk"),
    ("GET", "/operator/ui"),
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
)


@pytest.mark.parametrize(
    "unsafe",
    [UNSAFE_PRODUCTION, SAFE_DEVELOPMENT_SANDBOX_TRICK],
    ids=["production-daraja-auth-disabled", "dev-labelled-sandbox-proxy"],
)
def test_without_lifespan_every_route_refuses_unsafe_configuration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outbound_http: list[str],
    real_factory: dict[str, int],
    unsafe: dict[str, Any],
) -> None:
    use_settings(monkeypatch, **unsafe)
    client = TestClient(app)  # no `with`: the lifespan never runs

    with caplog.at_level(logging.INFO):
        responses = [
            client.request(method, path, json={} if method == "POST" else None)
            for method, path in NO_LIFESPAN_REQUESTS
        ]

    for (method, path), response in zip(NO_LIFESPAN_REQUESTS, responses, strict=True):
        assert response.status_code == 503, (method, path)
        assert response.json() == GENERIC_REJECTION, (method, path)
    assert real_factory["built"] == 0
    assert real_factory["daraja_clients"] == 0
    assert real_factory["attempts"] == 1, "a rejection is cached, not re-validated per request"
    assert outbound_http == []
    for secret in SECRET_STRINGS:
        assert secret not in caplog.text
    with pytest.raises(StartupConfigValidationError):
        callback_routes.get_app_container()


def test_without_lifespan_safe_configuration_serves_and_builds_once(
    monkeypatch: pytest.MonkeyPatch,
    outbound_http: list[str],
    real_factory: dict[str, int],
) -> None:
    use_settings(monkeypatch, **SAFE_DEVELOPMENT)
    client = TestClient(app)

    health = client.get("/health")
    ui = client.get("/operator/ui")
    transactions = client.get("/operator/transactions")

    assert health.status_code == 200
    assert ui.status_code == 200
    assert transactions.status_code == 200
    assert real_factory == {"attempts": 1, "built": 1, "daraja_clients": 0}
    assert outbound_http == []


@pytest.mark.parametrize(
    ("settings", "expected_status", "expected_built"),
    [(SAFE_DEVELOPMENT, 200, 1), (UNSAFE_PRODUCTION, 503, 0)],
    ids=["safe", "unsafe"],
)
def test_concurrent_first_requests_build_at_most_one_container(
    monkeypatch: pytest.MonkeyPatch,
    real_factory: dict[str, int],
    settings: dict[str, Any],
    expected_status: int,
    expected_built: int,
) -> None:
    use_settings(monkeypatch, **settings)
    client = TestClient(app)
    barrier = threading.Barrier(24)
    statuses: list[int] = []
    lock = threading.Lock()

    def first_request() -> None:
        barrier.wait()
        status = client.get("/operator/transactions").status_code
        with lock:
            statuses.append(status)

    threads = [threading.Thread(target=first_request) for _ in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses == [expected_status] * 24
    assert real_factory["attempts"] == 1
    assert real_factory["built"] == expected_built
    assert real_factory["daraja_clients"] == 0


def test_uvicorn_with_lifespan_off_refuses_every_request() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--lifespan", "off",
        ],
        cwd=PROJECT_ROOT,
        env=isolated_env(**UNSAFE_PRODUCTION),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    results: dict[str, tuple[int, Any]] = {}
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with httpx.Client(timeout=5) as local:
                    for path in ("/health", "/operator/transactions", "/operator/ui", "/docs"):
                        response = local.get(f"http://127.0.0.1:{port}{path}")
                        results[path] = (response.status_code, response.json())
                break
            except httpx.TransportError:
                time.sleep(0.5)
    finally:
        process.terminate()
        try:
            output = process.communicate(timeout=20)[0]
        except subprocess.TimeoutExpired:
            process.kill()
            output = process.communicate()[0]

    assert set(results) == {"/health", "/operator/transactions", "/operator/ui", "/docs"}, output
    for path, (status, body) in results.items():
        assert status == 503, (path, output)
        assert body == GENERIC_REJECTION, path
    # Without the lifespan nothing configures logging, so the rejection is emitted once
    # through Python's last-resort handler; the cached rejection prevents repeats.
    assert output.count("Startup blocked by unsafe configuration.") == 1, output
    for secret in SECRET_STRINGS:
        assert secret not in output


def test_container_factory_validates_even_when_called_directly(
    monkeypatch: pytest.MonkeyPatch,
    real_factory: dict[str, int],
) -> None:
    use_settings(monkeypatch, **UNSAFE_PRODUCTION)

    with pytest.raises(StartupConfigValidationError):
        AppContainer.from_environment()
    with pytest.raises(StartupConfigValidationError):
        bootstrap_container.create_app_container()

    assert real_factory["daraja_clients"] == 0


# --- Final remediation: numeric hosts, malformed URLs, middleware fast path --------------

NUMERIC_CALLBACK_PRODUCTION: dict[str, Any] = {
    **HARDENED_PRODUCTION,
    "daraja_production_callback_url": "https://0300.0251.0326.0310/stk",
    "daraja_transaction_status_result_url": "https://0300.0251.0326.0310/r",
    "daraja_transaction_status_timeout_url": "https://0300.0251.0326.0310/t",
}


def test_numeric_callback_host_never_builds_a_provider_on_any_serving_path(
    monkeypatch: pytest.MonkeyPatch,
    outbound_http: list[str],
    real_factory: dict[str, int],
) -> None:
    use_settings(monkeypatch, **NUMERIC_CALLBACK_PRODUCTION)

    response = TestClient(app).get("/operator/transactions")
    with pytest.raises(StartupConfigValidationError) as lifespan_error:
        with TestClient(app):
            pytest.fail("The application must not start serving.")
    with pytest.raises(StartupConfigValidationError):
        mcp_server.create_mcp_server()

    assert response.status_code == 503
    assert response.json() == GENERIC_REJECTION
    assert "DARAJA_PRODUCTION_CALLBACK_URL" in lifespan_error.value.settings
    assert real_factory["daraja_clients"] == 0
    assert real_factory["built"] == 0
    assert outbound_http == []


def test_malformed_database_url_is_a_cached_generic_rejection(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    real_factory: dict[str, int],
) -> None:
    password = secrets.token_hex(12)  # generated, so no secret-shaped literal is stored
    use_settings(
        monkeypatch,
        **SAFE_DEVELOPMENT,
        storage_mode="postgres",
        database_url=f"postgresql+psycopg://svc:{password}@db:notaport/x",
    )
    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.INFO):
        statuses = [client.get("/operator/transactions").status_code for _ in range(3)]

    assert statuses == [503, 503, 503]
    assert real_factory["attempts"] == 1
    assert real_factory["built"] == 0
    assert password not in caplog.text


def test_middleware_fast_path_skips_threadpool_once_container_exists(
    monkeypatch: pytest.MonkeyPatch,
    real_factory: dict[str, int],
) -> None:
    use_settings(monkeypatch, **SAFE_DEVELOPMENT)
    hops: list[int] = []
    real_hop = app_main.run_in_threadpool

    async def counting_hop(func: Any, *args: Any, **kwargs: Any) -> Any:
        hops.append(1)
        return await real_hop(func, *args, **kwargs)

    monkeypatch.setattr(app_main, "run_in_threadpool", counting_hop)
    client = TestClient(app)

    first = client.get("/health")
    after_first = len(hops)
    later = [client.get("/health").status_code for _ in range(5)]

    assert first.status_code == 200
    assert after_first == 1
    assert later == [200] * 5
    assert len(hops) == 1, "validated container short-circuits the gate"
    assert real_factory["attempts"] == 1


def test_middleware_fast_path_never_applies_to_overrides_or_rejections(
    monkeypatch: pytest.MonkeyPatch,
    real_factory: dict[str, int],
) -> None:
    hops: list[int] = []
    real_hop = app_main.run_in_threadpool

    async def counting_hop(func: Any, *args: Any, **kwargs: Any) -> Any:
        hops.append(1)
        return await real_hop(func, *args, **kwargs)

    monkeypatch.setattr(app_main, "run_in_threadpool", counting_hop)

    use_settings(monkeypatch, **UNSAFE_PRODUCTION)
    client = TestClient(app)
    rejected = [client.get("/health").status_code for _ in range(3)]
    assert rejected == [503, 503, 503]
    assert len(hops) == 3, "a cached rejection still goes through the gate"

    app.dependency_overrides[callback_routes.get_app_container] = AppContainer.mock
    try:
        overridden = [client.get("/health").status_code for _ in range(2)]
    finally:
        app.dependency_overrides.clear()
    assert overridden == [200, 200]
    assert len(hops) == 5, "overrides are always resolved, never short-circuited"
    assert real_factory["built"] == 0
