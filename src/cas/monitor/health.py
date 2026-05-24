# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Provider health monitoring for daily CI checks."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import structlog

from cas.core.models import (
    Geometry,
    HealthCheckResult,
    ProviderStatus,
    QualityFlag,
)
from cas.core.registry import discover, get_connector, list_providers

logger = structlog.get_logger()

TEST_POLYGONS: dict[str, Geometry] = {
    "central_us": Geometry(
        type="Polygon",
        coordinates=[[
            [-96.6, 39.0],
            [-96.5, 39.0],
            [-96.5, 39.1],
            [-96.6, 39.1],
            [-96.6, 39.0],
        ]],
    ),
    "alps": Geometry(
        type="Polygon",
        coordinates=[[
            [11.3, 46.8],
            [11.4, 46.8],
            [11.4, 46.9],
            [11.3, 46.9],
            [11.3, 46.8],
        ]],
    ),
}


async def check_provider(
    provider_slug: str,
    test_polygon_name: str = "central_us",
) -> HealthCheckResult:
    """Run health check for a single provider."""
    start = time.monotonic()
    test_geom = TEST_POLYGONS[test_polygon_name]

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
            result = await conn.extract(
                dataset_id=first_ds.id,
                geometry=test_geom,
            )

            response_time = int((time.monotonic() - start) * 1000)

            if result.quality == QualityFlag.MISSING:
                return HealthCheckResult(
                    provider=provider_slug,
                    status=ProviderStatus.DEGRADED,
                    response_time_ms=response_time,
                    last_checked=datetime.now(UTC),
                    datasets_available=len(datasets),
                    error="Test extraction returned no data",
                )

            return HealthCheckResult(
                provider=provider_slug,
                status=ProviderStatus.HEALTHY,
                response_time_ms=response_time,
                last_checked=datetime.now(UTC),
                datasets_available=len(datasets),
                test_value=result.value if isinstance(result.value, float) else None,
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
    test_polygon_name: str = "central_us",
) -> list[HealthCheckResult]:
    """Run health checks for all registered providers."""
    discover()
    results = []
    for slug in list_providers():
        result = await check_provider(slug, test_polygon_name)
        logger.info(
            "health_check",
            provider=slug,
            status=result.status,
            response_time_ms=result.response_time_ms,
        )
        results.append(result)
    return results
