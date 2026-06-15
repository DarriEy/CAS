# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Bbox subset queries over mirrored attribute vectors (design §3).

``mirror_subset_sync`` is the blocking implementation; ``mirror_subset`` is
the async facade (the work is file I/O — it runs in a worker thread).

Semantics (matching the native SYMFLUENCE handlers this replaces):

- the bbox is **EPSG:4326**, expanded by the per-dataset default buffer
  (0.1° GLHYMPS/HydroLAKES, 0.5° WOKAM) unless ``buffer_deg`` overrides it;
- the buffered bbox is reprojected to the layer's source CRS for the filter;
- features **intersecting** the buffered bbox are returned whole (no
  geometric clipping), read via GeoParquet row-group pruning;
- output is a single-layer **GeoPackage** in the source CRS — what
  SYMFLUENCE opens today — with license/attribution/citation/provenance
  embedded as dataset metadata and carried on the result model;
- empty results are valid (WOKAM outside karst regions) and still produce a
  schema-complete GeoPackage.

First use triggers lazy materialization (design 1A) unless
``CAS_MIRROR_OFFLINE`` is set or lazy mode is disabled — both fail with
actionable errors naming ``cas mirror sync`` (design 1B).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import structlog

from cas.core.config import Settings, get_settings
from cas.core.exceptions import MirrorError, MirrorUnitError
from cas.core.models import BoundingBox
from cas.mirror.convert import _import_geopandas
from cas.mirror.datasets import get_mirror_dataset
from cas.mirror.models import MirrorSubsetResult
from cas.mirror.store import (
    ensure_materialized,
    ensure_units_materialized,
    load_manifest,
)
from cas.mirror.units import units_for_bbox

logger = structlog.get_logger(__name__)

BboxLike = BoundingBox | tuple[float, float, float, float] | list[float] | dict

__all__ = ["mirror_subset", "mirror_subset_sync"]


def _normalize_bbox(bbox: BboxLike) -> tuple[float, float, float, float]:
    """Accept BoundingBox, (min_lon, min_lat, max_lon, max_lat), or dict."""
    if isinstance(bbox, BoundingBox):
        coords = (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)
    elif isinstance(bbox, dict):
        b = BoundingBox(**bbox)
        coords = (b.min_lon, b.min_lat, b.max_lon, b.max_lat)
    else:
        if len(bbox) != 4:
            raise MirrorError("bbox must be (min_lon, min_lat, max_lon, max_lat)")
        coords = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if coords[0] >= coords[2] or coords[1] >= coords[3]:
        raise MirrorError("bbox must have min_lon < max_lon and min_lat < max_lat")
    return coords


def _geo_metadata(parquet: Path):  # -> tuple[pyproj.CRS, str]
    """Layer CRS and primary geometry column from the GeoParquet metadata."""
    import pyarrow.parquet as pq
    import pyproj

    meta = pq.read_schema(parquet).metadata or {}
    geo = json.loads(meta.get(b"geo", b"{}"))
    primary = geo.get("primary_column", "geometry")
    crs_obj = geo.get("columns", {}).get(primary, {}).get("crs")
    if crs_obj is None:
        # GeoParquet default when crs is omitted: OGC:CRS84 (lon/lat WGS84)
        return pyproj.CRS.from_epsg(4326), primary
    return pyproj.CRS.from_user_input(crs_obj), primary


def mirror_subset_sync(
    dataset_id: str,
    bbox: BboxLike,
    output_dir: str | Path,
    *,
    columns: list[str] | None = None,
    buffer_deg: float | None = None,
    settings: Settings | None = None,
) -> MirrorSubsetResult:
    """Blocking bbox subset of a mirrored dataset into a GeoPackage.

    ``dataset_id`` is a mirror spec (``"wokam"`` or ``"glhymps==2.0"``).
    ``columns`` projects attributes on read (default: all source columns —
    the mirror keeps everything, design §3); geometry always rides along.
    """
    start = time.monotonic()
    settings = settings or get_settings()
    ds = get_mirror_dataset(dataset_id)
    if ds.delivery == "path":
        raise MirrorError(
            f"Mirror dataset '{ds.spec}' is a path-delivery geofabric: CAS "
            f"materializes topology-complete units and delivers paths — a bbox "
            f"subset cannot guarantee upstream closure (design §3). Use "
            f"cas.mirror_fetch('{ds.slug}', unit=...) instead."
        )
    gpd = _import_geopandas()
    import pyproj

    requested = _normalize_bbox(bbox)
    buffer = ds.default_buffer_deg if buffer_deg is None else float(buffer_deg)
    buffered = (
        requested[0] - buffer,
        requested[1] - buffer,
        requested[2] + buffer,
        requested[3] + buffer,
    )

    units: list[str] = []
    if ds.is_unit_structured:
        # Lazily materialize ONLY the regional units intersecting the query
        # (RGI: bbox → regions via the static extent table).
        units = units_for_bbox(ds.slug, buffered, settings)
        unit_files = ensure_units_materialized(ds, units, settings) if units else {}
        parquets = [
            path
            for unit in units
            for path in unit_files[unit].values()
            if path.suffix == ".parquet"
        ]
    else:
        parquets = [ensure_materialized(ds, settings)]

    if not parquets:
        # Honest empty answer: the bbox touches no regional unit (e.g. an
        # unglaciated domain for RGI). Schema-light but valid GeoPackage.
        return _empty_subset_result(
            ds, gpd, requested, buffer, output_dir, units, start
        )

    manifest = load_manifest(ds, settings)
    if manifest is None:  # pragma: no cover - just materialized above
        raise MirrorError(f"manifest for '{ds.spec}' vanished after materialization")

    # Reproject the buffered EPSG:4326 bbox into the layer CRS for the filter
    # (the native GLHYMPS handler does the same; design §3).
    crs, geometry_column = _geo_metadata(parquets[0])

    read_columns = None
    if columns is not None:
        unknown = sorted(set(columns) - set(manifest.columns))
        if unknown:
            raise MirrorError(
                f"Unknown column(s) {unknown} for '{ds.spec}'. "
                f"Available: {', '.join(manifest.columns)}"
            )
        read_columns = list(columns)
        if geometry_column not in read_columns:
            read_columns.append(geometry_column)

    if crs.equals(pyproj.CRS.from_epsg(4326)):
        filter_bbox = buffered
    else:
        transformer = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        filter_bbox = transformer.transform_bounds(*buffered, densify_pts=21)

    # Row-group pruning + the bbox covering column give envelope candidates;
    # refine to exact geometry-intersects against the buffered query box.
    from shapely.geometry import box as shapely_box

    query_box = shapely_box(*filter_bbox)
    frames = []
    for parquet in parquets:
        part = gpd.read_parquet(parquet, bbox=filter_bbox, columns=read_columns)
        if len(part):
            part = part[part.geometry.intersects(query_box)]
        frames.append(part)
    if len(frames) == 1:
        gdf = frames[0]
    else:
        import pandas as pd

        gdf = gpd.GeoDataFrame(
            pd.concat(frames, ignore_index=True), crs=frames[0].crs
        )

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ds.slug}_v{ds.version}_subset.gpkg"

    relevant = [
        a for a in manifest.archives if a.unit is None or a.unit in units
    ]
    archive_notes = "; ".join(
        f"{a.url} (sha256 {a.sha256[:16]}…"
        + (f", manual-import from {a.imported_from}" if a.imported_from else "")
        + ")"
        for a in relevant
    )
    unit_note = f"; units {','.join(units)}" if units else ""
    provenance = (
        f"cas {manifest.cas_version} curated mirror {ds.spec}{unit_note}; "
        f"source {archive_notes}; retrieved {manifest.retrieved_at.isoformat()}; "
        f"GeoParquet row-group bbox subset, bbox={requested}, buffer={buffer} deg"
    )
    citation = manifest.citation or ds.citation
    metadata = {
        "license": ds.license.license,
        "attribution": ds.license.attribution,
        "citation": citation,
        "provenance": provenance,
    }
    try:
        gdf.to_file(out_path, driver="GPKG", layer=ds.slug, metadata=metadata)
    except TypeError:  # older pyogrio without metadata support
        gdf.to_file(out_path, driver="GPKG", layer=ds.slug)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "mirror.subset", dataset=ds.spec, features=len(gdf),
        path=str(out_path), elapsed_ms=elapsed_ms,
    )
    return MirrorSubsetResult(
        dataset_id=ds.spec,
        slug=ds.slug,
        version=ds.version,
        path=out_path,
        feature_count=len(gdf),
        columns=[c for c in gdf.columns if c != gdf.geometry.name],
        crs=manifest.crs,
        bbox=requested,
        buffer_deg=buffer,
        license=ds.license.license,
        license_verified=ds.license.license_verified,
        license_flags=ds.license.license_flags,
        attribution=ds.license.attribution,
        citation=citation,
        provenance=provenance,
        elapsed_ms=elapsed_ms,
    )


def _empty_subset_result(
    ds, gpd, requested, buffer, output_dir, units, start
) -> MirrorSubsetResult:
    """A schema-light but valid empty GeoPackage for a query that touches no
    regional unit (e.g. an unglaciated domain for RGI)."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ds.slug}_v{ds.version}_subset.gpkg"
    empty = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    provenance = (
        f"curated mirror {ds.spec}; bbox={requested}, buffer={buffer} deg "
        f"intersects no regional unit — empty result, nothing materialized"
    )
    try:
        empty.to_file(out_path, driver="GPKG", layer=ds.slug)
    except Exception as exc:  # pragma: no cover - driver quirks
        raise MirrorUnitError(
            f"bbox {requested} intersects no unit of '{ds.spec}' and an empty "
            f"GeoPackage could not be written: {exc}"
        ) from exc
    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info("mirror.subset_empty", dataset=ds.spec, path=str(out_path))
    _ = units
    return MirrorSubsetResult(
        dataset_id=ds.spec,
        slug=ds.slug,
        version=ds.version,
        path=out_path,
        feature_count=0,
        columns=[],
        crs="EPSG:4326",
        bbox=requested,
        buffer_deg=buffer,
        license=ds.license.license,
        license_verified=ds.license.license_verified,
        license_flags=ds.license.license_flags,
        attribution=ds.license.attribution,
        citation=ds.citation,
        provenance=provenance,
        elapsed_ms=elapsed_ms,
    )


async def mirror_subset(
    dataset_id: str,
    bbox: BboxLike,
    output_dir: str | Path,
    *,
    columns: list[str] | None = None,
    buffer_deg: float | None = None,
    settings: Settings | None = None,
) -> MirrorSubsetResult:
    """Async facade for :func:`mirror_subset_sync` (runs in a worker thread)."""
    return await asyncio.to_thread(
        mirror_subset_sync,
        dataset_id,
        bbox,
        output_dir,
        columns=columns,
        buffer_deg=buffer_deg,
        settings=settings,
    )
