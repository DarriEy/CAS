# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""WCS (Web Coverage Service) protocol mixin."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import structlog

from cas.core.exceptions import DataFormatError, RasterUnsupportedError
from cas.core.models import Geometry, RasterResampling, RasterResult, TimeRange

logger = structlog.get_logger()


class WCSMixin:
    """Mixin providing OGC WCS request building and GeoTIFF response parsing.

    Used by connectors that talk to WCS endpoints (ISRIC SoilGrids, USGS 3DEP).

    Raster mode: a WCS connector gains ``extract_raster`` (a native-resolution
    GetCoverage GeoTIFF passthrough, clipped server-side to the request bbox)
    by setting ``supports_raster = True`` and overriding
    :meth:`_raster_coverage_id` to map a dataset id to a WCS coverage id.
    Connectors with non-standard request shapes (auth, WCS 1.0.0) can also
    override :meth:`_wcs_raster_bytes`.
    """

    def _raster_coverage_id(self, dataset_id: str) -> str | None:
        """Map a CAS dataset id to the WCS CoverageId used for raster output.

        Return ``None`` (the default) for datasets not wired for raster mode.
        """
        return None

    async def _wcs_raster_bytes(
        self, coverage_id: str, bbox: tuple[float, float, float, float],
    ) -> bytes:
        """Fetch the GetCoverage GeoTIFF payload (override for auth/dialects)."""
        return await self._wcs_get_coverage(coverage_id, bbox)

    async def extract_raster(
        self,
        dataset_id: str,
        bbox: tuple[float, float, float, float],
        output_path: Path,
        *,
        target_resolution: float | None = None,
        resampling: RasterResampling = RasterResampling.NEAREST,
        time_range: TimeRange | None = None,
    ) -> RasterResult:
        """Write a bbox-subset GetCoverage GeoTIFF to ``output_path``.

        This is a *passthrough*: the server's native-CRS, native-resolution
        GeoTIFF bytes are written verbatim, then opened once to validate the
        payload and report crs/transform/shape/nodata.
        """
        slug: str = self.slug  # type: ignore[attr-defined]
        coverage_id = self._raster_coverage_id(dataset_id)
        if coverage_id is None:
            raise RasterUnsupportedError(
                slug,
                f"Dataset '{dataset_id}' is not wired for raster output on "
                f"connector '{slug}'.",
            )
        if target_resolution is not None:
            raise RasterUnsupportedError(
                slug,
                "v1 WCS raster output is a native-resolution passthrough; "
                "'target_resolution' is not supported for WCS providers.",
            )
        from cas.extract.raster import describe_geotiff

        start_time = time.monotonic()
        data = await self._wcs_raster_bytes(coverage_id, bbox)
        head = data[:5]
        if head.startswith(b"<?xml") or head.startswith(b"<html") or head.startswith(b"<"):
            raise DataFormatError(
                slug, f"WCS error: {data[:300].decode(errors='replace')}",
            )

        output_path.write_bytes(data)
        try:
            crs, transform, shape, nodata = await asyncio.to_thread(
                describe_geotiff, output_path,
            )
        except Exception as e:
            output_path.unlink(missing_ok=True)
            raise DataFormatError(
                slug, f"WCS GetCoverage payload is not a readable GeoTIFF: {e}",
            ) from e

        return RasterResult(
            dataset_id=dataset_id,
            provider=slug,
            path=output_path,
            crs=crs,
            transform=transform,
            shape=shape,
            nodata=nodata,
            provenance=f"WCS GetCoverage passthrough: {coverage_id}",
            elapsed_ms=int((time.monotonic() - start_time) * 1000),
        )

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
