# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""FastAPI application for serving CAS data."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from cas.core.models import AttributeRequest, AttributeResponse
from cas.core.registry import discover, get_connector, list_providers


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

    @app.post("/api/v1/extract", response_model=AttributeResponse)
    async def extract_attributes(request: AttributeRequest):
        from cas.extract.engine import extract

        try:
            return await extract(request)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    @app.get("/api/v1/datasets")
    async def get_datasets(provider: str | None = None):
        slugs = [provider] if provider else list_providers()
        all_datasets = []
        for slug in slugs:
            try:
                connector_cls = get_connector(slug)
                async with connector_cls() as conn:
                    ds_list = await conn.list_datasets()
                    all_datasets.extend([ds.model_dump() for ds in ds_list])
            except Exception:
                continue
        return {"count": len(all_datasets), "datasets": all_datasets}

    @app.get("/api/v1/providers")
    async def get_providers():
        result = []
        for slug in list_providers():
            try:
                connector_cls = get_connector(slug)
                result.append({
                    "slug": slug,
                    "display_name": connector_cls.display_name,
                    "protocol": connector_cls.protocol,
                })
            except Exception:
                continue
        return {"providers": result}

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "providers_registered": len(list_providers())}

    return app
