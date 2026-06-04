# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""TerraClimate monthly climate & water-balance via Microsoft Planetary Computer.

TerraClimate is a global monthly dataset of climate and climatic water-balance
variables at ~4 km (1/24°), 1958–present (Abatzoglou et al. 2018). On Planetary
Computer it is published as a single collection-level **Zarr** datacube on Azure
Blob — not per-scene COGs — so it is read with xarray via the
``ZarrStoreMixin`` rather than the STAC+COG path.

It fills the catalog's missing arid-zone / water-stress axis: potential
evapotranspiration (``pet``), climatic water deficit (``def``), soil moisture
(``soil``), the Palmer Drought Severity Index (``pdsi``), plus precipitation,
actual ET, runoff and temperature. A derived ``aridity`` dataset reports the
UNEP aridity index (annual precipitation / annual PET) over the most recent
complete year.

The ``xarray``/``zarr`` stack lives in the optional ``climate`` extra and is
imported lazily (see ``ZarrStoreMixin``), so importing this module — and hence
the registry ``discover()`` — never requires the extra; only ``extract()`` does.
"""

from __future__ import annotations

import time

import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cas.connectors.base import BaseConnector
from cas.connectors.protocols.zarr_store import ZarrStoreMixin
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

logger = structlog.get_logger(__name__)


def _is_transient_open_error(exc: BaseException) -> bool:
    """Retry a Zarr/STAC open on any error except the missing-extra config error."""
    return not (isinstance(exc, RuntimeError) and "climate' extra" in str(exc))


PC_CATALOG = "https://planetarycomputer.microsoft.com/api/stac/v1"
TC_COLLECTION = "terraclimate"
TC_ASSET = "zarr-abfs"  # Azure-Blob Zarr; signed via planetary_computer
TC_RESOLUTION_M = 4638.0  # 1/24 degree at the equator

# Curated subset of TerraClimate variables exposed as datasets.
# key -> (zarr variable name, units, human description)
TC_VARS: dict[str, tuple[str, str, str]] = {
    "pet": ("pet", "mm", "Reference (potential) evapotranspiration"),
    "aet": ("aet", "mm", "Actual evapotranspiration"),
    "deficit": ("def", "mm", "Climatic water deficit (pet - aet)"),
    "soil_moisture": ("soil", "mm", "Soil moisture"),
    "precipitation": ("ppt", "mm", "Accumulated precipitation"),
    "runoff": ("q", "mm", "Runoff"),
    "pdsi": ("pdsi", "unitless", "Palmer Drought Severity Index"),
    "tmax": ("tmax", "degC", "Maximum temperature"),
    "tmin": ("tmin", "degC", "Minimum temperature"),
    "vpd": ("vpd", "kPa", "Vapor pressure deficit"),
}

# Derived index (computed from ppt and pet over the latest complete year).
ARIDITY_KEY = "aridity"


@register("terraclimate")
class TerraClimateConnector(ZarrStoreMixin, BaseConnector):
    """TerraClimate climate & water-balance variables (PC Zarr datacube)."""

    slug = "terraclimate"
    display_name = "TerraClimate Climate & Water Balance"
    base_url = PC_CATALOG
    protocol = "opendap"  # closest enum value: a remote multidim datacube, not COG

    async def list_datasets(self) -> list[Dataset]:
        bbox = BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90)
        datasets = [
            Dataset(
                id=f"{self.slug}:{key}",
                provider=self.slug,
                name=f"TerraClimate {desc}",
                description=f"{desc} — TerraClimate ~4km monthly ({units})",
                variables=[
                    Variable(name=key, units=units, data_type=DataType.CONTINUOUS, description=desc)
                ],
                resolution_m=TC_RESOLUTION_M,
                crs="EPSG:4326",
                bbox=bbox,
                temporal=TemporalExtent(temporal_type=TemporalType.MONTHLY),
                protocol=Protocol.OPENDAP,
                license="CC0-1.0",
                citation="Abatzoglou et al. (2018), TerraClimate; via Microsoft Planetary Computer",
            )
            for key, (_var, units, desc) in TC_VARS.items()
        ]
        datasets.append(
            Dataset(
                id=f"{self.slug}:{ARIDITY_KEY}",
                provider=self.slug,
                name="TerraClimate Aridity Index",
                description="UNEP aridity index (annual precipitation / annual PET), latest full year",
                variables=[
                    Variable(
                        name=ARIDITY_KEY,
                        units="unitless",
                        data_type=DataType.CONTINUOUS,
                        valid_range=(0.0, 10.0),
                        description="Aridity index = annual P / annual PET (lower = more arid)",
                    )
                ],
                resolution_m=TC_RESOLUTION_M,
                crs="EPSG:4326",
                bbox=bbox,
                temporal=TemporalExtent(temporal_type=TemporalType.ANNUAL),
                protocol=Protocol.OPENDAP,
                license="CC0-1.0",
                citation="Abatzoglou et al. (2018), TerraClimate; via Microsoft Planetary Computer",
            )
        )
        return datasets

    @retry(
        retry=retry_if_exception(_is_transient_open_error),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _open_store(self):
        """Resolve and open the signed TerraClimate Zarr datacube (blocking).

        The open is three sequential network steps (STAC client, collection
        fetch, Zarr handshake); a transient blip on any of them is retried so a
        slow upstream surfaces as a delayed success rather than a flaky DOWN.
        """
        import planetary_computer
        import pystac_client

        catalog = pystac_client.Client.open(
            PC_CATALOG, modifier=planetary_computer.sign_inplace
        )
        collection = catalog.get_collection(TC_COLLECTION)
        asset = collection.assets[TC_ASSET]
        open_kwargs = dict(asset.extra_fields.get("xarray:open_kwargs", {}))
        # Drop dask chunking so we read lazily through the zarr store itself
        # (chunk-level laziness) without requiring the optional dask dependency.
        open_kwargs.pop("chunks", None)
        return self._open_zarr(asset.href, open_kwargs=open_kwargs)

    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        start_time = time.monotonic()
        _, _, key = dataset_id.partition(":")
        if key != ARIDITY_KEY and key not in TC_VARS:
            raise DataFormatError(self.slug, f"Unknown dataset: {key}")

        bbox = self._geometry_to_bbox(geometry)

        try:
            ds = self._open_store()
        except Exception as e:
            raise DataFormatError(self.slug, f"Zarr open failed: {e}") from e

        try:
            if key == ARIDITY_KEY:
                value, n_cells = self._compute_aridity(ds, bbox)
                units = "unitless"
                var_name = ARIDITY_KEY
            else:
                var_name, units, _desc = TC_VARS[key]
                value, n_cells = self._reduce_variable(ds, var_name, bbox, time_range)
        except Exception as e:
            raise DataFormatError(self.slug, f"TerraClimate read failed: {e}") from e
        finally:
            ds.close()

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        if value is None:
            return AttributeResult(
                dataset_id=dataset_id,
                variable=key,
                value=None,
                units=units,
                aggregation=AggregationMethod.MEAN,
                quality=QualityFlag.MISSING,
                coverage_fraction=0.0,
                pixel_count=0,
                provider=self.slug,
                elapsed_ms=elapsed_ms,
                provenance="TerraClimate Zarr (Planetary Computer)",
            )

        return AttributeResult(
            dataset_id=dataset_id,
            variable=key,
            value=value,
            units=units,
            aggregation=AggregationMethod.MEAN,
            quality=QualityFlag.GOOD,
            coverage_fraction=1.0,
            pixel_count=n_cells,
            provider=self.slug,
            elapsed_ms=elapsed_ms,
            provenance=f"TerraClimate Zarr (Planetary Computer): {var_name}",
        )

    def _reduce_variable(self, ds, var_name, bbox, time_range):
        """Spatial+temporal mean of a single variable. Returns (value, n_cells)."""
        import numpy as np

        sub = self._spatial_window(ds, var_name, bbox)
        iso_start = iso_end = None
        if time_range is not None:
            iso_start = time_range.start.isoformat()
            iso_end = time_range.end.isoformat()
        sub = self._select_time(sub, iso_start=iso_start, iso_end=iso_end)

        arr = np.asarray(sub.values, dtype="float64")
        n_cells = int(arr.size)
        if n_cells == 0 or np.all(np.isnan(arr)):
            return None, 0
        return float(np.nanmean(arr)), n_cells

    def _compute_aridity(self, ds, bbox):
        """UNEP aridity index = annual P / annual PET over the latest full year."""
        import numpy as np

        ppt = self._spatial_window(ds, "ppt", bbox)
        pet = self._spatial_window(ds, "pet", bbox)
        # Latest 12 monthly steps = most recent ~full year.
        if "time" in ppt.dims:
            ppt = ppt.isel(time=slice(-12, None)).sum(dim="time")
            pet = pet.isel(time=slice(-12, None)).sum(dim="time")

        p = np.asarray(ppt.values, dtype="float64")
        e = np.asarray(pet.values, dtype="float64")
        if p.size == 0 or np.all(np.isnan(p)) or np.all(np.isnan(e)):
            return None, 0
        with np.errstate(divide="ignore", invalid="ignore"):
            ai = np.where(e > 0, p / e, np.nan)
        if np.all(np.isnan(ai)):
            return None, 0
        return float(np.nanmean(ai)), int(ai.size)
