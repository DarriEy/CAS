# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""SSURGO/gNATSGO connector — US soil survey data via USDA SDA REST API.

Queries the Soil Data Access (SDA) tabular service to get weighted-average
soil properties for map units intersecting the query geometry.
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

SSURGO_VARIABLES: dict[str, tuple[str, Variable]] = {
    "clay": ("claytotal_r", Variable(name="clay", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100))),
    "sand": ("sandtotal_r", Variable(name="sand", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100))),
    "silt": ("silttotal_r", Variable(name="silt", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100))),
    "om": ("om_r", Variable(name="om", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
                            description="Organic matter")),
    "ph": ("ph1to1h2o_r", Variable(name="ph", units="pH", data_type=DataType.CONTINUOUS, valid_range=(3, 10))),
    "bd": ("dbthirdbar_r", Variable(name="bd", units="g/cm3", data_type=DataType.CONTINUOUS, valid_range=(0.5, 2.5),
                                    description="Bulk density at 1/3 bar")),
    "ksat": ("ksat_r", Variable(name="ksat", units="um/s", data_type=DataType.CONTINUOUS, valid_range=(0, 1000),
                                description="Saturated hydraulic conductivity")),
    "awc": ("awc_r", Variable(name="awc", units="cm/cm", data_type=DataType.CONTINUOUS, valid_range=(0, 0.5),
                              description="Available water capacity")),
    "cec": ("cec7_r", Variable(name="cec", units="meq/100g", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
                               description="Cation exchange capacity")),
}


@register("ssurgo")
class SSURGOConnector(BaseConnector):
    slug = "ssurgo"
    display_name = "SSURGO/gNATSGO (US)"
    base_url = "https://SDMDataAccess.sc.egov.usda.gov"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, (_, var_meta) in SSURGO_VARIABLES.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{var_key}",
                    provider=self.slug,
                    name=f"SSURGO {var_meta.description or var_meta.name}",
                    description=f"US soil survey {var_meta.description or var_meta.name} ({var_meta.units})",
                    variables=[var_meta],
                    resolution_m=30,
                    crs="EPSG:4326",
                    bbox=BoundingBox(min_lon=-180, min_lat=17, max_lon=-64, max_lat=72),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.REST,
                    license="Public Domain",
                    citation="USDA-NRCS, Soil Survey Geographic Database (SSURGO)",
                )
            )
        return datasets

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()

        _, _, var_key = dataset_id.partition(":")
        if var_key not in SSURGO_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        sda_column, var_meta = SSURGO_VARIABLES[var_key]
        bbox = _geometry_to_bbox(geometry)
        aoi_wkt = _bbox_to_wkt(bbox)

        # Two-step: get mukeys for the AOI, then query soil properties
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Step 1: Get map unit keys intersecting the polygon
                mukey_query = (
                    f"SELECT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{aoi_wkt}')"
                )
                resp1 = await client.post(SDA_URL, json={"query": mukey_query, "format": "JSON+COLUMNNAME"})
                resp1.raise_for_status()
                mukey_data = resp1.json()

                mukey_table = mukey_data.get("Table", [])
                if len(mukey_table) < 2:
                    raise DataFormatError(self.slug, "No map units found for this area")

                mukeys = [str(r[0]) for r in mukey_table[1:]]
                mukey_list = ",".join(mukeys)

                # Step 2: Get weighted average of the property for these map units
                prop_query = f"""
                SELECT AVG(ch.{sda_column}) AS mean_value, COUNT(*) AS n_horizons
                FROM component c
                INNER JOIN chorizon ch ON c.cokey = ch.cokey
                WHERE c.mukey IN ({mukey_list})
                AND ch.hzdept_r = 0
                AND ch.{sda_column} IS NOT NULL
                """
                resp2 = await client.post(SDA_URL, json={"query": prop_query, "format": "JSON+COLUMNNAME"})
                resp2.raise_for_status()
                data = resp2.json()

        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"SDA query failed: {e}") from e

        table = data.get("Table", [])
        if len(table) < 2 or table[1][0] is None:
            return AttributeResult(
                dataset_id=dataset_id, variable=var_meta.name, value=None,
                units=var_meta.units, aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING, coverage_fraction=0.0, pixel_count=0,
                provider=self.slug,
                elapsed_ms=int((time.monotonic() - start_time) * 1000),
                provenance=f"SDA query: {sda_column} (no data)",
            )

        mean_value = float(table[1][0])
        n_horizons = int(table[1][1])
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return AttributeResult(
            dataset_id=dataset_id, variable=var_meta.name, value=mean_value,
            units=var_meta.units, aggregation=AggregationMethod.MEAN,
            quality=QualityFlag.GOOD, coverage_fraction=1.0,
            pixel_count=n_horizons, provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"SDA: {sda_column} ({len(mukeys)} mukeys, {n_horizons} horizons)",
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
