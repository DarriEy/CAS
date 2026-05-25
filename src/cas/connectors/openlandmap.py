# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""OpenLandMap connector — global soil properties via WCS from OpenGeoHub."""

from __future__ import annotations

import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.wcs import WCSMixin
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
from cas.extract.zonal import compute_zonal_stats, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

OLM_VARIABLES: dict[str, tuple[str, Variable]] = {
    "clay": (
        "sol_clay.wfraction_usda.3a1a1a_m",
        Variable(name="clay", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    "sand": (
        "sol_sand.wfraction_usda.3a1a1a_m",
        Variable(name="sand", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    "bd": (
        "sol_bulkdens.fineearth_usda.4a1h_m",
        Variable(name="bd", units="10*kg/m3", data_type=DataType.CONTINUOUS, valid_range=(0, 250)),
    ),
    "soc": (
        "sol_organic.carbon_usda.6a1c_m",
        Variable(name="soc", units="5*g/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 500)),
    ),
    "ph": (
        "sol_ph.h2o_usda.4c1a2a_m",
        Variable(name="ph", units="pH*10", data_type=DataType.CONTINUOUS, valid_range=(20, 110)),
    ),
    "texture": (
        "sol_texture.class_usda.tt_m",
        Variable(name="texture", units="class", data_type=DataType.CATEGORICAL,
                 description="USDA texture class"),
    ),
    "wc_33kpa": (
        "sol_watercontent.33kPa_usda.4b1c_m",
        Variable(name="wc_33kpa", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
                 description="Water content at field capacity (33 kPa)"),
    ),
}


@register("openlandmap")
class OpenLandMapConnector(WCSMixin, BaseConnector):
    slug = "openlandmap"
    display_name = "OpenLandMap"
    base_url = "https://geoserver.opengeohub.org/landgisgeoserver/ows"
    protocol = "wcs"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, (_, var_meta) in OLM_VARIABLES.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{var_key}",
                    provider=self.slug,
                    name=f"OpenLandMap {var_meta.description or var_meta.name}",
                    description=f"Global 250m {var_meta.description or var_meta.name} ({var_meta.units})",
                    variables=[var_meta],
                    resolution_m=250,
                    crs="EPSG:4326",
                    bbox=BoundingBox(min_lon=-180, min_lat=-62, max_lon=180, max_lat=87),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.WCS,
                    license="CC-BY-SA 4.0",
                    citation="Hengl et al., OpenLandMap / OpenGeoHub",
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
        if var_key not in OLM_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        coverage_id, var_meta = OLM_VARIABLES[var_key]
        bbox = self._geometry_to_bbox(geometry)

        params = {
            "service": "WCS",
            "version": "2.0.1",
            "request": "GetCoverage",
            "CoverageId": coverage_id,
            "format": "image/tiff",
            "subset": [
                f"Long({bbox[0]},{bbox[2]})",
                f"Lat({bbox[1]},{bbox[3]})",
            ],
            "SCALESIZE": "Long(100),Lat(100)",
        }

        try:
            resp = await self.client.get("", params=params, timeout=60.0)
            if resp.status_code != 200:
                raise DataFormatError(self.slug, f"WCS returned {resp.status_code}")
            content_type = resp.headers.get("content-type", "")
            if "xml" in content_type or "html" in content_type:
                raise DataFormatError(self.slug, f"WCS error: {resp.text[:300]}")
            raster_bytes = resp.content
        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"WCS request failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        agg = AggregationMethod.DISTRIBUTION if var_meta.data_type == DataType.CATEGORICAL else AggregationMethod.MEAN
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=agg, data_type=var_meta.data_type,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=var_meta.name, value=value,
            units=var_meta.units, aggregation=agg,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"WCS: {coverage_id}",
        )
