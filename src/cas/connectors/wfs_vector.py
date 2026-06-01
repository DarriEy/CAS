# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""WFS vector-feature connectors — categorical attributes from polygon features.

CAS is otherwise raster-based (WCS/WMS/COG/Zarr). A few authoritative sources only
publish their classification as WFS *vector features* — geologic units, soil polygons —
where the attribute of interest lives on the feature, not in a pixel. This module adds a
thin GetFeature -> GML -> shapely-overlay path: it fetches the polygons covering the
query geometry and returns the area-weighted distribution of one attribute field.

Deliberately minimal (one provider: usgs_geology). The GML parser handles the GML 3.1.1
that MapServer emits for EPSG:4326 (lat,lon axis order, posList rings + interior holes).
Generalize the config surface when a second WFS source actually lands.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
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

# Geometry container tags whose presence marks a feature child as the geometry
# (everything else on the feature is treated as a scalar attribute).
_GEOM_TAGS = {"MultiSurface", "Surface", "MultiPolygon", "Polygon", "Point", "LineString"}


def _local(tag: str) -> str:
    """Strip the XML namespace from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def _ring_coords(ring_parent: ET.Element) -> list[tuple[float, float]] | None:
    """Extract a ring's (lon, lat) vertices from a gml:exterior/interior element.

    MapServer EPSG:4326 GML 3.1.1 encodes a flat ``gml:posList`` of "lat lon lat lon …"
    (geographic axis order), so pairs are swapped to (lon, lat) for shapely. The GML 2
    ``gml:coordinates`` fallback ("lon,lat lon,lat …") is already x,y.
    """
    for el in ring_parent.iter():
        ln = _local(el.tag)
        if ln == "posList" and el.text and el.text.strip():
            nums = [float(x) for x in el.text.split()]
            return [(nums[i + 1], nums[i]) for i in range(0, len(nums) - 1, 2)]
        if ln == "coordinates" and el.text and el.text.strip():
            pts = []
            for tok in el.text.replace("\n", " ").split():
                xy = tok.split(",")
                if len(xy) >= 2:
                    pts.append((float(xy[0]), float(xy[1])))
            return pts
    return None


def _parse_polygons(geom_el: ET.Element):
    """Build shapely polygons from a GML geometry element (handles interior holes)."""
    from shapely.geometry import Polygon

    polys = []
    for poly in geom_el.iter():
        if _local(poly.tag) != "Polygon":
            continue
        shell: list[tuple[float, float]] | None = None
        holes: list[list[tuple[float, float]]] = []
        for ring_parent in poly:
            kind = _local(ring_parent.tag)
            ring = _ring_coords(ring_parent)
            if ring is None or len(ring) < 4:
                continue
            if kind in ("exterior", "outerBoundaryIs"):
                shell = ring
            elif kind in ("interior", "innerBoundaryIs"):
                holes.append(ring)
        if shell:
            try:
                polys.append(Polygon(shell, holes))
            except Exception:
                continue
    return polys


def _parse_features(xml_bytes: bytes, field: str):
    """Yield (shapely geometry, attribute-value) for each WFS feature member."""
    from shapely.geometry import MultiPolygon

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise DataFormatError("wfs", f"unparseable GML: {exc}") from exc

    out = []
    for fm in root.iter():
        if _local(fm.tag) != "featureMember":
            continue
        members = list(fm)
        if not members:
            continue
        feat = members[0]
        value = None
        geom_el = None
        for child in feat:
            # A child is the geometry if it contains a known geometry container.
            if any(_local(d.tag) in _GEOM_TAGS for d in child.iter()):
                geom_el = child
            elif _local(child.tag) == field:
                value = (child.text or "").strip() or None
        if geom_el is None or value is None:
            continue
        polys = _parse_polygons(geom_el)
        if not polys:
            continue
        geom = polys[0] if len(polys) == 1 else MultiPolygon(polys)
        out.append((geom, value))
    return out


@dataclass
class WFSDatasetConfig:
    slug: str
    display_name: str
    wfs_url: str
    typename: str
    attribute_field: str  # feature attribute to aggregate (e.g. "lith62")
    variable: Variable
    bbox: BoundingBox
    resolution_m: float = 500
    category: str = "geology"
    license: str = "Open"
    citation: str = ""
    wfs_version: str = "1.1.0"
    max_features: int = 3000
    # (lon, lat) the health check should sample.
    health_anchor: tuple[float, float] | None = None


class WFSVectorConnector(BaseConnector):
    """Base class for WFS vector-feature connectors (area-weighted attribute distribution)."""

    _config: WFSDatasetConfig

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
                protocol=Protocol.WFS,
                license=cfg.license,
                citation=cfg.citation,
            )
        ]

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

        # WFS 1.1.0 with EPSG:4326 expects the BBOX in lat,lon (geographic) axis order.
        bbox_param = f"{min_lat},{min_lon},{max_lat},{max_lon},EPSG:4326"
        params = {
            "service": "WFS",
            "version": cfg.wfs_version,
            "request": "GetFeature",
            "typename": cfg.typename,
            "srsName": "EPSG:4326",
            "bbox": bbox_param,
            "maxFeatures": str(cfg.max_features),
        }

        try:
            async with httpx.AsyncClient(
                timeout=90,
                follow_redirects=True,
                headers={"User-Agent": "CAS/0.1 (Community Attribute Service)"},
            ) as client:
                resp = await client.get(cfg.wfs_url, params=params)
                if resp.status_code != 200:
                    raise DataFormatError(cfg.slug, f"WFS returned {resp.status_code}")
                body = resp.content
                if b"ExceptionReport" in body[:2000] or b"ServiceException" in body[:2000]:
                    raise DataFormatError(cfg.slug, f"WFS error: {body[:300].decode('utf-8', 'replace')}")
        except DataFormatError:
            raise
        except Exception as exc:
            raise DataFormatError(cfg.slug, f"WFS request failed: {exc}") from exc

        features = _parse_features(body, cfg.attribute_field)
        query = shapely_shape(geometry.model_dump())

        # Area-weighted distribution of the attribute over the query geometry. Planar
        # degree² area is fine for a single small query: the latitude scale factor is
        # ~constant across the window and cancels in the per-class ratios.
        per_class: dict[str, float] = {}
        covered = 0.0
        hits = 0
        if geometry.is_point:
            for geom, val in features:
                if geom.covers(query):
                    per_class[val] = per_class.get(val, 0.0) + 1.0
                    covered = 1.0
                    hits = 1
                    break
            query_area = 1.0
        else:
            query_area = query.area
            for geom, val in features:
                if not geom.intersects(query):
                    continue
                inter = geom.intersection(query).area
                if inter <= 0:
                    continue
                per_class[val] = per_class.get(val, 0.0) + inter
                covered += inter
                hits += 1

        if not per_class or covered <= 0:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return AttributeResult(
                dataset_id=dataset_id,
                variable=cfg.variable.name,
                value=None,
                units=cfg.variable.units,
                aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING,
                coverage_fraction=0.0,
                pixel_count=0,
                provider=cfg.slug,
                elapsed_ms=elapsed_ms,
                provenance=f"WFS: {cfg.typename}.{cfg.attribute_field}",
            )

        distribution = {k: round(v / covered, 4) for k, v in sorted(per_class.items(), key=lambda kv: -kv[1])}
        coverage = 1.0 if geometry.is_point else max(0.0, min(1.0, covered / query_area)) if query_area else 0.0
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return AttributeResult(
            dataset_id=dataset_id,
            variable=cfg.variable.name,
            value=distribution,
            units=cfg.variable.units,
            aggregation=AggregationMethod.DISTRIBUTION,
            quality=quality,
            coverage_fraction=coverage,
            pixel_count=hits,
            provider=cfg.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"WFS: {cfg.typename}.{cfg.attribute_field}",
        )


# ═══════════════════════════════════════════════════════════════════════
#  USGS — State Geologic Map Compilation (SGMC), generalized lithology
# ═══════════════════════════════════════════════════════════════════════

# The SGMC WMS (sgmc2) is a cascaded proxy that only serves pre-rendered symbology
# (GetStyles/GetLegendGraphic return 501, no service-side color->class table), so it
# can't be inverted via the color_map mapper. The WFS Lithology layer instead exposes
# the authoritative attributes: lith62 is the 62-class generalized lithology, with
# unit_name / unit_age for context. This connector returns the area-weighted lith62
# distribution over the query geometry.
@register("usgs_geology")
class USGSGeologyConnector(WFSVectorConnector):
    slug = "usgs_geology"
    display_name = "USGS State Geologic Map (SGMC)"
    base_url = "https://mrdata.usgs.gov/services/wfs/sgmc"
    protocol = "wfs"
    _config = WFSDatasetConfig(
        slug="usgs_geology",
        display_name="USGS State Geologic Map Compilation — Generalized Lithology (SGMC)",
        wfs_url="https://mrdata.usgs.gov/services/wfs/sgmc",
        typename="Lithology",
        attribute_field="lith62",
        variable=Variable(
            name="lithology",
            units="class",
            data_type=DataType.CATEGORICAL,
            description="SGMC generalized lithology (62-class, lith62) — e.g. Andesite, Granite, Sandstone",
        ),
        resolution_m=500,
        category="geology",
        bbox=BoundingBox(min_lon=-179.0, min_lat=17.0, max_lon=-65.0, max_lat=72.0),
        license="Public Domain (USGS)",
        citation="USGS, State Geologic Map Compilation (SGMC) v2",
        # Colorado Front Range — diverse igneous/sedimentary units, dense mapping.
        health_anchor=(-105.5, 39.0),
    )
