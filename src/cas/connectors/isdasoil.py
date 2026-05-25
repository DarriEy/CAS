# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""iSDAsoil connector — Africa 30m soil properties via S3 COGs.

Open access to COG tiles on S3 (no auth for S3).
REST API at api.isda-africa.com requires free registration.

We use the S3 COGs directly — no registration needed.
"""

from __future__ import annotations

import time

import structlog

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.stac import STACMixin
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
from cas.extract.zonal import compute_zonal_stats, rasterize_geometry

logger = structlog.get_logger()

S3_BASE = "https://isdasoil.s3.amazonaws.com"

ISDA_VARIABLES: dict[str, tuple[str, Variable]] = {
    "clay": (
        "clay_content/clay_content",
        Variable(name="clay", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    "sand": (
        "sand_content/sand_content",
        Variable(name="sand", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    "silt": (
        "silt_content/silt_content",
        Variable(name="silt", units="%", data_type=DataType.CONTINUOUS, valid_range=(0, 100)),
    ),
    "soc": (
        "carbon_organic/carbon_organic",
        Variable(name="soc", units="g/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 500),
                 description="Organic carbon"),
    ),
    "ph": (
        "ph/ph",
        Variable(name="ph", units="pH", data_type=DataType.CONTINUOUS, valid_range=(3, 10)),
    ),
    "bd": (
        "bulk_density/bulk_density",
        Variable(name="bd", units="g/cm3", data_type=DataType.CONTINUOUS, valid_range=(0.5, 2.5),
                 description="Bulk density"),
    ),
    "cec": (
        "cation_exchange_capacity/cation_exchange_capacity",
        Variable(name="cec", units="cmol(+)/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 100),
                 description="Cation exchange capacity"),
    ),
    "n_total": (
        "nitrogen_total/nitrogen_total",
        Variable(name="n_total", units="g/kg", data_type=DataType.CONTINUOUS, valid_range=(0, 50),
                 description="Total nitrogen"),
    ),
    "bedrock_depth": (
        "bedrock_depth/bedrock_depth",
        Variable(name="bedrock_depth", units="cm", data_type=DataType.CONTINUOUS, valid_range=(0, 500),
                 description="Depth to bedrock"),
    ),
}

DEPTHS = {"0-20cm": "0_20", "20-50cm": "20_50"}


@register("isdasoil")
class ISDAsoilConnector(STACMixin, BaseConnector):
    slug = "isdasoil"
    display_name = "iSDAsoil (Africa)"
    base_url = S3_BASE
    protocol = "s3_direct"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, (_, var_meta) in ISDA_VARIABLES.items():
            for depth_label in DEPTHS:
                datasets.append(
                    Dataset(
                        id=f"{self.slug}:{var_key}_{depth_label}",
                        provider=self.slug,
                        name=f"iSDAsoil {var_meta.description or var_meta.name} at {depth_label}",
                        description=f"Africa 30m {var_meta.description or var_meta.name} ({var_meta.units})",
                        variables=[var_meta],
                        resolution_m=30,
                        crs="EPSG:4326",
                        bbox=BoundingBox(min_lon=-26, min_lat=-35, max_lon=58, max_lat=38),
                        temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                        protocol=Protocol.S3_DIRECT,
                        license="CC-BY 4.0",
                        citation="Hengl et al. 2021, iSDAsoil",
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

        _, _, layer_id = dataset_id.partition(":")
        parts = layer_id.rsplit("_", 1)
        var_key = parts[0] if len(parts) > 1 else layer_id
        depth_label = parts[-1] if len(parts) > 1 and parts[-1] in DEPTHS else "0-20cm"
        depth_code = DEPTHS.get(depth_label, "0_20")

        if var_key not in ISDA_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        s3_path, var_meta = ISDA_VARIABLES[var_key]
        bbox = self._geometry_to_bbox(geometry)

        cog_url = f"{S3_BASE}/{s3_path}_{depth_code}cm_v0.1_mean.tif"

        try:
            raster_data, transform, nodata, src_crs = await self._read_cog_window(
                cog_url=cog_url, bbox=bbox, geometry=geometry,
            )
        except Exception as e:
            raise DataFormatError(self.slug, f"S3 COG read failed: {e}") from e

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
            provenance=f"S3 COG: {s3_path}_{depth_code}cm",
        )
