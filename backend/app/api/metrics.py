"""Prometheus-compatible /metrics endpoint for ATOS-X."""
import os

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["observability"])

# ---------------------------------------------------------------------------
# Metrics registry – use a dedicated CollectorRegistry so tests don't share
# state with the default global registry and duplicate-registration errors
# are avoided between test runs.
# ---------------------------------------------------------------------------
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        generate_latest,
    )

    _registry = CollectorRegistry(auto_describe=True)

    http_requests_total = Counter(
        "atos_http_requests_total",
        "Total HTTP requests handled by the backend",
        ["method", "endpoint", "status"],
        registry=_registry,
    )

    active_trades_gauge = Gauge(
        "atos_active_trades",
        "Number of currently active trades managed by AutoTrader",
        registry=_registry,
    )

    app_info = Gauge(
        "atos_app_info",
        "Static application metadata (always 1)",
        ["version"],
        registry=_registry,
    )

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False


def _set_app_info(version: str) -> None:
    """Call once at startup to expose version label."""
    if _PROMETHEUS_AVAILABLE:
        app_info.labels(version=version).set(1)


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics scrape endpoint",
    include_in_schema=not bool(os.environ.get("ATOS_TEST_MODE")),
)
async def metrics_endpoint() -> PlainTextResponse:
    """Return Prometheus-format metrics for scraping."""
    if not _PROMETHEUS_AVAILABLE:
        return PlainTextResponse(
            "# prometheus_client not installed\n",
            media_type="text/plain; version=0.0.4",
        )
    content = generate_latest(_registry).decode("utf-8")
    return PlainTextResponse(content, media_type=CONTENT_TYPE_LATEST)
