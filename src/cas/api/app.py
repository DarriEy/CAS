# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""FastAPI application for serving CAS data."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import field_validator

from cas.api.metrics import PROVIDER_FAILURES, render_latest
from cas.api.middleware import register_middleware
from cas.api.security import rate_limit, require_api_key
from cas.cache.metadata import MetadataCache
from cas.core.config import get_settings
from cas.core.models import (
    AttributeRequest,
    AttributeResponse,
    BatchAttributeRequest,
    BatchAttributeResponse,
    Dataset,
    DatasetListResponse,
    OutputMode,
    ProviderDetail,
    ProviderListResponse,
    ProviderSummary,
    TemporalType,
)
from cas.core.registry import discover, get_connector, list_providers

_RASTER_OVER_HTTP_MSG = (
    "output='raster' is not available over the CAS HTTP API: the service is "
    "stats-only by design — it stores no rasters and does not redistribute "
    "provider data. Raster mode is in-process only: embed CAS and call "
    "cas.extract_raster_sync(...) (or 'await cas.extract_raster(...)')."
)


class StatsOnlyAttributeRequest(AttributeRequest):
    """HTTP-facing request model: rejects raster output at validation time.

    The in-process raster capability (``cas.extract_raster*``) must not be
    reachable through the FastAPI layer — that would turn CAS into a raster
    re-server, breaking its no-storage identity and the licensing posture
    that library access to provider rasters relies on.
    """

    @field_validator("output")
    @classmethod
    def _reject_raster(cls, v: OutputMode) -> OutputMode:
        if v is OutputMode.RASTER:
            raise ValueError(_RASTER_OVER_HTTP_MSG)
        return v


_metadata_cache: MetadataCache | None = None


def _get_metadata_cache() -> MetadataCache:
    global _metadata_cache
    if _metadata_cache is None:
        _metadata_cache = MetadataCache(default_ttl=get_settings().metadata_cache_ttl_s)
    return _metadata_cache


async def _datasets_for(slug: str) -> list[Dataset]:
    """Return a provider's datasets, served from the metadata cache when warm."""
    cache = _get_metadata_cache()
    cached = cache.get(slug)
    if cached is not None:
        return cached
    try:
        connector_cls = get_connector(slug)
        async with connector_cls() as conn:
            datasets = await conn.list_datasets()
    except Exception:
        # Don't cache a transient provider failure — that would serve an
        # empty catalog for the full TTL even after the provider recovers.
        return []
    cache.set(slug, datasets)
    return datasets


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        discover()
        yield

    app = FastAPI(
        title="CAS — Community Attribute Service",
        version="0.1.0",
        description=(
            "Harmonized access to global geospatial attribute datasets "
            "(DEM, soil, land cover, climate, vegetation)."
        ),
        lifespan=lifespan,
    )
    register_middleware(app)

    secured = [Depends(rate_limit), Depends(require_api_key)]

    @app.post(
        "/api/v1/extract",
        response_model=AttributeResponse,
        tags=["extract"],
        summary="Extract attributes for a single geometry",
        dependencies=secured,
    )
    async def extract_attributes(request: StatsOnlyAttributeRequest):
        from cas.extract.engine import extract

        response = await extract(request)
        for _ in response.warnings:
            PROVIDER_FAILURES.labels("aggregate").inc()
        return response

    @app.post(
        "/api/v1/extract/batch",
        response_model=BatchAttributeResponse,
        tags=["extract"],
        summary="Extract attributes for many geometries",
        dependencies=secured,
    )
    async def batch_extract_attributes(request: BatchAttributeRequest):
        from cas.extract.engine import batch_extract

        return await batch_extract(request)

    @app.get(
        "/api/v1/datasets",
        response_model=DatasetListResponse,
        tags=["catalog"],
        summary="List available datasets (paginated and filtered)",
        dependencies=secured,
    )
    async def get_datasets(
        provider: str | None = None,
        variable: str | None = None,
        temporal_type: TemporalType | None = None,
        min_resolution: float | None = None,
        max_resolution: float | None = None,
        bbox: str | None = Query(None, pattern=r"^-?\d+(\.\d+)?,-?\d+(\.\d+)?,-?\d+(\.\d+)?,-?\d+(\.\d+)?$"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        slugs = [provider] if provider else list_providers()
        all_datasets: list[Dataset] = []
        for slug in slugs:
            all_datasets.extend(await _datasets_for(slug))

        # Filtering
        filtered = all_datasets
        if variable:
            var_lower = variable.lower()
            filtered = [
                ds for ds in filtered
                if any(v.name.lower() == var_lower for v in ds.variables)
            ]
        if temporal_type:
            filtered = [ds for ds in filtered if ds.temporal.temporal_type == temporal_type]
        if min_resolution is not None:
            filtered = [ds for ds in filtered if ds.resolution_m >= min_resolution]
        if max_resolution is not None:
            filtered = [ds for ds in filtered if ds.resolution_m <= max_resolution]
        if bbox:
            try:
                min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
                filtered = [
                    ds for ds in filtered
                    if not (
                        ds.bbox.max_lon < min_lon or
                        ds.bbox.min_lon > max_lon or
                        ds.bbox.max_lat < min_lat or
                        ds.bbox.min_lat > max_lat
                    )
                ]
            except ValueError:
                pass  # Pattern validation should catch this, but safety first

        page = filtered[offset : offset + limit]
        return {
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "datasets": [ds.model_dump() for ds in page],
        }

    def _provider_summary(slug: str) -> ProviderSummary | None:
        try:
            cls = get_connector(slug)
        except Exception:
            return None
        return ProviderSummary(
            slug=slug,
            name=cls.display_name,
            protocol=str(cls.protocol),
            base_url=cls.base_url,
        )

    @app.get(
        "/api/v1/providers",
        response_model=ProviderListResponse,
        tags=["catalog"],
        summary="List registered providers (paginated)",
        dependencies=secured,
    )
    async def get_providers(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        all_providers = [s for s in (_provider_summary(slug) for slug in list_providers()) if s]
        page = all_providers[offset : offset + limit]
        return {
            "total": len(all_providers),
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "providers": page,
        }

    @app.get(
        "/api/v1/providers/{slug}",
        response_model=ProviderDetail,
        tags=["catalog"],
        summary="One provider with full dataset metadata",
        dependencies=secured,
    )
    async def get_provider(slug: str):
        summary = _provider_summary(slug)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"Unknown provider '{slug}'")
        return ProviderDetail(
            slug=summary.slug,
            name=summary.name,
            protocol=summary.protocol,
            base_url=summary.base_url,
            datasets=await _datasets_for(slug),
        )

    @app.get("/metrics", tags=["ops"], summary="Prometheus metrics exposition")
    async def metrics():
        body, content_type = render_latest()
        return Response(content=body, media_type=content_type)

    @app.get("/health", tags=["ops"], summary="Liveness check and cache stats")
    async def health_check():
        from cas.extract.engine import get_result_cache

        return {
            "status": "ok",
            "providers_registered": len(list_providers()),
            "cache": get_result_cache().stats(),
        }

    return app
