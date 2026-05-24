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

from cas.core.exceptions import ConnectorError, ExtractionError
from cas.core.models import (
    AttributeRequest,
    AttributeResponse,
    AttributeResult,
)
from cas.core.qc import validate_result
from cas.core.registry import discover, get_connector

logger = structlog.get_logger()


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

    tasks: list[tuple[str, asyncio.Task]] = []
    for provider_slug, ds_ids in provider_datasets.items():
        for ds_id in ds_ids:
            task = asyncio.create_task(
                _extract_single(
                    provider_slug=provider_slug,
                    dataset_id=ds_id,
                    request=request,
                ),
                name=f"extract:{ds_id}",
            )
            tasks.append((ds_id, task))

    raw_results = await asyncio.gather(*[t for _, t in tasks], return_exceptions=True)

    results: list[AttributeResult] = []
    warnings: list[str] = []

    for (ds_id, _), result in zip(tasks, raw_results):
        if isinstance(result, Exception):
            warnings.append(f"{ds_id}: {result}")
            logger.warning("extraction_failed", dataset=ds_id, error=str(result))
        elif isinstance(result, AttributeResult):
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
