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
from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import urlparse

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
            # A connector may pin a curated on-land, in-coverage anchor when its
            # declared bbox would otherwise sample nodata (e.g. a regional data
            # mask narrower than its bounding box, or a coastal-only layer like
            # mangroves). The anchor lives directly on the connector for COG
            # connectors, or on its ``_config`` for the national WCS family.
            anchor = getattr(conn, "health_anchor", None) or getattr(
                getattr(conn, "_config", None), "health_anchor", None
            )
            test_geom = coverage_test_geometry(
                first_ds.bbox, first_ds.resolution_m, anchor=anchor
            )
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
        # AUTH_GATED is distinct from UNKNOWN so the baseline can show "expected,
        # needs creds" separately from "genuinely unclassified".
        return HealthCheckResult(
            provider=provider_slug,
            status=ProviderStatus.AUTH_GATED,
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


def _provider_host(slug: str) -> str:
    """Best-effort hostname for a provider, used to throttle per-host load.

    Several providers share a single upstream server (e.g. the CSIRO SLGA
    soil layers all live on ``asris.csiro.au``).  When the sweep fans out
    these are otherwise hit simultaneously, which can make a weaker host
    drop connections under concurrent load.  Returns the slug itself if the
    host can't be determined, so such providers fall back to no extra
    throttling rather than all sharing one bucket.
    """
    try:
        base_url = getattr(get_connector(slug), "base_url", None)
    except Exception:
        base_url = None
    host = urlparse(base_url).hostname if base_url else None
    return host or slug


# Higher is better. Used to keep the better of two checks when re-verifying.
_STATUS_RANK = {"down": 0, "degraded": 1, "unknown": 2, "auth_gated": 2, "healthy": 3}


async def check_all_providers(
    slugs: list[str] | None = None,
    concurrency: int = 12,
    per_host_concurrency: int = 2,
    recheck_failures: bool = True,
) -> list[HealthCheckResult]:
    """Run end-to-end health checks for all (or selected) providers in parallel.

    Concurrency is bounded two ways:

    - ``concurrency`` caps total in-flight checks across the whole sweep.
    - ``per_host_concurrency`` caps how many checks hit the *same upstream
      host* at once.  This stops the sweep from hammering a single server
      with all of its providers simultaneously (which caused transient
      connection drops, e.g. the CSIRO ``asris.csiro.au`` soil layers).

    ``recheck_failures`` then re-verifies any provider that came back
    ``down`` or ``degraded`` with a *single serial pass* (no concurrency,
    one host at a time). Under concurrent load some fragile servers drop
    connections or time out and read as down/degraded even though they are
    healthy sequentially; the serial re-check lets those recover. A genuine
    outage or honestly-sparse layer fails the re-check too, so it still
    surfaces — we only ever keep the *better* of the two results, never a
    worse one. This is what makes the committed baseline trustworthy rather
    than full of concurrent-load false-positives.
    """
    discover()

    if slugs is None:
        slugs = list_providers()

    global_semaphore = asyncio.Semaphore(concurrency)
    host_semaphores: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host_concurrency))

    async def _bounded(slug: str) -> HealthCheckResult:
        # Acquire the global limit first, then the per-host limit, so a host
        # that is fully busy can't hold a global slot while it waits.
        async with global_semaphore, host_semaphores[_provider_host(slug)]:
            result = await check_provider(slug)
            logger.info(
                "health_check",
                provider=slug,
                status=result.status,
                response_time_ms=result.response_time_ms,
            )
            return result

    results = await asyncio.gather(*[_bounded(slug) for slug in slugs])

    if not recheck_failures:
        return list(results)

    # Serial re-verification pass for transient concurrent-load flakes.
    results = list(results)
    for i, result in enumerate(results):
        if str(result.status) not in ("down", "degraded"):
            continue
        retry = await check_provider(result.provider)
        if _STATUS_RANK.get(str(retry.status), 3) > _STATUS_RANK.get(str(result.status), 3):
            logger.info(
                "health_recheck_recovered",
                provider=result.provider,
                was=str(result.status),
                now=str(retry.status),
            )
            results[i] = retry

    return results
