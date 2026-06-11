# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""SYMFLUENCE acquisition-handler adapter for CAS.

This module lets `SYMFLUENCE <https://github.com/DarriEy/SYMFLUENCE>`_ acquire
per-HRU zonal attributes from any CAS dataset as part of its
``acquire_attributes`` workflow step. It is discovered automatically through
the ``symfluence.plugins`` entry point declared in CAS's ``pyproject.toml``;
a plain ``import symfluence`` (with CAS installed in the same environment)
calls :func:`register`, which

* registers :class:`CASAttributeAcquirer` in SYMFLUENCE's acquisition registry
  under the key ``'CAS'``, and
* appends a ``ProfileDataset`` entry to **every** attribute profile
  (``core`` / ``camels_spat`` / ``full``), so the handler runs whatever profile
  is selected.

Auto-registration in every profile is safe because the handler is a strict
no-op until the user sets ``CAS_DATASETS`` in their SYMFLUENCE configuration
(and it can always be disabled outright with ``DOWNLOAD_CAS: false``).

The module is intentionally decoupled, following the climaclass precedent:

* The request-building / CSV-shaping helpers (:func:`parse_dataset_ids`,
  :func:`build_batch_requests`, :func:`responses_to_rows`,
  :func:`quality_summary`) are pure functions with no SYMFLUENCE dependency
  and are unit-tested standalone.
* The SYMFLUENCE base class is resolved defensively at import time; if
  SYMFLUENCE is absent the class still imports (its base degrades to
  ``object``) so ``import cas`` never fails.

Output contract
---------------
``download()`` writes ``{DOMAIN_NAME}_cas_attributes.csv`` into the directory
SYMFLUENCE passes it (``data/attributes/cas/`` by profile convention): one row
per HRU sorted by HRU id, one numeric column per CAS dataset (categorical
``distribution`` results expand to one ``{dataset}_{class}`` fraction column
per class), mirroring the shape of SYMFLUENCE's attribute-processor CSVs. An
explicit ``hru_id`` column plus per-dataset ``*_units`` / ``*_quality`` /
``*_coverage_fraction`` columns are carried as extras.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from cas.core.models import AttributeResponse, BatchAttributeRequest, Geometry

# Resolve the SYMFLUENCE base class defensively so importing this module (and
# therefore ``import cas``) never hard-fails when SYMFLUENCE is not installed.
try:  # pragma: no cover - exercised only with SYMFLUENCE present
    from symfluence.data.acquisition.base import BaseAcquisitionHandler as _Base

    HAVE_SYMFLUENCE = True
except Exception:  # noqa: BLE001 - any import failure means "not available"
    _Base = object  # type: ignore[assignment, misc]
    HAVE_SYMFLUENCE = False

#: Hard limit on geometries per request (``BatchAttributeRequest`` constraint).
MAX_GEOMETRIES_PER_REQUEST = 1000

#: Registry key and profile entry constants (single source for register()).
HANDLER_NAME = "CAS"
PROFILE_OUTPUT_SUBDIR = "cas"
PROFILE_OVERRIDE_KEY = "DOWNLOAD_CAS"
PROFILE_DESCRIPTION = "Community Attribute Service (CAS) zonal attributes"


# ---------------------------------------------------------------------------
# Pure helpers (no SYMFLUENCE dependency — unit-testable standalone)
# ---------------------------------------------------------------------------


def parse_dataset_ids(raw: Any) -> list[str]:
    """Parse the ``CAS_DATASETS`` config value into a list of dataset ids.

    Accepts a comma-separated string (``"copernicus_dem:elevation,
    isric_soilgrids:clay_0-5cm"``) or an already-split list/tuple. Empty or
    ``None`` input yields an empty list (the handler's no-op signal).
    """
    if raw is None:
        return []
    items: Iterable[Any] = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def sanitize_column(name: str) -> str:
    """Turn a CAS dataset id (or class label) into a safe CSV column name.

    ``"isric_soilgrids:clay_0-5cm"`` becomes ``"isric_soilgrids_clay_0_5cm"``.
    """
    return re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_")


def build_batch_requests(
    geometries: Sequence[Any],
    dataset_ids: Sequence[str],
    aggregation: str = "mean",
    chunk_size: int = MAX_GEOMETRIES_PER_REQUEST,
) -> list[BatchAttributeRequest]:
    """Chunk geometries into valid :class:`BatchAttributeRequest` objects.

    ``BatchAttributeRequest`` accepts up to :data:`MAX_GEOMETRIES_PER_REQUEST`
    geometries but any number of dataset ids, so the most efficient legal
    shape is one request per geometry chunk covering *all* datasets.

    Args:
        geometries: ``cas.Geometry`` instances or GeoJSON-like mappings
            (``{"type": ..., "coordinates": ...}``) in EPSG:4326.
        dataset_ids: CAS dataset ids to extract for every geometry.
        aggregation: Zonal aggregation method (CAS ``AggregationMethod``).
        chunk_size: Maximum geometries per request.

    Returns:
        Requests in geometry order; concatenating their per-geometry
        responses restores the input order.
    """
    if chunk_size < 1 or chunk_size > MAX_GEOMETRIES_PER_REQUEST:
        raise ValueError(f"chunk_size must be in [1, {MAX_GEOMETRIES_PER_REQUEST}], got {chunk_size}")
    geoms = [g if isinstance(g, Geometry) else Geometry(**dict(g)) for g in geometries]
    return [
        BatchAttributeRequest(
            geometries=geoms[i : i + chunk_size],
            dataset_ids=list(dataset_ids),
            aggregation=aggregation,  # type: ignore[arg-type]
        )
        for i in range(0, len(geoms), chunk_size)
    ]


def responses_to_rows(
    hru_ids: Sequence[Any],
    responses: Sequence[AttributeResponse],
    include_metadata: bool = True,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Shape per-geometry CAS responses into per-HRU CSV rows.

    Args:
        hru_ids: HRU ids aligned 1:1 with *responses* (i.e. with the geometry
            order the requests were built from).
        responses: Flattened per-geometry ``AttributeResponse`` list, e.g. the
            concatenated ``batch.responses`` of every chunked batch call.
        include_metadata: Carry ``*_units`` / ``*_quality`` /
            ``*_coverage_fraction`` columns alongside each value column.

    Returns:
        ``(fieldnames, rows)`` where rows are dicts sorted by ``str(hru_id)``
        — the same row order SYMFLUENCE's attribute processors produce — and
        fieldnames preserve first-encounter column order with ``hru_id``
        first. Scalar results give one column per dataset; class-fraction
        (``distribution``) results give one column per class.
    """
    if len(hru_ids) != len(responses):
        raise ValueError(
            f"Got {len(responses)} CAS responses for {len(hru_ids)} HRUs; "
            "responses must align 1:1 with the requested geometries"
        )

    fieldnames: list[str] = ["hru_id"]
    seen = set(fieldnames)

    def _set(row: dict[str, Any], column: str, value: Any) -> None:
        if column not in seen:
            seen.add(column)
            fieldnames.append(column)
        row[column] = value

    rows: list[dict[str, Any]] = []
    for hru_id, response in zip(hru_ids, responses):
        row: dict[str, Any] = {"hru_id": hru_id}
        for result in response.results:
            base = sanitize_column(result.dataset_id)
            if isinstance(result.value, dict):
                for class_label, fraction in result.value.items():
                    _set(row, f"{base}_{sanitize_column(str(class_label))}", fraction)
            elif result.value is not None:
                _set(row, base, result.value)
            if include_metadata:
                _set(row, f"{base}_units", result.units)
                _set(row, f"{base}_quality", str(result.quality))
                _set(row, f"{base}_coverage_fraction", result.coverage_fraction)
        rows.append(row)

    rows.sort(key=lambda r: str(r["hru_id"]))
    return fieldnames, rows


def quality_summary(responses: Sequence[AttributeResponse]) -> dict[str, int]:
    """Count CAS quality flags across all results (for log summaries)."""
    return dict(Counter(str(result.quality) for response in responses for result in response.results))


# ---------------------------------------------------------------------------
# SYMFLUENCE acquisition handler
# ---------------------------------------------------------------------------


class CASAttributeAcquirer(_Base):  # type: ignore[misc, valid-type]
    """SYMFLUENCE acquisition handler extracting per-HRU attributes via CAS.

    Invoked by SYMFLUENCE's ``acquire_attributes`` step (profile dispatch)
    with ``output_dir = {project_dir}/data/attributes/cas/``. Reads flat
    config keys:

    ``CAS_DATASETS``
        Required. Comma-separated CAS dataset ids, e.g.
        ``"copernicus_dem:elevation,isric_soilgrids:clay_0-5cm"``. When unset
        or empty the handler logs an info message and returns *output_dir*
        unchanged (no-op), which is what makes auto-profile-registration safe.
    ``CAS_AGGREGATION``
        Optional zonal aggregation method (default ``'mean'``).
    ``CAS_API_CONFIG``
        Optional mapping of CAS settings passed to ``cas.configure()``
        (e.g. ``{provider_timeout_s: 60}``).
    """

    def download(self, output_dir: Path) -> Path:
        """Extract CAS attributes for every HRU and write a per-HRU CSV."""
        if not HAVE_SYMFLUENCE:  # pragma: no cover - guard for standalone use
            raise RuntimeError(
                "CASAttributeAcquirer requires SYMFLUENCE. "
                "Install SYMFLUENCE in the same environment as CAS."
            )

        dataset_ids = parse_dataset_ids(
            self._get_config_value(lambda: None, default=None, dict_key="CAS_DATASETS")
        )
        if not dataset_ids:
            self.logger.info(
                "CAS adapter installed but CAS_DATASETS not configured; skipping"
            )
            return output_dir

        aggregation = self._get_config_value(lambda: None, default="mean", dict_key="CAS_AGGREGATION")
        api_config = self._get_config_value(lambda: None, default=None, dict_key="CAS_API_CONFIG")

        domain_name = self._get_config_value(
            lambda: self.config.domain.name, default="domain", dict_key="DOMAIN_NAME"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"{domain_name}_cas_attributes.csv"
        if self._skip_if_exists(out_path):
            return out_path

        import cas

        if api_config:
            cas.configure(**dict(api_config))

        gdf, hru_ids = self._load_hru_geometries()
        geometries = self._to_cas_geometries(gdf)

        self.logger.info(
            f"CAS extraction: {len(dataset_ids)} dataset(s) x {len(geometries)} HRU(s) "
            f"(aggregation={aggregation})"
        )

        responses: list[AttributeResponse] = []
        for request in build_batch_requests(geometries, dataset_ids, aggregation=str(aggregation)):
            batch = cas.batch_extract_sync(request)
            responses.extend(batch.responses)

        summary = quality_summary(responses)
        log = self.logger.info if set(summary) <= {"good"} else self.logger.warning
        log(f"CAS extraction quality summary: {summary or 'no results returned'}")

        fieldnames, rows = responses_to_rows(hru_ids, responses)
        self._write_csv(out_path, fieldnames, rows)
        self.logger.info(f"Wrote {len(fieldnames) - 1} columns x {len(rows)} HRUs to {out_path}")
        return out_path

    # -- internals ----------------------------------------------------------

    def _load_hru_geometries(self):
        """Load the domain's HRU polygons in EPSG:4326 plus their ids.

        Follows the same config keys and path conventions as SYMFLUENCE's
        ``BaseAttributeProcessor._get_catchment_path``.
        """
        import geopandas as gpd  # lazy import, per SYMFLUENCE convention

        catchment_path = self._find_catchment_path()
        if not catchment_path.exists():
            raise FileNotFoundError(
                f"No catchment/HRU shapefile found at {catchment_path}; "
                "run the domain definition/discretization steps first"
            )

        gdf = gpd.read_file(catchment_path)
        if gdf.crs is None:
            self.logger.warning(
                f"{catchment_path} has no CRS; assuming EPSG:4326 (CAS requires lon/lat)"
            )
        elif gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(4326)

        hru_id_field = self._get_config_value(
            lambda: self.config.paths.catchment_hruid, default="HRU_ID", dict_key="CATCHMENT_SHP_HRUID"
        )
        if hru_id_field in gdf.columns:
            hru_ids = gdf[hru_id_field].tolist()
        elif "GRU_ID" in gdf.columns:
            self.logger.warning(f"No '{hru_id_field}' column in {catchment_path.name}; using GRU_ID")
            hru_ids = gdf["GRU_ID"].tolist()
        else:
            self.logger.warning(
                f"No '{hru_id_field}' or 'GRU_ID' column in {catchment_path.name}; using row order"
            )
            hru_ids = list(range(len(gdf)))
        return gdf, hru_ids

    def _find_catchment_path(self) -> Path:
        """Locate the catchment shapefile (mirrors BaseAttributeProcessor)."""
        catchment_path = self._get_config_value(
            lambda: self.config.paths.catchment_path, default="default", dict_key="CATCHMENT_PATH"
        )
        catchment_name = self._get_config_value(
            lambda: self.config.paths.catchment_shp_name, default="default", dict_key="CATCHMENT_SHP_NAME"
        )
        domain_name = self._get_config_value(
            lambda: self.config.domain.name, default="domain", dict_key="DOMAIN_NAME"
        )

        if catchment_path in (None, "default"):
            base = Path(self.project_dir) / "shapefiles" / "catchment"
        else:
            base = Path(catchment_path)

        if catchment_name in (None, "default"):
            discretization = self._get_config_value(
                lambda: self.config.domain.discretization, dict_key="SUB_GRID_DISCRETIZATION"
            )
            catchment_file = f"{domain_name}_HRUs_{discretization}.shp"
        else:
            catchment_file = str(catchment_name)

        direct = base / catchment_file
        if direct.exists():
            return direct

        if base.exists():
            # discretize_domain writes to {method}/{experiment_id}/ subdirs
            matches = sorted(base.rglob(catchment_file))
            if matches:
                return matches[0]
            fallback = sorted(base.rglob(f"{domain_name}_HRUs_*.shp"))
            if fallback:
                return fallback[0]

        return direct

    def _to_cas_geometries(self, gdf) -> list[Geometry]:
        """Convert GeoDataFrame geometries to CAS ``Geometry`` models."""
        geometries: list[Geometry] = []
        for geom in gdf.geometry:
            mapping = geom.__geo_interface__
            if mapping["type"] not in ("Point", "Polygon", "MultiPolygon"):
                raise ValueError(
                    f"Unsupported geometry type {mapping['type']!r}; "
                    "CAS accepts Point, Polygon, and MultiPolygon"
                )
            geometries.append(Geometry(type=mapping["type"], coordinates=list(mapping["coordinates"])))
        return geometries

    @staticmethod
    def _write_csv(out_path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
        import csv

        with out_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, restval="")
            writer.writeheader()
            writer.writerows(rows)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register() -> None:
    """Register the CAS adapter with SYMFLUENCE (``symfluence.plugins`` hook).

    Zero-arg callable invoked by SYMFLUENCE's plugin discovery at
    ``import symfluence``. Idempotent: safe to call repeatedly.

    * Adds :class:`CASAttributeAcquirer` to ``R.acquisition_handlers`` under
      ``'CAS'`` (skipped when already present).
    * Appends one ``ProfileDataset`` entry to every list in
      ``attribute_profiles.PROFILES`` (guarded against double-append).
      ``PROFILES`` is read at call time by the acquisition service, so
      register-time appends take effect.
    """
    if not HAVE_SYMFLUENCE:  # pragma: no cover - discovery never calls us then
        return

    from symfluence.core.registries import R
    from symfluence.data.acquisition import attribute_profiles

    if HANDLER_NAME not in R.acquisition_handlers:
        R.acquisition_handlers.add(HANDLER_NAME, CASAttributeAcquirer)

    entry = attribute_profiles.ProfileDataset(
        handler_name=HANDLER_NAME,
        description=PROFILE_DESCRIPTION,
        output_subdir=PROFILE_OUTPUT_SUBDIR,
        config_override_key=PROFILE_OVERRIDE_KEY,
    )
    for datasets in attribute_profiles.PROFILES.values():
        if not any(ds.handler_name == HANDLER_NAME for ds in datasets):
            datasets.append(entry)


# Self-register at import time. This matters for the *reverse* import order:
# when this module is imported before symfluence, the defensive base-class
# import above triggers SYMFLUENCE's bootstrap, whose entry-point discovery
# re-enters this module while it is still partially initialized, cannot find
# ``register`` yet, and skips the plugin. Registering here — after the module
# body is complete — covers that case; in the normal order (``import
# symfluence`` first) discovery calls ``register()`` again, which is
# idempotent.
if HAVE_SYMFLUENCE:  # pragma: no cover - exercised only with SYMFLUENCE present
    register()
