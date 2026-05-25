# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""EEA Copernicus HRL connectors — European 10-20m land attributes via ArcGIS ImageServer.

High Resolution Layers from Copernicus Land Monitoring Service:
- Tree Cover Density
- Dominant Leaf Type
- Built-up / Imperviousness (pan-European)
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
from cas.extract.zonal import compute_zonal_stats, parse_geotiff, rasterize_geometry

logger = structlog.get_logger()

EEA_BASE = "https://image.discomap.eea.europa.eu/arcgis/rest/services/GioLandPublic"
EU_BBOX = BoundingBox(min_lon=-32, min_lat=27, max_lon=45, max_lat=72)


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


class EEAImageServerConnector(BaseConnector):
    """Base for EEA HRL ImageServer connectors."""

    _service_name: str
    _variable: Variable

    async def list_datasets(self) -> list[Dataset]:
        return [
            Dataset(
                id=f"{self.slug}:{self._variable.name}",
                provider=self.slug,
                name=self.display_name,
                description=self._variable.description or self._variable.name,
                variables=[self._variable],
                resolution_m=10,
                crs="EPSG:3035",
                bbox=EU_BBOX,
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.REST,
                license="Copernicus (free)",
                citation="Copernicus Land Monitoring Service, HRL",
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

        export_url = f"{EEA_BASE}/{self._service_name}/ImageServer/exportImage"

        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(
                    export_url,
                    params={
                        "bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
                        "bboxSR": "4326",
                        "imageSR": "4326",
                        "size": "500,500",
                        "format": "tiff",
                        "f": "json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                href = data.get("href")
                if not href:
                    raise DataFormatError(self.slug, "No href in ImageServer response")

                tiff_resp = await client.get(href, timeout=30)
                tiff_resp.raise_for_status()
                raster_bytes = tiff_resp.content

        except DataFormatError:
            raise
        except Exception as e:
            raise DataFormatError(self.slug, f"ImageServer request failed: {e}") from e

        raster_data, transform, nodata, src_crs = parse_geotiff(raster_bytes)
        if nodata is None:
            nodata = 255.0

        geom_dict = geometry.model_dump()
        mask = rasterize_geometry(geom_dict, raster_data.shape, transform, src_crs)

        is_cat = self._variable.data_type == DataType.CATEGORICAL
        agg = AggregationMethod.DISTRIBUTION if is_cat else AggregationMethod.MEAN
        value, coverage, pixel_count = compute_zonal_stats(
            raster_data=raster_data, mask=mask, nodata=nodata,
            aggregation=agg, data_type=self._variable.data_type,
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if coverage > 0.8 else QualityFlag.PARTIAL
        if coverage == 0.0:
            quality = QualityFlag.MISSING

        return AttributeResult(
            dataset_id=dataset_id, variable=self._variable.name, value=value,
            units=self._variable.units, aggregation=agg,
            quality=quality, coverage_fraction=coverage, pixel_count=pixel_count,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance=f"EEA HRL: {self._service_name}",
        )


@register("eea_tree_cover")
class EEATreeCoverConnector(EEAImageServerConnector):
    slug = "eea_tree_cover"
    display_name = "Europe Tree Cover Density 10m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_TreeCoverDensity_2018"
    _variable = Variable(
        name="tree_cover_density", units="%", data_type=DataType.CONTINUOUS,
        valid_range=(0, 100),
        description="European 10m tree cover density — controls interception and transpiration",
    )


@register("eea_leaf_type")
class EEALeafTypeConnector(EEAImageServerConnector):
    slug = "eea_leaf_type"
    display_name = "Europe Dominant Leaf Type 10m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_DominantLeafType2018"
    _variable = Variable(
        name="leaf_type", units="class", data_type=DataType.CATEGORICAL,
        description="Dominant leaf type (broadleaf/coniferous) — controls ET and interception",
    )


@register("eea_imperviousness")
class EEAImperviousnessConnector(EEAImageServerConnector):
    slug = "eea_imperviousness"
    display_name = "Europe Imperviousness 10m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_BuiltUp_2018"
    _variable = Variable(
        name="imperviousness", units="%", data_type=DataType.CONTINUOUS,
        valid_range=(0, 100),
        description="European 10m impervious surface — controls infiltration vs runoff",
    )


@register("eea_imperv_density")
class EEAImperviousnessDensityConnector(EEAImageServerConnector):
    slug = "eea_imperv_density"
    display_name = "Europe Imperviousness Density 10m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_ImperviousnessDensity_2018"
    _variable = Variable(
        name="imperv_density", units="%", data_type=DataType.CONTINUOUS,
        valid_range=(0, 100),
        description="European 10m imperviousness density (continuous %)",
    )


@register("eea_grassland")
class EEAGrasslandConnector(EEAImageServerConnector):
    slug = "eea_grassland"
    display_name = "Europe Grassland 10m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_Grassland_2018"
    _variable = Variable(
        name="grassland", units="class", data_type=DataType.CATEGORICAL,
        description="European 10m grassland extent — controls ET and infiltration",
    )


@register("eea_forest_type")
class EEAForestTypeConnector(EEAImageServerConnector):
    slug = "eea_forest_type"
    display_name = "Europe Forest Type 10m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_ForestType_2018"
    _variable = Variable(
        name="forest_type", units="class", data_type=DataType.CATEGORICAL,
        description="European forest type (broadleaf/coniferous/mixed)",
    )


@register("eea_water_wetness")
class EEAWaterWetnessConnector(EEAImageServerConnector):
    slug = "eea_water_wetness"
    display_name = "Europe Water & Wetness 10m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_WaterWetness_2018"
    _variable = Variable(
        name="water_wetness", units="class", data_type=DataType.CATEGORICAL,
        description="European water & wetness extent — identifies saturated areas",
    )


@register("eea_small_woody")
class EEASmallWoodyConnector(EEAImageServerConnector):
    slug = "eea_small_woody"
    display_name = "Europe Small Woody Features 100m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_SmallWoodyFeaturesDensity_2021_100m"
    _variable = Variable(
        name="small_woody_density", units="%", data_type=DataType.CONTINUOUS,
        valid_range=(0, 100),
        description="European small woody feature density (hedgerows, tree lines, copses)",
    )


@register("eea_tree_cover_change")
class EEATreeCoverChangeConnector(EEAImageServerConnector):
    slug = "eea_tree_cover_change"
    display_name = "Europe Tree Cover Change 2015-2018 (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_TreeCoverChangeMask_15_18"
    _variable = Variable(
        name="tree_cover_change", units="class", data_type=DataType.CATEGORICAL,
        description="European tree cover gain/loss 2015-2018 — tracks deforestation",
    )


@register("eea_leaf_type_change")
class EEALeafTypeChangeConnector(EEAImageServerConnector):
    slug = "eea_leaf_type_change"
    display_name = "Europe Leaf Type Change 2015-2018 (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "HRL_DominantLeafTypeChange_15_18"
    _variable = Variable(
        name="leaf_type_change", units="class", data_type=DataType.CATEGORICAL,
        description="European dominant leaf type change 2015-2018",
    )


@register("eea_riparian")
class EEARiparianConnector(EEAImageServerConnector):
    slug = "eea_riparian"
    display_name = "Europe Riparian Zones LC 10m (HRL)"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "RZ_LandCover_2018"
    _variable = Variable(
        name="riparian_land_cover", units="class", data_type=DataType.CATEGORICAL,
        description="European riparian zone land cover (detailed wetland/floodplain classes)",
    )


@register("eea_n2k")
class EEAN2KConnector(EEAImageServerConnector):
    slug = "eea_n2k"
    display_name = "Europe Natura 2000 LC 10m"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "N2K_LandCover_2018"
    _variable = Variable(
        name="natura2000_land_cover", units="class", data_type=DataType.CATEGORICAL,
        description="Natura 2000 protected site land cover (detailed habitat classes)",
    )


@register("eea_coastal")
class EEACoastalConnector(EEAImageServerConnector):
    slug = "eea_coastal"
    display_name = "Europe Coastal Zones LC 10m"
    base_url = EEA_BASE
    protocol = "rest"
    _service_name = "CZ_LandCover_2018"
    _variable = Variable(
        name="coastal_land_cover", units="class", data_type=DataType.CATEGORICAL,
        description="European coastal zone land cover (detailed littoral/marine classes)",
    )
