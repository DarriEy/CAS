# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""SSURGO Hydrologic Soil Group connector — US soil drainage classification via SDA.

HSG (A/B/C/D) controls curve number and infiltration-excess runoff generation.
Queries the component table (not chorizon) for the dominant HSG per map unit.
"""

from __future__ import annotations

import time

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

logger = structlog.get_logger()

SDA_URL = "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest"

HSG_VAR = Variable(
    name="hydrologic_soil_group", units="class", data_type=DataType.CATEGORICAL,
    description="USDA Hydrologic Soil Group (A/B/C/D) — controls runoff curve number",
)


@register("ssurgo_hsg")
class SSURGOHSGConnector(BaseConnector):
    slug = "ssurgo_hsg"
    display_name = "SSURGO Hydrologic Soil Group (US)"
    base_url = "https://SDMDataAccess.sc.egov.usda.gov"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:hsg",
                provider=self.slug,
                name="SSURGO Hydrologic Soil Group (US)",
                description="US hydrologic soil group (A/B/C/D) — controls curve number and runoff",
                variables=[HSG_VAR],
                resolution_m=30,
                crs="EPSG:4326",
                bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
                temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                protocol=Protocol.REST,
                license="Public Domain",
                citation="USDA-NRCS, SSURGO Hydrologic Soil Group",
            )
        ]

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        bbox = _geometry_to_bbox(geometry)
        aoi_wkt = _bbox_to_wkt(bbox)

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                mukey_query = (
                    f"SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{aoi_wkt}')"
                )
                resp1 = await client.post(SDA_URL, json={"query": mukey_query, "format": "JSON+COLUMNNAME"})
                resp1.raise_for_status()
                mukey_data = resp1.json()

                mukey_table = mukey_data.get("Table", [])
                if len(mukey_table) < 2:
                    raise DataFormatError(self.slug, "No map units found")

                mukeys = [str(r[0]) for r in mukey_table[1:]]
                mukey_list = ",".join(mukeys)

                hsg_query = f"""
                SELECT c.hydgrp, SUM(c.comppct_r) AS total_pct
                FROM component c
                WHERE c.mukey IN ({mukey_list})
                AND c.hydgrp IS NOT NULL
                AND c.comppct_r > 0
                GROUP BY c.hydgrp
                ORDER BY total_pct DESC
                """
                resp2 = await client.post(SDA_URL, json={"query": hsg_query, "format": "JSON+COLUMNNAME"})
                resp2.raise_for_status()
                data = resp2.json()

        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"SDA query failed: {e}") from e

        table = data.get("Table", [])
        if len(table) < 2:
            return AttributeResult(
                dataset_id=dataset_id, variable="hydrologic_soil_group", value=None,
                units="class", aggregation=AggregationMethod.DISTRIBUTION,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance="SDA: hydgrp (no data)",
            )

        total = sum(float(row[1]) for row in table[1:])
        distribution = {row[0]: round(float(row[1]) / total, 4) for row in table[1:]}

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return AttributeResult(
            dataset_id=dataset_id, variable="hydrologic_soil_group",
            value=distribution, units="class_fraction",
            aggregation=AggregationMethod.DISTRIBUTION,
            quality=QualityFlag.GOOD, coverage_fraction=1.0,
            pixel_count=len(mukeys), provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"SDA: hydgrp ({len(mukeys)} mukeys)",
        )


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


def _bbox_to_wkt(bbox: tuple[float, float, float, float]) -> str:
    return (
        f"POLYGON(({bbox[0]} {bbox[1]}, {bbox[2]} {bbox[1]}, "
        f"{bbox[2]} {bbox[3]}, {bbox[0]} {bbox[3]}, {bbox[0]} {bbox[1]}))"
    )
