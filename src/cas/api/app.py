# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""FastAPI application for serving CAS data."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Response

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
)
from cas.core.registry import discover, get_connector, list_providers

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
        datasets = []
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
    async def extract_attributes(request: AttributeRequest):
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
        tags=["catalog"],
        summary="List available datasets (paginated)",
        dependencies=secured,
    )
    async def get_datasets(
        provider: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        slugs = [provider] if provider else list_providers()
        all_datasets: list[dict] = []
        for slug in slugs:
            all_datasets.extend(ds.model_dump() for ds in await _datasets_for(slug))
        page = all_datasets[offset : offset + limit]
        return {
            "total": len(all_datasets),
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "datasets": page,
        }

    @app.get(
        "/api/v1/providers",
        tags=["catalog"],
        summary="List registered providers (paginated)",
        dependencies=secured,
    )
    async def get_providers(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        all_providers = []
        for slug in list_providers():
            try:
                connector_cls = get_connector(slug)
                all_providers.append({
                    "slug": slug,
                    "display_name": connector_cls.display_name,
                    "protocol": connector_cls.protocol,
                })
            except Exception:
                continue
        page = all_providers[offset : offset + limit]
        return {
            "total": len(all_providers),
            "limit": limit,
            "offset": offset,
            "count": len(page),
            "providers": page,
        }

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
