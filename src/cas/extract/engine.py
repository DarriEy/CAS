# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Extraction engine — orchestrates multi-dataset attribute extraction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from uuid import uuid4

import structlog

from cas.cache.results import ResultCache
from cas.core.config import get_settings
from cas.core.exceptions import ConnectorError, ExtractionError, RequestLimitError
from cas.core.models import (
    AttributeRequest,
    AttributeResponse,
    AttributeResult,
    BatchAttributeRequest,
    BatchAttributeResponse,
    Geometry,
)
from cas.core.qc import check_cross_provider_consistency, validate_result
from cas.core.registry import discover, get_connector

logger = structlog.get_logger()


_result_cache: ResultCache | None = None
_result_cache_settings: object | None = None


def get_result_cache() -> ResultCache:
    """Return the process-wide result cache, building it lazily.

    The cache is keyed to the current :class:`~cas.core.config.Settings`
    singleton: when settings are refreshed (e.g. via :func:`cas.configure`),
    the next call rebuilds the cache with the new TTL/size limits.
    """
    global _result_cache, _result_cache_settings
    settings = get_settings()
    if _result_cache is None or _result_cache_settings is not settings:
        _result_cache = ResultCache(
            default_ttl=settings.result_cache_ttl_s,
            max_entries=settings.result_cache_max_entries,
        )
        _result_cache_settings = settings
    return _result_cache


def _count_vertices(geometry: Geometry) -> int:
    """Count coordinate pairs in a geometry (0 for a Point)."""
    if geometry.is_point:
        return 0

    def _walk(seq: object) -> int:
        if not isinstance(seq, (list, tuple)):
            return 0
        # A coordinate pair is [x, y] of numbers.
        if seq and all(isinstance(c, (int, float)) for c in seq):
            return 1
        return sum(_walk(item) for item in seq)

    return _walk(geometry.coordinates)


def _validate_limits(request: AttributeRequest) -> None:
    """Reject requests that exceed configured safety limits."""
    settings = get_settings()
    n_datasets = len(request.dataset_ids)
    if n_datasets > settings.max_datasets_per_request:
        raise RequestLimitError(
            f"Too many datasets: {n_datasets} > "
            f"limit {settings.max_datasets_per_request}"
        )
    n_vertices = _count_vertices(request.geometry)
    if n_vertices > settings.max_polygon_vertices:
        raise RequestLimitError(
            f"Geometry too complex: {n_vertices} vertices > "
            f"limit {settings.max_polygon_vertices}"
        )


async def extract(request: AttributeRequest) -> AttributeResponse:
    """Execute a multi-dataset attribute extraction for a single geometry.

    Groups dataset_ids by provider, fans out async tasks, collects results,
    and applies QC validation.
    """
    discover()
    _validate_limits(request)
    settings = get_settings()
    result_cache = get_result_cache()
    start_time = time.monotonic()
    request_id = uuid4().hex[:12]
    geometry_hash = hashlib.sha256(
        json.dumps(request.geometry.model_dump(), sort_keys=True).encode()
    ).hexdigest()[:16]

    provider_datasets: dict[str, list[str]] = {}
    for ds_id in request.dataset_ids:
        provider, _, _ = ds_id.partition(":")
        provider_datasets.setdefault(provider, []).append(ds_id)

    results: list[AttributeResult] = []
    warnings: list[str] = []
    tasks: list[tuple[str, asyncio.Task]] = []

    for provider_slug, ds_ids in provider_datasets.items():
        for ds_id in ds_ids:
            cache_key = result_cache.make_key(
                ds_id, geometry_hash, request.aggregation, request.time_range,
            )
            cached = result_cache.get(cache_key)
            if cached is not None:
                logger.debug("cache_hit", dataset=ds_id)
                qc_warnings = validate_result(cached)
                if qc_warnings:
                    warnings.extend(qc_warnings)
                results.append(cached)
                continue

            logger.debug("cache_miss", dataset=ds_id)
            task = asyncio.create_task(
                _extract_with_timeout(
                    provider_slug=provider_slug,
                    dataset_id=ds_id,
                    request=request,
                    timeout_s=settings.provider_timeout_s,
                ),
                name=f"extract:{ds_id}",
            )
            tasks.append((ds_id, task))

    if tasks:
        raw_results = await _gather_with_backstop(
            tasks, timeout_s=settings.request_timeout_s, warnings=warnings,
        )

        for (ds_id, _), result in zip(tasks, raw_results):
            if result is _UNFINISHED:
                continue
            if isinstance(result, Exception):
                warnings.append(f"{ds_id}: {result}")
                logger.warning("extraction_failed", dataset=ds_id, error=str(result))
            elif isinstance(result, AttributeResult):
                cache_key = result_cache.make_key(
                    ds_id, geometry_hash, request.aggregation, request.time_range,
                )
                result_cache.set(cache_key, result)
                qc_warnings = validate_result(result)
                if qc_warnings:
                    warnings.extend(qc_warnings)
                results.append(result)

    # Cross-provider consistency: when several providers answer the same
    # variable (e.g. elevation from multiple DEMs), flag any that diverge from
    # the cross-provider mean. Run once per distinct variable.
    checked_variables: set[str] = set()
    for r in results:
        var_lower = r.variable.lower()
        if var_lower in checked_variables:
            continue
        checked_variables.add(var_lower)
        warnings.extend(check_cross_provider_consistency(results, r.variable))

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    return AttributeResponse(
        request_id=request_id,
        geometry_hash=geometry_hash,
        results=results,
        warnings=warnings,
        elapsed_ms=elapsed_ms,
    )


_UNFINISHED = object()
"""Sentinel marking a task cancelled by the request backstop timeout."""


async def _gather_with_backstop(
    tasks: list[tuple[str, asyncio.Task]],
    timeout_s: float,
    warnings: list[str],
) -> list[object]:
    """Gather task results under a whole-request deadline.

    Per-provider timeouts already bound each task, so this is a safety net.
    On timeout, finished tasks keep their results; unfinished ones are
    cancelled, recorded as warnings, and returned as ``_UNFINISHED``.
    """
    pending = [t for _, t in tasks]
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), timeout=timeout_s,
        )
    except TimeoutError:
        for ds_id, task in tasks:
            if not task.done():
                task.cancel()
                warnings.append(f"{ds_id}: request timeout after {timeout_s:.0f}s")
                logger.warning("request_timeout", dataset=ds_id, timeout_s=timeout_s)

    results: list[object] = []
    for _, task in tasks:
        if task.cancelled() or not task.done():
            results.append(_UNFINISHED)
            continue
        exc = task.exception()
        results.append(exc if exc is not None else task.result())
    return results


async def _extract_with_timeout(
    provider_slug: str,
    dataset_id: str,
    request: AttributeRequest,
    timeout_s: float,
) -> AttributeResult:
    """Run ``_extract_single`` under a per-provider deadline.

    A timeout is converted to ``ExtractionError`` so it flows through the
    engine's existing exception-to-warning path rather than aborting the request.
    """
    try:
        return await asyncio.wait_for(
            _extract_single(provider_slug, dataset_id, request), timeout=timeout_s,
        )
    except TimeoutError as e:
        raise ExtractionError(
            f"Extraction failed for {dataset_id}: timeout after {timeout_s:.0f}s"
        ) from e


async def _extract_single(
    provider_slug: str,
    dataset_id: str,
    request: AttributeRequest,
) -> AttributeResult:
    start = time.monotonic()
    try:
        connector_cls = get_connector(provider_slug)
        async with connector_cls() as conn:
            result = await conn.extract(
                dataset_id=dataset_id,
                geometry=request.geometry,
                time_range=request.time_range,
            )
            result.elapsed_ms = int((time.monotonic() - start) * 1000)

            # Enrich result with metadata-driven valid_range if missing
            if result.valid_range is None:
                try:
                    datasets = await conn.list_datasets()
                    for ds in datasets:
                        if ds.id == dataset_id:
                            for var in ds.variables:
                                if var.name == result.variable:
                                    result.valid_range = var.valid_range
                                    break
                            break
                except Exception:
                    pass  # Catalog fetch failure shouldn't kill the extraction

            return result
    except ConnectorError:
        raise
    except Exception as e:
        raise ExtractionError(f"Extraction failed for {dataset_id}: {e}") from e


_MAX_CONCURRENT_GEOMETRIES = 10


async def batch_extract(request: BatchAttributeRequest) -> BatchAttributeResponse:
    """Execute multi-geometry, multi-dataset extraction.

    Fans out across geometries with bounded concurrency.
    Per-result caching deduplicates shared datasets across identical geometries.
    """
    discover()
    # Validate the shared dataset list once, up front: a per-geometry
    # RequestLimitError would otherwise be swallowed by the gather() below
    # and surface as a 200-with-warnings instead of a 422 rejection.
    settings = get_settings()
    if len(request.dataset_ids) > settings.max_datasets_per_request:
        raise RequestLimitError(
            f"Too many datasets: {len(request.dataset_ids)} > "
            f"limit {settings.max_datasets_per_request}"
        )
    start_time = time.monotonic()
    request_id = uuid4().hex[:12]
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_GEOMETRIES)

    async def _extract_one(geom):
        async with semaphore:
            single = AttributeRequest(
                geometry=geom,
                dataset_ids=request.dataset_ids,
                time_range=request.time_range,
                aggregation=request.aggregation,
                target_crs=request.target_crs,
            )
            return await extract(single)

    responses = await asyncio.gather(
        *[_extract_one(geom) for geom in request.geometries],
        return_exceptions=True,
    )

    valid_responses: list[AttributeResponse] = []
    for i, resp in enumerate(responses):
        if isinstance(resp, Exception):
            logger.warning(
                "batch_geometry_failed", index=i, error=str(resp),
            )
            valid_responses.append(AttributeResponse(
                request_id=request_id,
                geometry_hash="error",
                results=[],
                warnings=[str(resp)],
                elapsed_ms=0,
            ))
        elif isinstance(resp, AttributeResponse):
            valid_responses.append(resp)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    total_results = sum(len(r.results) for r in valid_responses)

    return BatchAttributeResponse(
        request_id=request_id,
        responses=valid_responses,
        total_geometries=len(request.geometries),
        total_results=total_results,
        elapsed_ms=elapsed_ms,
    )
