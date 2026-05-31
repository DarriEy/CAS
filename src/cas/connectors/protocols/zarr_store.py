# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Zarr datacube access mixin (xarray over cloud object storage).

Some providers publish a single analysis-ready datacube as Zarr rather than
per-scene Cloud-Optimized GeoTIFFs — e.g. TerraClimate on Microsoft Planetary
Computer is one collection-level Zarr store on Azure Blob, not COG items. This
mixin opens such a store with ``xarray`` and reads a small spatial/temporal
window, so a point or small-polygon extract pulls only the overlapping chunks.

The heavy dependencies (``xarray``, ``zarr``, ``fsspec``, ``adlfs``) live in the
optional ``climate`` extra and are imported lazily *inside* the methods — never
at module top — so the connector module stays importable (and the registry's
``discover()`` keeps working) even when the extra is not installed. A connector
using this mixin only fails at ``extract()`` time, with a clear message, when
the extra is missing.
"""

from __future__ import annotations

from typing import Any

import structlog

from cas.core.models import Geometry

logger = structlog.get_logger(__name__)


class ZarrStoreMixin:
    """Mixin for reading a cloud-hosted Zarr datacube via xarray."""

    @staticmethod
    def _geometry_to_bbox(geometry: Geometry) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat) for a geometry.

        Mirrors ``STACMixin._geometry_to_bbox`` so Zarr connectors don't depend
        on the COG mixin; a Point is buffered to a tiny box.
        """
        if geometry.type == "Point":
            lon, lat = geometry.coordinates[0], geometry.coordinates[1]
            buf = 0.001
            return (lon - buf, lat - buf, lon + buf, lat + buf)
        if geometry.type == "Polygon":
            coords = geometry.coordinates[0]
        else:  # MultiPolygon
            coords = [c for ring in geometry.coordinates for c in ring[0]]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        return (min(lons), min(lats), max(lons), max(lats))

    @staticmethod
    def _require_climate_extra() -> Any:
        """Import xarray lazily, raising a clear error if the extra is absent."""
        try:
            import xarray as xr
        except ImportError as e:  # pragma: no cover - exercised only without extra
            raise RuntimeError(
                "Zarr datacube access requires the 'climate' extra: "
                "pip install -e '.[climate]' (xarray, zarr, fsspec, adlfs)"
            ) from e
        return xr

    def _open_zarr(
        self,
        href: str,
        storage_options: dict | None = None,
        open_kwargs: dict | None = None,
    ) -> Any:
        """Open a Zarr store as an xarray Dataset.

        ``href`` / ``storage_options`` / ``open_kwargs`` come straight from a
        STAC asset's ``xarray:*`` extra fields (Planetary Computer style). The
        store is opened lazily (dask-backed) so only requested chunks transfer.
        """
        xr = self._require_climate_extra()
        kwargs = dict(open_kwargs or {})
        # The STAC asset advertises engine/consolidated; default to zarr.
        kwargs.setdefault("engine", "zarr")
        if storage_options is not None:
            kwargs["storage_options"] = storage_options
        return xr.open_dataset(href, **kwargs)

    @staticmethod
    def _spatial_window(
        ds: Any,
        variable: str,
        bbox: tuple[float, float, float, float],
        lat_name: str = "lat",
        lon_name: str = "lon",
    ) -> Any:
        """Select the ``variable`` over a lon/lat bbox.

        Handles descending latitude axes (common in climate cubes) by ordering
        the ``slice`` to match the coordinate's direction. Falls back to a
        nearest-cell selection when the bbox is narrower than one grid cell, so
        a point geometry still returns the covering cell rather than an empty
        slice.
        """
        min_lon, min_lat, max_lon, max_lat = bbox
        da = ds[variable]

        lats = da[lat_name].values
        lat_slice = (
            slice(max_lat, min_lat) if len(lats) > 1 and lats[0] > lats[-1] else slice(min_lat, max_lat)
        )
        lons = da[lon_name].values
        lon_slice = (
            slice(max_lon, min_lon) if len(lons) > 1 and lons[0] > lons[-1] else slice(min_lon, max_lon)
        )

        sub = da.sel({lat_name: lat_slice, lon_name: lon_slice})
        if sub.sizes.get(lat_name, 0) == 0 or sub.sizes.get(lon_name, 0) == 0:
            # bbox smaller than a cell — snap to the nearest cell centre.
            clat = (min_lat + max_lat) / 2.0
            clon = (min_lon + max_lon) / 2.0
            sub = da.sel({lat_name: clat, lon_name: clon}, method="nearest")
        return sub

    @staticmethod
    def _select_time(da: Any, time_name: str = "time", iso_start: str | None = None,
                     iso_end: str | None = None) -> Any:
        """Select a time window (mean over it), or the latest step if unset."""
        if time_name not in da.dims and time_name not in da.coords:
            return da
        if iso_start or iso_end:
            return da.sel({time_name: slice(iso_start, iso_end)})
        # Default: most recent time step.
        return da.isel({time_name: -1})
