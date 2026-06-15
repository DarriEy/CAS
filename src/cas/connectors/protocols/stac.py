# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""STAC catalog search + COG (Cloud-Optimized GeoTIFF) reading mixin."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import ClassVar

import httpx
import numpy as np
import structlog

from cas.core.exceptions import ProtocolError, RasterUnsupportedError
from cas.core.models import Geometry, RasterResampling, RasterResult, TimeRange

logger = structlog.get_logger()


class STACMixin:
    """Mixin for searching STAC catalogs and reading COG data.

    Used by connectors that access data through STAC APIs
    (Copernicus DEM, ESA WorldCover via Planetary Computer).

    Raster mode: any COG-backed connector gains a full ``extract_raster``
    implementation (all-intersecting-items windowed mosaic, bbox-clipped,
    native resolution, nodata-correct) by declaring two class attributes::

        supports_raster = True
        stac_raster_collections = ("cop-dem-glo-30",)

    plus optionally ``stac_raster_asset`` (default ``"data"``, falls back to
    the item's first asset) and ``stac_sign_assets = True`` for Planetary
    Computer catalogs whose asset hrefs need signing.
    """

    #: STAC collections searched by :meth:`extract_raster`. Empty tuple means
    #: the connector is not wired for raster output.
    stac_raster_collections: ClassVar[tuple[str, ...]] = ()
    #: Preferred asset key for raster extraction (falls back to first asset).
    stac_raster_asset: ClassVar[str] = "data"
    #: Sign asset hrefs via the Planetary Computer SAS endpoint before reading.
    stac_sign_assets: ClassVar[bool] = False

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
    ) -> tuple[np.ndarray, object, float | None, object]:
        """Read a spatial window from a COG.

        Returns (array, transform, nodata, src_crs).
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
        return arr, transform, nodata, src_crs

    @staticmethod
    def _select_best_item(items: list[dict], preference: str = "first") -> dict:
        """Pick one item for the scalar stats path.

        Note: this items[0] truncation is only acceptable for zonal *stats*
        on small geometries; the raster path never uses it — it mosaics
        **all** intersecting items (see :meth:`extract_raster`).
        """
        if not items:
            raise ProtocolError("stac", "No items found in STAC search")
        return items[0]

    # ── Raster mode (in-process only) ───────────────────────────────

    def _raster_asset_href(self, item: dict) -> str:
        """Resolve (and optionally sign) the raster asset href of a STAC item."""
        assets = item.get("assets", {})
        asset = assets.get(self.stac_raster_asset)
        if asset is None:
            if not assets:
                raise ProtocolError(
                    self.slug,  # type: ignore[attr-defined]
                    f"STAC item {item.get('id', 'unknown')} has no assets",
                )
            asset = next(iter(assets.values()))
        href = asset.get("href", "")
        if not href:
            raise ProtocolError(
                self.slug,  # type: ignore[attr-defined]
                f"STAC asset has no href on item {item.get('id', 'unknown')}",
            )
        if self.stac_sign_assets:
            href = self._sign_planetary_computer(href)  # type: ignore[attr-defined]
        return str(href)

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
        """Mosaic *all* STAC items intersecting ``bbox`` into one GeoTIFF.

        Unlike the stats path (which truncates to a single best item), this
        searches the collection, takes every intersecting item, and performs
        a windowed ``rasterio.merge`` of the per-item bbox windows — so a
        basin spanning several Copernicus DEM 1° tiles comes back as ONE
        correct bbox-clipped, native-resolution GeoTIFF with the source
        nodata carried through.
        """
        slug: str = self.slug  # type: ignore[attr-defined]
        if not self.stac_raster_collections:
            raise RasterUnsupportedError(
                slug,
                f"Connector '{slug}' is not wired for raster output "
                "(no stac_raster_collections declared).",
            )
        from cas.extract.raster import mosaic_cog_windows, write_geotiff

        start_time = time.monotonic()
        datetime_range = None
        if time_range is not None:
            datetime_range = f"{time_range.start.isoformat()}/{time_range.end.isoformat()}"

        items = await self._stac_search(
            catalog_url=self.base_url,  # type: ignore[attr-defined]
            collections=list(self.stac_raster_collections),
            bbox=bbox,
            datetime_range=datetime_range,
        )
        if not items:
            raise ProtocolError(
                slug,
                f"No STAC items intersect bbox {bbox} in "
                f"collections {list(self.stac_raster_collections)}",
            )

        hrefs = [self._raster_asset_href(item) for item in items]
        logger.info("stac_raster_mosaic", provider=slug, items=len(items), bbox=bbox)

        arr, transform, nodata, crs = await asyncio.to_thread(
            mosaic_cog_windows,
            hrefs,
            bbox,
            target_resolution=target_resolution,
            resampling=resampling,
        )
        await asyncio.to_thread(write_geotiff, output_path, arr, transform, crs, nodata)

        item_ids = ",".join(str(item.get("id", "unknown")) for item in items)
        t = transform  # affine.Affine
        return RasterResult(
            dataset_id=dataset_id,
            provider=slug,
            path=output_path,
            crs=str(crs) if crs is not None else "EPSG:4326",
            transform=(t.a, t.b, t.c, t.d, t.e, t.f),  # type: ignore[attr-defined]
            shape=(int(arr.shape[0]), int(arr.shape[1])),
            nodata=nodata,
            provenance=(
                f"STAC mosaic: {'/'.join(self.stac_raster_collections)} "
                f"({len(items)} items: {item_ids})"
            ),
            elapsed_ms=int((time.monotonic() - start_time) * 1000),
        )

    @staticmethod
    def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
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
