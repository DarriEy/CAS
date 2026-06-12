# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Mirror-backed stats connector (design §5 — the optional ``extract()`` win).

A :class:`MirrorVectorConnector` is a :class:`~cas.connectors.base.BaseConnector`
whose ``extract()`` reads a **curated local mirror** (a GeoParquet query layer)
instead of an HTTP endpoint, so a mirror-backed vector dataset serves zonal
*stats* through the normal extraction engine (``cas.extract_sync`` / ``batch``)
with no engine changes. This resurrects datasets that have no live global
stats endpoint — GLHYMPS being the motivating case (the old HTTP connector was
disabled because pygeoglim's real API is CONUS-only; the mirror gives global
coverage).

Aggregation is **area-weighted** over the polygon, computed in an equal-area
CRS. GLHYMPS's source CRS *is* World Cylindrical Equal Area, so intersection
areas are taken directly in the layer CRS (no extra reprojection error); the
generic path falls back to an equal-area reprojection. ``coverage_fraction`` is
the intersected-area fraction of the query — the honest "how much of your
polygon the mirror actually covered" signal (mirrors of clipped products, or a
query partly off-coverage, report < 1.0).

Health (design §5): mirror connectors are **not** live endpoints, so health is
**manifest integrity**, not a network probe — HEALTHY when the manifest parses
and files are intact, DEGRADED when not yet materialized ("not synced" is not an
outage), DOWN on an integrity failure. :func:`cas.monitor.health.check_provider`
detects ``kind == "mirror"`` connectors and uses :meth:`mirror_health` instead
of running a downloading extraction.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import ClassVar

import structlog

from cas.connectors.base import BaseConnector
from cas.core.exceptions import MirrorError
from cas.core.models import (
    AggregationMethod,
    AttributeResult,
    BoundingBox,
    Dataset,
    DataType,
    Geometry,
    Protocol,
    ProviderStatus,
    QualityFlag,
    TemporalExtent,
    TemporalType,
    TimeRange,
    Variable,
)

logger = structlog.get_logger()


@dataclass
class MirrorVariable:
    """One extractable variable backed by a mirror parquet column."""

    name: str
    column: str
    units: str
    description: str = ""
    valid_range: tuple[float, float] | None = None
    data_type: DataType = DataType.CONTINUOUS


@dataclass
class MirrorVectorConfig:
    slug: str
    mirror_spec: str
    """Mirror dataset spec (``"glhymps==2.0"``) materialized for stats."""
    display_name: str
    variables: list[MirrorVariable]
    resolution_m: float = 10000
    category: str = "hydrology"
    bbox: BoundingBox = field(
        default_factory=lambda: BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90)
    )


class MirrorVectorConnector(BaseConnector):
    """Base class for mirror-backed continuous-attribute stats connectors."""

    #: Marks this connector as mirror-backed so the health sweep checks
    #: manifest integrity instead of probing a network endpoint (design §5).
    kind: ClassVar[str] = "mirror"

    _config: MirrorVectorConfig

    # No network base_url — the data is local. Kept for BaseConnector's
    # interface and the inventory exporter.
    base_url = "(local mirror)"
    protocol = "mirror"
    supports_raster = False

    # ── Catalog ──────────────────────────────────────────────────────

    async def list_datasets(self) -> list[Dataset]:
        cfg = self._config
        from cas.mirror.datasets import get_mirror_dataset

        ds = get_mirror_dataset(cfg.mirror_spec)
        return [
            Dataset(
                id=f"{cfg.slug}:{var.name}",
                provider=cfg.slug,
                name=f"{cfg.display_name} — {var.description or var.name}",
                description=var.description or var.name,
                variables=[
                    Variable(
                        name=var.name,
                        units=var.units,
                        data_type=var.data_type,
                        description=var.description,
                        valid_range=var.valid_range,
                    )
                ],
                resolution_m=cfg.resolution_m,
                crs="EPSG:4326",
                bbox=cfg.bbox,
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license=ds.license.license,
                citation=ds.citation,
            )
            for var in cfg.variables
        ]

    def _variable(self, var_name: str) -> MirrorVariable:
        for var in self._config.variables:
            if var.name == var_name:
                return var
        raise MirrorError(
            f"Unknown variable '{var_name}' for mirror connector "
            f"'{self._config.slug}'. Available: "
            f"{', '.join(v.name for v in self._config.variables)}"
        )

    # ── Stats extraction over the local mirror ────────────────────────

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        _, _, var_name = dataset_id.partition(":")
        var = self._variable(var_name)
        return await asyncio.to_thread(self._extract_blocking, dataset_id, var, geometry)

    def _extract_blocking(
        self, dataset_id: str, var: MirrorVariable, geometry: Geometry
    ) -> AttributeResult:
        start = time.monotonic()
        cfg = self._config

        from shapely.geometry import shape as shapely_shape

        from cas.mirror.convert import _import_geopandas
        from cas.mirror.store import ensure_materialized, load_manifest

        gpd = _import_geopandas()
        import pyproj

        parquet = ensure_materialized(cfg.mirror_spec)
        manifest = load_manifest_or_raise(load_manifest, cfg.mirror_spec)
        if var.column not in manifest.columns:
            raise MirrorError(
                f"Mirror dataset '{cfg.mirror_spec}' has no column "
                f"'{var.column}' (have {', '.join(manifest.columns)})."
            )

        query_4326 = shapely_shape(geometry.model_dump())
        layer_crs = pyproj.CRS.from_user_input(manifest.crs) if manifest.crs else pyproj.CRS.from_epsg(4326)

        # Reproject the query into the layer CRS for the bbox push-down filter.
        if layer_crs.equals(pyproj.CRS.from_epsg(4326)):
            to_layer = None
            query_layer = query_4326
        else:
            to_layer = pyproj.Transformer.from_crs("EPSG:4326", layer_crs, always_xy=True)
            from shapely.ops import transform as shp_transform

            query_layer = shp_transform(to_layer.transform, query_4326)

        minx, miny, maxx, maxy = query_layer.bounds
        # Read the value column + the primary geometry column (geopandas needs
        # the geometry explicitly listed when `columns=` is given).
        import json

        import pyarrow.parquet as pq

        meta = pq.read_schema(parquet).metadata or {}
        geo = json.loads(meta.get(b"geo", b"{}"))
        geom_col = geo.get("primary_column", "geometry")
        gdf = gpd.read_parquet(
            parquet, bbox=(minx, miny, maxx, maxy), columns=[var.column, geom_col]
        )

        # Choose the equal-area CRS to measure intersection areas in. GLHYMPS's
        # source CRS is already equal-area (World Cylindrical Equal Area), so we
        # measure directly in the layer CRS; otherwise reproject to an
        # equal-area CRS centred on the query.
        if layer_crs.is_geographic:
            area_crs = _laea_for(query_4326)
            to_area = pyproj.Transformer.from_crs(layer_crs, area_crs, always_xy=True)
            from shapely.ops import transform as shp_transform

            query_area_geom = shp_transform(
                pyproj.Transformer.from_crs("EPSG:4326", area_crs, always_xy=True).transform,
                query_4326,
            )
            reproject = lambda g: shp_transform(to_area.transform, g)  # noqa: E731
        else:
            query_area_geom = query_layer
            reproject = None

        value, coverage, hits = _area_weighted(
            gdf, var.column, query_layer, query_area_geom, reproject,
            is_point=geometry.is_point,
        )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        provenance = (
            f"curated mirror {cfg.mirror_spec} column {var.column}; "
            f"area-weighted mean over {hits} polygon(s) in equal-area CRS"
        )
        if value is None:
            return AttributeResult(
                dataset_id=dataset_id, variable=var.name, value=None,
                units=var.units, aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=cfg.slug, elapsed_ms=elapsed_ms, provenance=provenance,
                valid_range=var.valid_range,
            )
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        return AttributeResult(
            dataset_id=dataset_id, variable=var.name, value=round(float(value), 6),
            units=var.units, aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=round(coverage, 6), pixel_count=hits,
            provider=cfg.slug, elapsed_ms=elapsed_ms, provenance=provenance,
            valid_range=var.valid_range,
        )

    # ── Manifest-integrity health (design §5) ─────────────────────────

    def mirror_health(self) -> tuple[ProviderStatus, str | None, int]:
        """(status, error, datasets) from manifest integrity, no network.

        HEALTHY = manifest valid + files intact; DEGRADED = not materialized
        ("not synced" is not an outage); DOWN = integrity failure.
        """
        from cas.mirror.store import is_materialized, load_manifest

        spec = self._config.mirror_spec
        n_datasets = len(self._config.variables)
        manifest = load_manifest(spec_dataset(spec))
        if manifest is None:
            return ProviderStatus.DEGRADED, "mirror not synced", n_datasets
        if not is_materialized(spec_dataset(spec)):
            return ProviderStatus.DOWN, "mirror manifest/files integrity failure", n_datasets
        return ProviderStatus.HEALTHY, None, n_datasets


# ── Module helpers ──────────────────────────────────────────────────


def spec_dataset(spec: str):
    from cas.mirror.datasets import get_mirror_dataset

    return get_mirror_dataset(spec)


def load_manifest_or_raise(load_manifest, spec: str):
    manifest = load_manifest(spec_dataset(spec))
    if manifest is None:  # pragma: no cover - ensure_materialized just wrote it
        raise MirrorError(f"manifest for mirror '{spec}' vanished after materialization")
    return manifest


def _laea_for(geom):
    """A Lambert Azimuthal Equal-Area CRS centred on a geometry (EPSG:4326)."""
    import pyproj

    c = geom.centroid
    return pyproj.CRS.from_proj4(
        f"+proj=laea +lat_0={c.y} +lon_0={c.x} +x_0=0 +y_0=0 "
        f"+ellps=WGS84 +units=m +no_defs"
    )


def _area_weighted(gdf, column, query_layer, query_area_geom, reproject, *, is_point):
    """Area-weighted mean of ``column`` over the query.

    Point → the value of the covering polygon (coverage 1.0). Polygon →
    intersection-area-weighted mean in the equal-area CRS;
    ``coverage_fraction`` = covered area / query area.
    """
    if len(gdf) == 0:
        return None, 0.0, 0

    if is_point:
        for geom, val in zip(gdf.geometry, gdf[column]):
            if val is None:
                continue
            if geom.covers(query_layer):
                return float(val), 1.0, 1
        return None, 0.0, 0

    query_area = query_area_geom.area
    if query_area <= 0:
        return None, 0.0, 0
    weighted = 0.0
    covered = 0.0
    hits = 0
    for geom, val in zip(gdf.geometry, gdf[column]):
        if val is None or not geom.intersects(query_layer):
            continue
        inter = geom.intersection(query_layer)
        if inter.is_empty:
            continue
        inter_area = (reproject(inter).area if reproject is not None else inter.area)
        if inter_area <= 0:
            continue
        weighted += float(val) * inter_area
        covered += inter_area
        hits += 1
    if covered <= 0:
        return None, 0.0, 0
    coverage = max(0.0, min(1.0, covered / query_area))
    return weighted / covered, coverage, hits
