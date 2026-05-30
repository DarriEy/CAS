# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Provider health monitoring — end-to-end extraction checks.

Unlike reachability (which only confirms an endpoint answers), a health
check runs a real ``extract()`` against each provider and verifies the
returned zonal statistic is present and finite.  The test polygon is
derived from each provider's own coverage so country-specific connectors
are exercised over data they actually serve (see :mod:`test_geometry`).
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime

import structlog

from cas.core.exceptions import RegistrationRequiredError
from cas.core.models import (
    HealthCheckResult,
    ProviderStatus,
    QualityFlag,
)
from cas.core.registry import discover, get_connector, list_providers
from cas.monitor.test_geometry import coverage_test_geometry

logger = structlog.get_logger()


def _values(value: float | dict | None) -> list[float]:
    """Flatten a result value (scalar or category distribution) to floats."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, int | float)]
    if isinstance(value, int | float):
        return [float(value)]
    return []


async def check_provider(provider_slug: str) -> HealthCheckResult:
    """Run an end-to-end extraction health check for a single provider.

    Lists the provider's catalog, then extracts the first dataset over a
    coverage-derived test polygon.  Classifies as:

    - ``HEALTHY``  — extraction returned a present, finite value
    - ``DEGRADED`` — catalog empty, no data over the test area, or NaN/inf
    - ``DOWN``     — the connector raised (network, parse, auth, ...)
    """
    start = time.monotonic()

    try:
        connector_cls = get_connector(provider_slug)
        async with connector_cls() as conn:
            datasets = await conn.list_datasets()
            if not datasets:
                return HealthCheckResult(
                    provider=provider_slug,
                    status=ProviderStatus.DEGRADED,
                    response_time_ms=int((time.monotonic() - start) * 1000),
                    last_checked=datetime.now(UTC),
                    error="No datasets returned from catalog",
                )

            first_ds = datasets[0]
            test_geom = coverage_test_geometry(first_ds.bbox)
            result = await conn.extract(
                dataset_id=first_ds.id,
                geometry=test_geom,
            )

            response_time = int((time.monotonic() - start) * 1000)

            if result.quality == QualityFlag.MISSING or result.value is None:
                return HealthCheckResult(
                    provider=provider_slug,
                    status=ProviderStatus.DEGRADED,
                    response_time_ms=response_time,
                    last_checked=datetime.now(UTC),
                    datasets_available=len(datasets),
                    error="Test extraction returned no data",
                )

            values = _values(result.value)
            if values and not all(math.isfinite(v) for v in values):
                return HealthCheckResult(
                    provider=provider_slug,
                    status=ProviderStatus.DEGRADED,
                    response_time_ms=response_time,
                    last_checked=datetime.now(UTC),
                    datasets_available=len(datasets),
                    error="Test extraction returned non-finite value",
                )

            return HealthCheckResult(
                provider=provider_slug,
                status=ProviderStatus.HEALTHY,
                response_time_ms=response_time,
                last_checked=datetime.now(UTC),
                datasets_available=len(datasets),
                test_value=values[0] if values else None,
            )

    except RegistrationRequiredError as e:
        # Missing credentials is a configuration state, not a connector failure.
        return HealthCheckResult(
            provider=provider_slug,
            status=ProviderStatus.UNKNOWN,
            response_time_ms=int((time.monotonic() - start) * 1000),
            last_checked=datetime.now(UTC),
            error=f"auth-gated: {str(e)[:160]}",
        )
    except Exception as e:
        return HealthCheckResult(
            provider=provider_slug,
            status=ProviderStatus.DOWN,
            response_time_ms=int((time.monotonic() - start) * 1000),
            last_checked=datetime.now(UTC),
            error=str(e)[:200],
        )


async def check_all_providers(
    slugs: list[str] | None = None,
    concurrency: int = 12,
) -> list[HealthCheckResult]:
    """Run end-to-end health checks for all (or selected) providers in parallel."""
    discover()

    if slugs is None:
        slugs = list_providers()

    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(slug: str) -> HealthCheckResult:
        async with semaphore:
            result = await check_provider(slug)
            logger.info(
                "health_check",
                provider=slug,
                status=result.status,
                response_time_ms=result.response_time_ms,
            )
            return result

    return await asyncio.gather(*[_bounded(slug) for slug in slugs])
