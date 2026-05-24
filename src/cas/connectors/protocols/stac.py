# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""STAC catalog search + COG (Cloud-Optimized GeoTIFF) reading mixin."""

from __future__ import annotations

import httpx
import numpy as np
import structlog

from cas.core.exceptions import ProtocolError
from cas.core.models import Geometry

logger = structlog.get_logger()


class STACMixin:
    """Mixin for searching STAC catalogs and reading COG data.

    Used by connectors that access data through STAC APIs
    (Copernicus DEM, ESA WorldCover via Planetary Computer).
    """

    async def _stac_search(
        self,
        catalog_url: str,
        collections: list[str],
        bbox: tuple[float, float, float, float],
        datetime_range: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Search a STAC API for items intersecting a bounding box."""
        search_body: dict = {
            "collections": collections,
            "bbox": list(bbox),
            "limit": limit,
        }
        if datetime_range:
            search_body["datetime"] = datetime_range

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            resp = await client.post(f"{catalog_url}/search", json=search_body)
            resp.raise_for_status()
            data = resp.json()

        return data.get("features", [])  # type: ignore[no-any-return]

    async def _read_cog_window(
        self,
        cog_url: str,
        bbox: tuple[float, float, float, float],
        geometry: Geometry | None = None,
    ) -> tuple[np.ndarray, object, float | None]:
        """Read a spatial window from a COG, returning (array, transform, nodata).

        Handles CRS reprojection when the COG is not in EPSG:4326.
        """
        import rasterio
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds

        with rasterio.open(cog_url) as src:
            src_crs = src.crs
            if src_crs and str(src_crs) != "EPSG:4326":
                native_bbox = transform_bounds("EPSG:4326", src_crs, *bbox)
            else:
                native_bbox = bbox
            window = from_bounds(*native_bbox, transform=src.transform)
            arr = src.read(1, window=window)
            transform = src.window_transform(window)
            nodata = src.nodata
        return arr, transform, nodata

    @staticmethod
    def _select_best_item(items: list[dict], preference: str = "first") -> dict:
        if not items:
            raise ProtocolError("stac", "No items found in STAC search")
        return items[0]

    @staticmethod
    def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
        if geometry.type == "Polygon":
            coords = geometry.coordinates[0]
        else:
            coords = [c for ring in geometry.coordinates for c in ring[0]]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        return (min(lons), min(lats), max(lons), max(lats))
