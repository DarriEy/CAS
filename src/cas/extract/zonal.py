# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Zonal statistics computation for raster data over polygons."""

from __future__ import annotations

import numpy as np

from cas.core.models import AggregationMethod, DataType


def compute_zonal_stats(
    raster_data: np.ndarray,
    mask: np.ndarray,
    nodata: float | None,
    aggregation: AggregationMethod,
    data_type: DataType = DataType.CONTINUOUS,
) -> tuple[float | dict[str, float] | None, float, int]:
    """Compute zonal statistics for masked raster data.

    Returns (value, coverage_fraction, pixel_count).
    """
    total_pixels = int(mask.sum())
    if total_pixels == 0:
        return None, 0.0, 0

    masked = raster_data[mask]

    valid = masked[~np.isclose(masked, nodata)] if nodata is not None else masked[~np.isnan(masked)]

    pixel_count = len(valid)
    coverage_fraction = pixel_count / total_pixels if total_pixels > 0 else 0.0

    if pixel_count == 0:
        return None, 0.0, 0

    if data_type == DataType.CATEGORICAL or aggregation in (
        AggregationMethod.MAJORITY,
        AggregationMethod.MINORITY,
        AggregationMethod.DISTRIBUTION,
        AggregationMethod.UNIQUE,
    ):
        return _categorical_stats(valid, aggregation), coverage_fraction, pixel_count

    return _continuous_stats(valid, aggregation), coverage_fraction, pixel_count


def _continuous_stats(values: np.ndarray, method: AggregationMethod) -> float:
    match method:
        case AggregationMethod.MEAN:
            return float(np.mean(values))
        case AggregationMethod.MEDIAN:
            return float(np.median(values))
        case AggregationMethod.MIN:
            return float(np.min(values))
        case AggregationMethod.MAX:
            return float(np.max(values))
        case AggregationMethod.STD:
            return float(np.std(values))
        case AggregationMethod.SUM:
            return float(np.sum(values))
        case _:
            return float(np.mean(values))


def _categorical_stats(
    values: np.ndarray,
    method: AggregationMethod,
) -> float | dict[str, float]:
    unique, counts = np.unique(values, return_counts=True)
    total = counts.sum()

    match method:
        case AggregationMethod.MAJORITY:
            return float(unique[np.argmax(counts)])
        case AggregationMethod.MINORITY:
            return float(unique[np.argmin(counts)])
        case AggregationMethod.UNIQUE:
            return float(len(unique))
        case AggregationMethod.DISTRIBUTION:
            return {str(int(u)): round(float(c / total), 4) for u, c in zip(unique, counts)}
        case _:
            return float(unique[np.argmax(counts)])


def rasterize_geometry(
    geometry_dict: dict,
    shape: tuple[int, int],
    transform: object,
    src_crs: object | None = None,
) -> np.ndarray:
    """Create a boolean mask from a GeoJSON geometry on the raster grid.

    For Point geometries, returns a mask with a single True pixel at the
    point location. For Polygon/MultiPolygon, rasterizes the full geometry.

    If src_crs is provided and differs from EPSG:4326, the geometry
    is reprojected to the raster's native CRS before masking.
    """
    if geometry_dict.get("type") == "Point":
        return _point_mask(geometry_dict, shape, transform, src_crs)

    from rasterio.features import geometry_mask
    from shapely.geometry import shape as shapely_shape

    geom = shapely_shape(geometry_dict)

    if src_crs and str(src_crs) != "EPSG:4326":
        from pyproj import Transformer
        from shapely.ops import transform as shapely_transform

        transformer = Transformer.from_crs("EPSG:4326", str(src_crs), always_xy=True)
        geom = shapely_transform(transformer.transform, geom)

    mask = ~geometry_mask(
        [geom],
        out_shape=shape,
        transform=transform,
        all_touched=True,
    )
    return mask  # type: ignore[no-any-return]


def _point_mask(
    geometry_dict: dict,
    shape: tuple[int, int],
    transform: object,
    src_crs: object | None = None,
) -> np.ndarray:
    """Create a single-pixel boolean mask for a Point geometry."""
    from rasterio.transform import rowcol

    lon, lat = geometry_dict["coordinates"][0], geometry_dict["coordinates"][1]

    if src_crs and str(src_crs) != "EPSG:4326":
        from pyproj import Transformer

        transformer = Transformer.from_crs("EPSG:4326", str(src_crs), always_xy=True)
        lon, lat = transformer.transform(lon, lat)

    row, col = rowcol(transform, lon, lat)
    mask = np.zeros(shape, dtype=bool)
    if 0 <= row < shape[0] and 0 <= col < shape[1]:
        mask[row, col] = True
    return mask


def parse_geotiff(data: bytes) -> tuple[np.ndarray, object, float | None, object]:
    """Parse GeoTIFF bytes into (numpy_array, transform, nodata, crs)."""
    from rasterio.io import MemoryFile

    with MemoryFile(data) as memfile, memfile.open() as src:
        arr = src.read(1)
        transform = src.transform
        nodata = src.nodata
        crs = src.crs
    return arr, transform, nodata, crs


def geometry_to_bbox(geometry) -> tuple[float, float, float, float]:
    """Convert a Geometry (Point/Polygon/MultiPolygon) to (min_lon, min_lat, max_lon, max_lat).

    For Points, adds a small buffer (~100m) around the coordinate.
    Accepts Geometry model instances or raw dicts with 'type' and 'coordinates'.
    """
    gtype = geometry.type if hasattr(geometry, "type") else geometry["type"]
    coords_raw = geometry.coordinates if hasattr(geometry, "coordinates") else geometry["coordinates"]

    if gtype == "Point":
        lon, lat = coords_raw[0], coords_raw[1]
        buf = 0.001
        return (lon - buf, lat - buf, lon + buf, lat + buf)
    if gtype == "Polygon":
        coords = coords_raw[0]
    else:
        coords = [c for ring in coords_raw for c in ring[0]]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))
