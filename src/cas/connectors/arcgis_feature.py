# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""ArcGIS REST FeatureServer connectors — continuous attributes from polygon features.

A companion to ``wfs_vector`` (which serves *categorical* class distributions from WFS/GML).
This module adds CAS's second vector path: a thin ArcGIS REST ``/query`` -> GeoJSON ->
shapely-overlay reader for sources whose attribute of interest is a *numeric value carried by
a polygon feature* — a basin's upstream drainage area, a lake's mean depth. The aggregation is
therefore **continuous** (area-weighted mean over the query geometry / value-at-point), not the
categorical distribution that ``wfs_vector`` returns.

Why ArcGIS REST: the HydroSHEDS family (the obvious hydrology fill for CAS) has no live global
OGC endpoint — it is download-only, which a no-storage passthrough service cannot use. The only
live, bbox-queryable mirrors are third-party ArcGIS FeatureServer/MapServer endpoints. They are
**regional clips**, not global; each connector's ``bbox`` reflects the true service extent
honestly, and a query outside it returns MISSING (never a fabricated value).

Deliberately minimal: polygon layers only. HydroRIVERS (linestrings) need a nearest-feature
aggregation and are intentionally left to a follow-up. Generalize the config surface when a
non-HydroSHEDS source actually lands.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import structlog

from cas.connectors.base import BaseConnector
from cas.core.exceptions import DataFormatError
from cas.core.models import (
    AggregationMethod,
    AttributeResult,
    BoundingBox,
    Dataset,
    DataType,
    Geometry,
    Protocol,
    QualityFlag,
    TemporalExtent,
    TemporalType,
    TimeRange,
    Variable,
)
from cas.core.registry import register
from cas.extract.zonal import geometry_to_bbox

logger = structlog.get_logger()


def _parse_geojson_features(payload: dict, field: str):
    """Yield (shapely geometry, float value) for each GeoJSON feature with a numeric ``field``.

    ArcGIS ``f=geojson`` returns a standard FeatureCollection, so shapely parses the geometry
    directly. Features whose attribute is null or non-numeric are skipped — they cannot
    contribute to a continuous mean.
    """
    from shapely.geometry import shape as shapely_shape

    out = []
    for feat in payload.get("features", []):
        props = feat.get("properties") or {}
        raw = props.get(field)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        geom_json = feat.get("geometry")
        if not geom_json:
            continue
        try:
            geom = shapely_shape(geom_json)
        except Exception:
            continue
        if geom.is_empty:
            continue
        out.append((geom, value))
    return out


@dataclass
class ArcGISFeatureConfig:
    slug: str
    display_name: str
    service_url: str  # ".../<Feature|Map>Server" — layer id is appended per query
    layers: list[int]  # candidate layer ids; layer_router picks one per query
    attribute_field: str  # numeric feature attribute to aggregate (e.g. "UP_AREA")
    variable: Variable
    bbox: BoundingBox  # TRUE service extent (regional), not a global aspiration
    resolution_m: float = 500
    category: str = "hydrology"
    license: str = "Open"
    citation: str = ""
    # MapServer rejects resultRecordCount ("Pagination is not supported"); FeatureServer accepts it.
    supports_pagination: bool = True
    max_record_count: int = 2000
    # (lon, lat) -> layer id, for services that split coverage across layers (e.g. NA vs SA).
    layer_router: Callable[[float, float], int] | None = None
    # (lon, lat) the health check should sample — must land inside the regional clip.
    health_anchor: tuple[float, float] | None = None


class ArcGISFeatureConnector(BaseConnector):
    """Base class for ArcGIS REST FeatureServer connectors (continuous attribute, polygon features)."""

    _config: ArcGISFeatureConfig

    async def list_datasets(self) -> list[Dataset]:
        cfg = self._config
        return [
            Dataset(
                id=f"{cfg.slug}:{cfg.variable.name}",
                provider=cfg.slug,
                name=cfg.display_name,
                description=cfg.variable.description or cfg.variable.name,
                variables=[cfg.variable],
                resolution_m=cfg.resolution_m,
                crs="EPSG:4326",
                bbox=cfg.bbox,
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license=cfg.license,
                citation=cfg.citation,
            )
        ]

    def _select_layer(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> int:
        cfg = self._config
        if cfg.layer_router is not None:
            return cfg.layer_router((min_lon + max_lon) / 2, (min_lat + max_lat) / 2)
        return cfg.layers[0]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        from shapely.geometry import shape as shapely_shape

        start = time.monotonic()
        cfg = self._config
        min_lon, min_lat, max_lon, max_lat = geometry_to_bbox(geometry)
        layer = self._select_layer(min_lon, min_lat, max_lon, max_lat)

        params = {
            "where": "1=1",
            "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": cfg.attribute_field,
            "returnGeometry": "true",
            "f": "geojson",
        }
        if cfg.supports_pagination:
            params["resultRecordCount"] = str(cfg.max_record_count)

        url = f"{cfg.service_url}/{layer}/query"
        try:
            async with httpx.AsyncClient(
                timeout=90,
                follow_redirects=True,
                headers={"User-Agent": "CAS/0.1 (Community Attribute Service)"},
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    raise DataFormatError(cfg.slug, f"ArcGIS query returned {resp.status_code}")
                try:
                    payload = resp.json()
                except Exception as exc:
                    raise DataFormatError(cfg.slug, f"non-JSON ArcGIS response: {exc}") from exc
        except DataFormatError:
            raise
        except Exception as exc:
            raise DataFormatError(cfg.slug, f"ArcGIS request failed: {exc}") from exc

        if isinstance(payload, dict) and "error" in payload:
            err = payload["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            raise DataFormatError(cfg.slug, f"ArcGIS error: {msg}")

        features = _parse_geojson_features(payload, cfg.attribute_field)
        query = shapely_shape(geometry.model_dump())

        # Continuous aggregation. Point -> the value of the covering polygon. Polygon -> the
        # area-weighted mean of the per-feature values over the query geometry (planar degree²
        # area is fine for one small query: the latitude scale factor cancels in the weights).
        value: float | None = None
        coverage = 0.0
        hits = 0
        if geometry.is_point:
            for geom, val in features:
                if geom.covers(query):
                    value = val
                    coverage = 1.0
                    hits = 1
                    break
        else:
            query_area = query.area
            weighted = 0.0
            covered = 0.0
            for geom, val in features:
                if not geom.intersects(query):
                    continue
                inter = geom.intersection(query).area
                if inter <= 0:
                    continue
                weighted += val * inter
                covered += inter
                hits += 1
            if covered > 0:
                value = weighted / covered
                coverage = max(0.0, min(1.0, covered / query_area)) if query_area else 0.0

        elapsed_ms = int((time.monotonic() - start) * 1000)
        provenance = f"ArcGIS REST: layer {layer}.{cfg.attribute_field}"

        if value is None:
            return AttributeResult(
                dataset_id=dataset_id,
                variable=cfg.variable.name,
                value=None,
                units=cfg.variable.units,
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING,
                coverage_fraction=0.0,
                pixel_count=0,
                provider=cfg.slug,
                elapsed_ms=elapsed_ms,
                provenance=provenance,
            )

        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        return AttributeResult(
            dataset_id=dataset_id,
            variable=cfg.variable.name,
            value=round(value, 4),
            units=cfg.variable.units,
            aggregation=AggregationMethod.MEAN,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=hits,
            provider=cfg.slug,
            elapsed_ms=elapsed_ms,
            provenance=provenance,
            valid_range=cfg.variable.valid_range,
        )


# ═══════════════════════════════════════════════════════════════════════
#  HydroBASINS — WWF HydroSHEDS sub-basins (Pfafstetter level 08)
# ═══════════════════════════════════════════════════════════════════════

# Coverage is the Americas only: this NatureServe MapServer carries hybas_na_lev08 (layer 1)
# and hybas_sa_lev08 (layer 2). We route by centroid latitude — the Darién/Panama land bridge
# (~7°N) cleanly separates the North- and South-America Pfafstetter regions. The reported value
# is UP_AREA, the upstream drainage area (km²) of the sub-basin containing the query — the most
# hydrologically useful basin attribute. MapServer rejects resultRecordCount, so pagination off.
@register("hydrobasins")
class HydroBasinsConnector(ArcGISFeatureConnector):
    slug = "hydrobasins"
    display_name = "HydroBASINS upstream drainage area (Americas)"
    base_url = "https://imapinvasives.natureserve.org/arcgis/rest/services/hydrobasins/MapServer"
    protocol = "rest"
    _config = ArcGISFeatureConfig(
        slug="hydrobasins",
        display_name="HydroBASINS — Upstream Drainage Area, Pfafstetter L08 (Americas)",
        service_url="https://imapinvasives.natureserve.org/arcgis/rest/services/hydrobasins/MapServer",
        layers=[1, 2],  # 1 = hybas_na_lev08 (N. America), 2 = hybas_sa_lev08 (S. America)
        attribute_field="UP_AREA",
        variable=Variable(
            name="upstream_area",
            units="km2",
            data_type=DataType.CONTINUOUS,
            description="HydroBASINS upstream drainage area (UP_AREA) of the containing sub-basin (Pfafstetter L08)",
            valid_range=(0.0, 7000000.0),  # Amazon at the mouth ≈ 6.1M km²
        ),
        resolution_m=500,
        category="hydrology",
        # True extent = the Americas (NA + SA level-08 layers); query outside -> MISSING.
        bbox=BoundingBox(min_lon=-170.0, min_lat=-56.0, max_lon=-34.0, max_lat=72.0),
        license="HydroSHEDS / WWF (free for scientific, educational, commercial use; attribution)",
        citation="Lehner, B. & Grill, G. (2013), HydroBASINS — HydroSHEDS; via NatureServe ArcGIS service",
        supports_pagination=False,  # MapServer: "Pagination is not supported"
        layer_router=lambda lon, lat: 1 if lat >= 7.0 else 2,
        health_anchor=(-62.0, -4.0),  # central Amazon — high upstream area, dense basin mesh
    )


# ═══════════════════════════════════════════════════════════════════════
#  HydroLAKES — WWF HydroSHEDS lake polygons (v1.0)
# ═══════════════════════════════════════════════════════════════════════

# A public ArcGIS FeatureServer mirror covering a Central-Asia clip (~24–102°E, 26–63°N) of the
# global HydroLAKES product. Returns Depth_avg (mean lake depth, m) of the lake polygon covering
# the query; a point on land (no lake) yields MISSING — the honest "no lake here" answer.
@register("hydrolakes")
class HydroLakesConnector(ArcGISFeatureConnector):
    slug = "hydrolakes"
    display_name = "HydroLAKES mean depth (Central Asia)"
    base_url = "https://services8.arcgis.com/GyR85gR88mMqIY4t/arcgis/rest/services/HydroLAKES_v10/FeatureServer"
    protocol = "rest"
    _config = ArcGISFeatureConfig(
        slug="hydrolakes",
        display_name="HydroLAKES — Mean Lake Depth (Central Asia clip)",
        service_url="https://services8.arcgis.com/GyR85gR88mMqIY4t/arcgis/rest/services/HydroLAKES_v10/FeatureServer",
        layers=[0],
        attribute_field="Depth_avg",
        variable=Variable(
            name="lake_depth",
            units="m",
            data_type=DataType.CONTINUOUS,
            description="HydroLAKES average depth (Depth_avg) of the lake polygon covering the query",
            valid_range=(0.0, 1700.0),  # Caspian Sea max-ish; Baikal mean ~744m
        ),
        resolution_m=300,
        category="hydrology",
        # True extent = the Central-Asia clip hosted by this service; query outside -> MISSING.
        bbox=BoundingBox(min_lon=24.0, min_lat=26.0, max_lon=102.0, max_lat=63.0),
        license="HydroSHEDS / WWF (free for scientific, educational, commercial use; attribution)",
        citation="Messager et al. (2016), HydroLAKES v1.0 — HydroSHEDS; via public ArcGIS FeatureServer mirror",
        supports_pagination=True,
        health_anchor=(50.5, 41.0),  # southern Caspian Sea
    )
