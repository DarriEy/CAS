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
from cas.core.exceptions import ConnectorError, ExtractionError
from cas.core.models import (
    AttributeRequest,
    AttributeResponse,
    AttributeResult,
    BatchAttributeRequest,
    BatchAttributeResponse,
)
from cas.core.qc import validate_result
from cas.core.registry import discover, get_connector

logger = structlog.get_logger()

_result_cache = ResultCache()


def get_result_cache() -> ResultCache:
    return _result_cache


async def extract(request: AttributeRequest) -> AttributeResponse:
    """Execute a multi-dataset attribute extraction for a single geometry.

    Groups dataset_ids by provider, fans out async tasks, collects results,
    and applies QC validation.
    """
    discover()
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
            cache_key = _result_cache.make_key(
                ds_id, geometry_hash, request.aggregation, request.time_range,
            )
            cached = _result_cache.get(cache_key)
            if cached is not None:
                logger.debug("cache_hit", dataset=ds_id)
                qc_warnings = validate_result(cached)
                if qc_warnings:
                    warnings.extend(qc_warnings)
                results.append(cached)
                continue

            logger.debug("cache_miss", dataset=ds_id)
            task = asyncio.create_task(
                _extract_single(
                    provider_slug=provider_slug,
                    dataset_id=ds_id,
                    request=request,
                ),
                name=f"extract:{ds_id}",
            )
            tasks.append((ds_id, task))

    if tasks:
        raw_results = await asyncio.gather(
            *[t for _, t in tasks], return_exceptions=True,
        )

        for (ds_id, _), result in zip(tasks, raw_results):
            if isinstance(result, Exception):
                warnings.append(f"{ds_id}: {result}")
                logger.warning("extraction_failed", dataset=ds_id, error=str(result))
            elif isinstance(result, AttributeResult):
                cache_key = _result_cache.make_key(
                    ds_id, geometry_hash, request.aggregation, request.time_range,
                )
                _result_cache.set(cache_key, result)
                qc_warnings = validate_result(result)
                if qc_warnings:
                    warnings.extend(qc_warnings)
                results.append(result)

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    return AttributeResponse(
        request_id=request_id,
        geometry_hash=geometry_hash,
        results=results,
        warnings=warnings,
        elapsed_ms=elapsed_ms,
    )


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
        else:
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
