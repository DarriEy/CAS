# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Extraction engine — orchestrates multi-dataset attribute extraction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import structlog

from cas.cache.results import ResultCache
from cas.core.config import get_settings
from cas.core.exceptions import (
    ConnectorError,
    ExtractionError,
    RasterUnsupportedError,
    RequestLimitError,
)
from cas.core.models import (
    AttributeRequest,
    AttributeResponse,
    AttributeResult,
    BatchAttributeRequest,
    BatchAttributeResponse,
    BoundingBox,
    Geometry,
    OutputMode,
    RasterResampling,
    RasterResult,
    TimeRange,
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
    if request.geometry is None:
        return
    n_vertices = _count_vertices(request.geometry)
    if n_vertices > settings.max_polygon_vertices:
        raise RequestLimitError(
            f"Geometry too complex: {n_vertices} vertices > "
            f"limit {settings.max_polygon_vertices}"
        )


async def extract(request: AttributeRequest) -> AttributeResponse:
    """Execute a multi-dataset attribute extraction for a single geometry.

    Groups dataset_ids by provider, fans out async tasks, collects results,
    and applies QC validation. Stats-only: raster-mode requests are served by
    :func:`extract_raster` instead.
    """
    if request.output is not OutputMode.STATS:
        raise ExtractionError(
            "extract() serves output='stats' only; raster mode is in-process "
            "only — use cas.extract_raster() / cas.extract_raster_sync()."
        )
    if request.geometry is None:  # pragma: no cover — model validation enforces this
        raise ExtractionError("output='stats' requires 'geometry'")
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
    if request.geometry is None:  # pragma: no cover — extract() guarantees stats shape
        raise ExtractionError(f"Extraction failed for {dataset_id}: request has no geometry")
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


# ── Raster mode (in-process only) ───────────────────────────────────


def _as_bounding_box(
    bbox: BoundingBox | tuple[float, float, float, float] | list[float] | dict,
) -> BoundingBox:
    """Coerce a (min_lon, min_lat, max_lon, max_lat) tuple/dict to BoundingBox."""
    if isinstance(bbox, BoundingBox):
        return bbox
    if isinstance(bbox, dict):
        return BoundingBox(**bbox)
    min_lon, min_lat, max_lon, max_lat = bbox
    return BoundingBox(
        min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat,
    )


async def extract_raster(
    dataset_id: str,
    bbox: BoundingBox | tuple[float, float, float, float] | list[float] | dict,
    output_dir: str | Path,
    *,
    target_resolution: float | None = None,
    resampling: RasterResampling | str = RasterResampling.NEAREST,
    time_range: TimeRange | None = None,
    filename: str | None = None,
) -> RasterResult:
    """Extract a bbox-mode raster to ``output_dir`` and return its metadata.

    Acceptance contract (what SYMFLUENCE discretization consumes): the
    written file is a *domain-bbox, tile-mosaicked, native-resolution,
    EPSG:4326 GeoTIFF with correct nodata at a path* — the returned
    :class:`~cas.core.models.RasterResult` carries that path, never bytes.

    This path deliberately differs from the stats engine:

    - **No result cache.** Arrays don't belong in the JSON TTL cache; every
      call re-reads the source (the caller owns persistence via
      ``output_dir``).
    - **No scalar QC layer.** Quality flags and cross-provider consistency
      checks are stats concepts; raster failures raise instead of degrading
      into warnings.
    - **In-process only.** The HTTP API rejects ``output="raster"`` — CAS
      the *service* stores and re-serves nothing; raster mode is library use
      (end-user access under each provider's license terms).

    v1 scope: STAC/COG and WCS connectors only (``supports_raster``);
    native-CRS passthrough (EPSG:4326 for the supported sources).
    """
    discover()
    box = _as_bounding_box(bbox)
    # Reuse the request model for validation (bbox sanity, v1 limitations).
    request = AttributeRequest(
        output=OutputMode.RASTER,
        bbox=box,
        dataset_ids=[dataset_id],
        time_range=time_range,
        target_resolution=target_resolution,
        resampling=RasterResampling(resampling),
    )
    settings = get_settings()
    start_time = time.monotonic()

    provider_slug, _, dataset_name = dataset_id.partition(":")
    try:
        connector_cls = get_connector(provider_slug)
    except KeyError as e:
        raise ExtractionError(f"Unknown provider '{provider_slug}' in '{dataset_id}'") from e
    if not getattr(connector_cls, "supports_raster", False):
        raise RasterUnsupportedError(
            provider_slug,
            f"Provider '{provider_slug}' is stats-only: raster output is not "
            "supported (v1 covers STAC/COG and WCS connectors). Use "
            "output='stats' or a raster-capable provider.",
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / (filename or f"{provider_slug}_{dataset_name or 'raster'}.tif")
    bbox_tuple = (box.min_lon, box.min_lat, box.max_lon, box.max_lat)

    async with connector_cls() as conn:
        try:
            result = await asyncio.wait_for(
                conn.extract_raster(
                    dataset_id=dataset_id,
                    bbox=bbox_tuple,
                    output_path=output_path,
                    target_resolution=request.target_resolution,
                    resampling=request.resampling,
                    time_range=time_range,
                ),
                timeout=settings.provider_timeout_s,
            )
        except TimeoutError as e:
            raise ExtractionError(
                f"Raster extraction failed for {dataset_id}: "
                f"timeout after {settings.provider_timeout_s:.0f}s"
            ) from e

        # Enrich with catalog metadata (license/variable/units) when missing.
        if not result.license or not result.variable:
            try:
                for ds in await conn.list_datasets():
                    if ds.id == dataset_id:
                        result.license = result.license or ds.license
                        if not result.variable and ds.variables:
                            result.variable = ds.variables[0].name
                            result.units = result.units or ds.variables[0].units
                        break
            except Exception:
                pass  # Catalog fetch failure shouldn't kill the extraction

    result.elapsed_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        "raster_extracted",
        dataset=dataset_id,
        path=str(result.path),
        shape=result.shape,
        elapsed_ms=result.elapsed_ms,
    )
    return result


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
