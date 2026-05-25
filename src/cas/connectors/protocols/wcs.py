# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""WCS (Web Coverage Service) protocol mixin."""

from __future__ import annotations

import structlog

from cas.core.models import Geometry

logger = structlog.get_logger()


class WCSMixin:
    """Mixin providing OGC WCS request building and GeoTIFF response parsing.

    Used by connectors that talk to WCS endpoints (ISRIC SoilGrids, USGS 3DEP).
    """

    async def _wcs_get_coverage(
        self,
        coverage_id: str,
        bbox: tuple[float, float, float, float],
        crs: str = "http://www.opengis.net/def/crs/EPSG/0/4326",
        output_format: str = "image/tiff",
        extra_params: dict | None = None,
    ) -> bytes:
        """Build and execute a WCS 2.0.1 GetCoverage request."""
        params: dict = {
            "service": "WCS",
            "version": "2.0.1",
            "request": "GetCoverage",
            "CoverageId": coverage_id,
            "format": output_format,
            "subset": [
                f"Long({bbox[0]},{bbox[2]})",
                f"Lat({bbox[1]},{bbox[3]})",
            ],
        }
        if extra_params:
            params.update(extra_params)

        return await self._get_bytes("", params=params)  # type: ignore[attr-defined, no-any-return]

    async def _wcs_get_capabilities(self) -> str:
        """Fetch WCS GetCapabilities XML."""
        resp = await self._get(  # type: ignore[attr-defined]
            "",
            params={
                "service": "WCS",
                "version": "2.0.1",
                "request": "GetCapabilities",
            },
        )
        return resp.text  # type: ignore[no-any-return]

    @staticmethod
    def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
        """Extract bounding box from a GeoJSON geometry."""
        if geometry.type == "Point":
            lon, lat = geometry.coordinates[0], geometry.coordinates[1]
            buf = 0.001
            return (lon - buf, lat - buf, lon + buf, lat + buf)
        if geometry.type == "Polygon":
            coords = geometry.coordinates[0]
        else:
            coords = [c for ring in geometry.coordinates for c in ring[0]]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        return (min(lons), min(lats), max(lons), max(lats))
