# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""GLHYMPS connector — global hydrogeology (permeability, porosity).

GLobal HYdrogeology MaPS v2.0: near-surface permeability and porosity
derived from the Global Lithological Map (GLiM).

Data available via Borealis Data (UVic) or pygeoglim package.
Controls: baseflow generation, groundwater recharge, subsurface storage.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

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
from cas.core.registry import register  # noqa: F401  (used by @register when re-enabled)
from cas.extract.zonal import geometry_to_bbox

logger = structlog.get_logger()

_glhymps_pool = ThreadPoolExecutor(max_workers=1)

GLHYMPS_VARIABLES: dict[str, Variable] = {
    "permeability": Variable(
        name="permeability", units="log10(m2)", data_type=DataType.CONTINUOUS,
        valid_range=(-20, -8),
        description="Near-surface permeability (controls baseflow/recharge)",
    ),
    "porosity": Variable(
        name="porosity", units="fraction", data_type=DataType.CONTINUOUS,
        valid_range=(0, 0.6),
        description="Near-surface porosity (controls subsurface storage)",
    ),
}


# Disabled: connector calls a non-existent pygeoglim.get_glhymps(lat, lon). The real
# pygeoglim API is load_geometry(bbox=...)/glhymps_attributes() returning
# geol_porosity/geol_permeability, and it is CONUS-only (not the global coverage
# advertised here). Re-enable only after rewriting extract() for bbox CONUS queries.
# @register("glhymps")
class GLHYMPSConnector(BaseConnector):
    slug = "glhymps"
    display_name = "GLHYMPS 2.0 (Hydrogeology)"
    base_url = "https://borealisdata.ca"
    protocol = "rest"

    async def list_datasets(self) -> list[Dataset]:
        datasets = []
        for var_key, var_meta in GLHYMPS_VARIABLES.items():
            datasets.append(
                Dataset(
                    id=f"{self.slug}:{var_key}",
                    provider=self.slug,
                    name=f"GLHYMPS {var_meta.description}",
                    description=(
                        f"Global {var_meta.description} from lithology mapping. "
                        f"Install pygeoglim for local access: pip install pygeoglim"
                    ),
                    variables=[var_meta],
                    resolution_m=10000,
                    crs="EPSG:4326",
                    bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
                    temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
                    protocol=Protocol.REST,
                    license="CC-BY 4.0",
                    citation="Gleeson et al. 2014 / Huscroft et al. 2018, GLHYMPS",
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

        if var_key not in GLHYMPS_VARIABLES:
            raise DataFormatError(self.slug, f"Unknown variable: {var_key}")

        var_meta = GLHYMPS_VARIABLES[var_key]

        try:
            from pygeoglim import get_glhymps
        except ImportError as exc:
            raise DataFormatError(
                self.slug,
                "pygeoglim package required: pip install pygeoglim\n"
                "Provides local access to GLHYMPS permeability and porosity data.",
            ) from exc

        bbox = geometry_to_bbox(geometry)
        center_lon = (bbox[0] + bbox[2]) / 2
        center_lat = (bbox[1] + bbox[3]) / 2

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                _glhymps_pool, get_glhymps, center_lat, center_lon,
            )
            if var_key == "permeability":
                value = result.get("logK_Ferr_", result.get("logK_Ice_", None))
            else:
                value = result.get("Porosity_x", result.get("Porosity", None))

            if value is not None:
                value = float(value)
        except Exception as e:
            raise DataFormatError(self.slug, f"pygeoglim query failed: {e}") from e

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        quality = QualityFlag.GOOD if value is not None else QualityFlag.MISSING
        coverage = 1.0 if value is not None else 0.0

        return AttributeResult(
            dataset_id=dataset_id, variable=var_meta.name, value=value,
            units=var_meta.units, aggregation=AggregationMethod.MEAN,
            quality=quality, coverage_fraction=coverage,
            pixel_count=1 if value is not None else 0,
            provider=self.slug, elapsed_ms=elapsed_ms,
            provenance="pygeoglim point query",
        )

