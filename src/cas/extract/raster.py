# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Raster-mode helpers: windowed COG mosaicking and GeoTIFF writing.

These back the in-process raster path (``cas.extract_raster``). The mosaic
semantics deliberately mirror SYMFLUENCE's native DEM tile handler: clip to
the requested bbox, merge *all* intersecting tiles at native resolution,
carry the source nodata through, and warn (using the first explicit value)
when tiles disagree about nodata.

Everything here is blocking rasterio work — callers run it via
``asyncio.to_thread`` so provider timeouts stay enforceable.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import structlog

from cas.core.exceptions import ProtocolError
from cas.core.models import RasterResampling

logger = structlog.get_logger()


def _snap_bounds_to_grid(
    bounds: tuple[float, float, float, float],
    transform: object,
) -> tuple[float, float, float, float]:
    """Expand bounds outward to the pixel edges of a reference grid.

    ``rasterio.merge`` anchors the output grid at the requested west/north
    bound. When that grid is offset from the source pixel grid (Copernicus
    DEM tiles are half-pixel shifted: pixel *centers* sit on degree lines),
    rounding can leave the easternmost/southernmost output column/row
    uncovered by any source — silently 0-filled when nodata is None. Snapping
    the request outward to the reference grid (≤1 native pixel per edge)
    aligns the output with the sources so every pixel is covered.
    """
    t = transform  # affine.Affine
    rx: float = t.a  # type: ignore[attr-defined]  # pixel width (> 0)
    ry: float = -t.e  # type: ignore[attr-defined]  # pixel height (> 0)
    x_off: float = t.c  # type: ignore[attr-defined]
    y_off: float = t.f  # type: ignore[attr-defined]
    west, south, east, north = bounds
    eps_x, eps_y = rx * 1e-6, ry * 1e-6
    west = x_off + math.floor((west - x_off) / rx + eps_x) * rx
    east = x_off + math.ceil((east - x_off) / rx - eps_x) * rx
    north = y_off - math.floor((y_off - north) / ry + eps_y) * ry
    south = y_off - math.ceil((y_off - south) / ry - eps_y) * ry
    return (west, south, east, north)


def mosaic_cog_windows(
    urls: list[str],
    bbox: tuple[float, float, float, float],
    *,
    target_resolution: float | None = None,
    resampling: RasterResampling = RasterResampling.NEAREST,
) -> tuple[np.ndarray, object, float | None, object]:
    """Windowed mosaic of every intersecting COG, clipped to ``bbox``.

    ``bbox`` is (min_lon, min_lat, max_lon, max_lat) in EPSG:4326; it is
    transformed to the sources' native CRS when needed, and the output stays
    in that native CRS (v1 passthrough — no reprojection). ``rasterio.merge``
    reads only the per-source windows that overlap the clipped bounds, so a
    basin spanning several 1° DEM tiles comes back as one correct array
    without downloading whole tiles. The bounds are snapped outward (at most
    one native pixel per edge) to the first source's pixel grid so the merge
    cannot leave half-covered edge pixels unfilled.

    Returns ``(array_2d, transform, nodata, crs)`` for band 1.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.merge import merge as rio_merge
    from rasterio.warp import transform_bounds

    if not urls:
        raise ProtocolError("stac", "No COG URLs to mosaic")

    srcs = [rasterio.open(u) for u in urls]
    try:
        crs0 = srcs[0].crs
        for s in srcs[1:]:
            if s.crs != crs0:
                raise ProtocolError(
                    "stac",
                    f"Mixed source CRS in mosaic ({s.crs} vs {crs0}); "
                    "v1 raster mode requires a single native CRS",
                )

        reproject_bbox = crs0 is not None and str(crs0) != "EPSG:4326"
        bounds = transform_bounds("EPSG:4326", crs0, *bbox) if reproject_bbox else bbox
        # Align the output grid with the source pixel grid (≤1 px outward per
        # edge) so merge rounding can't leave edge columns/rows uncovered.
        bounds = _snap_bounds_to_grid(tuple(bounds), srcs[0].transform)

        src_nodata = [s.nodata for s in srcs]
        nodata = next((v for v in src_nodata if v is not None), None)
        if any(v != nodata for v in src_nodata):
            logger.warning(
                "inconsistent_nodata_across_tiles", values=src_nodata, using=nodata,
            )

        merge_kwargs: dict = {
            "bounds": tuple(bounds),
            "resampling": Resampling[resampling.value],
        }
        if nodata is not None:
            merge_kwargs["nodata"] = nodata
        if target_resolution is not None:
            merge_kwargs["res"] = target_resolution

        mosaic, out_transform = rio_merge(srcs, **merge_kwargs)
    finally:
        for s in srcs:
            s.close()

    return mosaic[0], out_transform, nodata, crs0


def write_geotiff(
    path: Path,
    array: np.ndarray,
    transform: object,
    crs: object,
    nodata: float | None,
    compress: str = "lzw",
) -> None:
    """Write a single-band 2D array as a compressed GeoTIFF."""
    import rasterio

    profile: dict = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": array.dtype.name,
        "crs": crs,
        "transform": transform,
        "compress": compress,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)


def describe_geotiff(
    path: Path,
) -> tuple[str, tuple[float, float, float, float, float, float], tuple[int, int], float | None]:
    """Read (crs, transform-6-tuple, (rows, cols), nodata) from a GeoTIFF on disk.

    Also serves as a validity check for passthrough payloads: a non-raster
    file (e.g. a WCS XML error document) fails to open.
    """
    import rasterio

    with rasterio.open(path) as src:
        t = src.transform
        return (
            str(src.crs),
            (t.a, t.b, t.c, t.d, t.e, t.f),
            (src.height, src.width),
            src.nodata,
        )
