# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""SLGA connector — Soil and Landscape Grid of Australia, 90m via WCS.

Australian national soil properties from CSIRO/TERN at ~90m resolution.
"""

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

WCS_BASE = "https://www.asris.csiro.au/ArcGIS/services/TERN"

SLGA_VARIABLES: dict[str, tuple[str, Variable]] = {
    "clay": (
        "CLY_ACLEP_AU_NAT_C",
        Variable(name="clay", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    "sand": (
        "SND_ACLEP_AU_NAT_C",
        Variable(name="sand", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    "silt": (
        "SLT_ACLEP_AU_NAT_C",
        Variable(name="silt", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    # "soc" dropped: SOC_ACLEP_AU_NAT_C_V1 WCS GetCapabilities exposes no numbered
    # coverage and rejects every coverage id (InvalidParameterValue). Server-broken.
    "bd": (
        "BDW_ACLEP_AU_NAT_C",
        Variable(name="bd", units="g/cm3", data_type=DataType.CONTINUOUS, valid_range=(0.5, 2.5),
                 description="Bulk density whole earth"),
    ),
    "ph": (
        "PHC_ACLEP_AU_NAT_C",
        Variable(name="ph", units="pH", data_type=DataType.CONTINUOUS, valid_range=(3, 10),
                 description="pH (CaCl2)"),
    ),
    "awc": (
        "AWC_ACLEP_AU_NAT_C",
        Variable(name="awc", units="mm/m", data_type=DataType.CONTINUOUS, valid_range=(0, 300),
                 description="Available water capacity"),
    ),
    "ecec": (
        "ECE_ACLEP_AU_NAT_C",
        Variable(name="ecec", units="meq/100g", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
                 description="Effective cation exchange capacity"),
    ),
    "n_total": (
        "NTO_ACLEP_AU_NAT_C",
        Variable(name="n_total", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 5),
                 description="Total nitrogen"),
    ),
    "p_total": (
        "PTO_ACLEP_AU_NAT_C",
        Variable(name="p_total", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 1),
                 description="Total phosphorus"),
    ),
    "depth": (
        "DES_ACLEP_AU_NAT_C_V1",
        Variable(name="depth", units="m", data_type=DataType.CONTINUOUS, valid_range=(0, 10),
                 description="Depth of soil"),
    ),
}


@register("slga")
class SLGAConnector(WCSMixin, BaseConnector):
    slug = "slga"
    display_name = "SLGA (Australia)"
    base_url = WCS_BASE
    protocol = "wcs"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, (_, var_meta) in SLGA_VARIABLES.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{var_key}",
                    provider=self.slug,
                    name=f"SLGA {var_meta.description or var_meta.name}",
                    description=f"Australia ~90m {var_meta.description or var_meta.name} ({var_meta.units})",
                    variables=[var_meta],
                    resolution_m=90,
                    crs="EPSG:4326",
                    bbox=BoundingBox(min_lon=112, min_lat=-44, max_lon=154, max_lat=-10),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.WCS,
                    license="CC-BY 4.0",
                    citation="CSIRO / TERN, Soil and Landscape Grid of Australia",
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
        if var_key not in SLGA_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        service_name, var_meta = SLGA_VARIABLES[var_key]
        bbox = self._geometry_to_bbox(geometry)

        wcs_url = f"{WCS_BASE}/{service_name}/MapServer/WCSServer"

        params = {
            "service": "WCS",
            "version": "1.0.0",
            "request": "GetCoverage",
            "coverage": "1",
            "CRS": "EPSG:4326",
            # This WCS 1.0.0 server expects minx,miny,maxx,maxy (lon,lat) order.
            "BBOX": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
            "width": "200",
            "height": "200",
            "format": "GeoTIFF",
        }

        try:
            raster_bytes = await self._get_bytes(wcs_url, params=params)
            content_type_check = raster_bytes[:5]
            if content_type_check.startswith(b"<?xml") or content_type_check.startswith(b"<html"):
                raise DataFormatError(self.slug, f"WCS error: {raster_bytes[:300].decode(errors='replace')}")
        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"WCS request failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=AggregationMethod.MEAN, data_type=DataType.CONTINUOUS,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=var_meta.name, value=value,
            units=var_meta.units, aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"WCS: {service_name}",
        )
