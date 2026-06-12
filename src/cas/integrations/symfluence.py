# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""SYMFLUENCE integration for CAS.

This module lets `SYMFLUENCE <https://github.com/DarriEy/SYMFLUENCE>`_ source
per-HRU zonal attributes from any CAS dataset. It plugs into SYMFLUENCE at two
seams, both declared as entry points in CAS's ``pyproject.toml``:

**Primary — attribute processor** (``symfluence.attribute_processors``)
    :class:`CASAttributeProcessor` subclasses SYMFLUENCE's
    ``BaseAttributeProcessor`` and is discovered by
    ``discover_attribute_plugins()``. SYMFLUENCE's attribute machinery
    (``attributeProcessor._process_plugin_attributes``) constructs it with
    ``(config, logger)`` and merges its ``.process() -> dict`` output into the
    same results dict the in-tree elevation/soil/climate processors feed, so
    CAS attributes flow into the merged per-HRU attribute table like any
    native attribute. Gate with ``ATTRIBUTE_PLUGINS_ENABLED`` /
    ``ATTRIBUTE_PLUGINS_EXCLUDE`` on the SYMFLUENCE side.

**Secondary — acquisition handler** (``symfluence.plugins``)
    :func:`register` adds :class:`CASAttributeAcquirer` to SYMFLUENCE's
    acquisition registry under the key ``'CAS'`` for *explicit* use (custom
    profiles, scripted acquisition). It writes an analysis-oriented per-HRU
    CSV to ``data/attributes/cas/``; it is **not** auto-appended to the
    built-in attribute profiles — the processor seam supersedes that.

Both seams are strict no-ops until ``CAS_DATASETS`` is set in the SYMFLUENCE
configuration.

The module is intentionally decoupled, following the climaclass precedent:

* The request-building / result-shaping helpers (:func:`parse_dataset_ids`,
  :func:`build_batch_requests`, :func:`responses_to_attributes`,
  :func:`responses_to_rows`, :func:`quality_summary`) are pure functions with
  no SYMFLUENCE dependency and are unit-tested standalone.
* The SYMFLUENCE base classes are resolved defensively at import time; if
  SYMFLUENCE is absent the classes still import (their bases degrade to
  ``object``) so ``import cas`` never fails.

Output contracts
----------------
``CASAttributeProcessor.process()`` returns the flat dict shape SYMFLUENCE's
attribute pipeline consumes: ``{"cas.{dataset}": value}`` for lumped domains,
``{"HRU_{id}_cas.{dataset}": value}`` for distributed ones (categorical
``distribution`` results expand to one ``cas.{dataset}_{class}`` key per
class; ``*_quality`` / ``*_coverage_fraction`` metadata keys ride along).

``CASAttributeAcquirer.download()`` writes ``{DOMAIN_NAME}_cas_attributes.csv``
into the directory SYMFLUENCE passes it: one row per HRU sorted by HRU id,
one numeric column per CAS dataset, plus an explicit ``hru_id`` column and
per-dataset ``*_units`` / ``*_quality`` / ``*_coverage_fraction`` extras.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from cas.core.models import AttributeResponse, BatchAttributeRequest, Geometry

# Resolve the SYMFLUENCE base classes defensively so importing this module
# (and therefore ``import cas``) never hard-fails when SYMFLUENCE is missing.
try:  # pragma: no cover - exercised only with SYMFLUENCE present
    from symfluence.data.acquisition.base import BaseAcquisitionHandler as _AcquirerBase
    from symfluence.data.preprocessing.attribute_processors.base import (
        BaseAttributeProcessor as _ProcessorBase,
    )

    HAVE_SYMFLUENCE = True
except Exception:  # noqa: BLE001 - any import failure means "not available"
    _AcquirerBase = object  # type: ignore[assignment, misc]
    _ProcessorBase = object  # type: ignore[assignment, misc]
    HAVE_SYMFLUENCE = False

#: Hard limit on geometries per request (``BatchAttributeRequest`` constraint).
MAX_GEOMETRIES_PER_REQUEST = 1000

#: Acquisition-registry key for the secondary (handler) seam.
HANDLER_NAME = "CAS"

#: Namespace prefix for attribute keys returned by the processor seam,
#: mirroring the in-tree ``elevation.`` / ``soil.`` / ``climate.`` categories.
ATTRIBUTE_NAMESPACE = "cas."

_NO_DATASETS_MESSAGE = "CAS_DATASETS not configured; skipping"


# ---------------------------------------------------------------------------
# Pure helpers (no SYMFLUENCE dependency — unit-testable standalone)
# ---------------------------------------------------------------------------


def parse_dataset_ids(raw: Any) -> list[str]:
    """Parse the ``CAS_DATASETS`` config value into a list of dataset ids.

    Accepts a comma-separated string (``"copernicus_dem:elevation,
    isric_soilgrids:clay_0-5cm"``) or an already-split list/tuple. Empty or
    ``None`` input yields an empty list (the integration's no-op signal).
    """
    if raw is None:
        return []
    items: Iterable[Any] = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def sanitize_column(name: str) -> str:
    """Turn a CAS dataset id (or class label) into a safe attribute/CSV name.

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


def _check_alignment(hru_ids: Sequence[Any], responses: Sequence[AttributeResponse]) -> None:
    if len(hru_ids) != len(responses):
        raise ValueError(
            f"Got {len(responses)} CAS responses for {len(hru_ids)} HRUs; "
            "responses must align 1:1 with the requested geometries"
        )


def responses_to_attributes(
    hru_ids: Sequence[Any],
    responses: Sequence[AttributeResponse],
    lumped: bool = False,
    include_metadata: bool = True,
    logger: Any = None,
) -> dict[str, Any]:
    """Shape per-geometry CAS responses into SYMFLUENCE attribute keys.

    This produces exactly the flat dict SYMFLUENCE's
    ``attributeProcessor._process_plugin_attributes`` merges (via
    ``results.update``) into the in-tree processors' results:

    * lumped domains: plain ``cas.{dataset}`` keys (single attribute row);
    * distributed domains: ``HRU_{id}_cas.{dataset}`` keys, where ``{id}``
      must be an integer — SYMFLUENCE parses it with
      ``int(key.split("_")[1])`` when rebuilding the per-HRU table. Ids that
      cannot be coerced to ``int`` fall back to the geometry's positional
      index (with a warning).

    Categorical ``distribution`` results expand to one
    ``cas.{dataset}_{class}`` fraction key per class. When *include_metadata*
    is true, ``cas.{dataset}_quality`` (string) and
    ``cas.{dataset}_coverage_fraction`` (float) keys ride along — string
    values are tolerated by the consumer (climaclass ships string class codes
    the same way) and non-numeric values are dropped automatically wherever
    SYMFLUENCE writes numeric-only CSVs.

    Args:
        hru_ids: HRU ids aligned 1:1 with *responses* (geometry order).
        responses: Flattened per-geometry ``AttributeResponse`` list.
        lumped: Whether the SYMFLUENCE domain is lumped
            (``DOMAIN_DEFINITION_METHOD == 'lumped'``). With more than one
            geometry, lumped keying would silently overwrite values, so HRU
            prefixes are used anyway (with a warning).
        include_metadata: Carry quality/coverage metadata keys.
        logger: Optional logger for fallback warnings.

    Returns:
        Flat ``{attribute_key: value}`` dict ready for ``.process()``.
    """
    _check_alignment(hru_ids, responses)

    use_prefix = not lumped or len(hru_ids) > 1
    if lumped and len(hru_ids) > 1 and logger is not None:
        logger.warning(
            f"Domain is lumped but {len(hru_ids)} HRU geometries were provided; "
            "using HRU-prefixed attribute keys to avoid overwriting values"
        )

    attributes: dict[str, Any] = {}
    for position, (hru_id, response) in enumerate(zip(hru_ids, responses)):
        if use_prefix:
            try:
                prefix = f"HRU_{int(hru_id)}_"
            except (TypeError, ValueError):
                if logger is not None:
                    logger.warning(
                        f"HRU id {hru_id!r} is not an integer; keying attributes by "
                        f"geometry position {position} instead"
                    )
                prefix = f"HRU_{position}_"
        else:
            prefix = ""

        for result in response.results:
            base = f"{prefix}{ATTRIBUTE_NAMESPACE}{sanitize_column(result.dataset_id)}"
            if isinstance(result.value, dict):
                for class_label, fraction in result.value.items():
                    attributes[f"{base}_{sanitize_column(str(class_label))}"] = fraction
            elif result.value is not None:
                attributes[base] = result.value
            if include_metadata:
                attributes[f"{base}_quality"] = str(result.quality)
                attributes[f"{base}_coverage_fraction"] = result.coverage_fraction

    return attributes


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
    _check_alignment(hru_ids, responses)

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
# Shared geometry helpers (need geopandas, but no SYMFLUENCE)
# ---------------------------------------------------------------------------


def load_catchment_wgs84(catchment_path: Path, logger: Any):
    """Read the HRU/catchment shapefile and normalize it to EPSG:4326."""
    import geopandas as gpd  # lazy import, per SYMFLUENCE convention

    catchment_path = Path(catchment_path)
    if not catchment_path.exists():
        raise FileNotFoundError(
            f"No catchment/HRU shapefile found at {catchment_path}; "
            "run the domain definition/discretization steps first"
        )

    gdf = gpd.read_file(catchment_path)
    if gdf.crs is None:
        logger.warning(f"{catchment_path} has no CRS; assuming EPSG:4326 (CAS requires lon/lat)")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    return gdf


def hru_ids_from_gdf(gdf: Any, hru_id_field: str, logger: Any, source: str = "catchment") -> list[Any]:
    """Extract HRU ids from a catchment GeoDataFrame with sensible fallbacks."""
    if hru_id_field in gdf.columns:
        return list(gdf[hru_id_field])
    if "GRU_ID" in gdf.columns:
        logger.warning(f"No '{hru_id_field}' column in {source}; using GRU_ID")
        return list(gdf["GRU_ID"])
    logger.warning(f"No '{hru_id_field}' or 'GRU_ID' column in {source}; using row order")
    return list(range(len(gdf)))


def to_cas_geometries(gdf: Any) -> list[Geometry]:
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


def _extract_responses(
    geometries: Sequence[Geometry],
    dataset_ids: Sequence[str],
    aggregation: str,
    api_config: Any,
) -> list[AttributeResponse]:
    """Run the chunked ``cas.batch_extract_sync`` calls for *geometries*."""
    import cas

    if api_config:
        cas.configure(**dict(api_config))

    responses: list[AttributeResponse] = []
    for request in build_batch_requests(geometries, dataset_ids, aggregation=aggregation):
        batch = cas.batch_extract_sync(request)
        responses.extend(batch.responses)
    return responses


# ---------------------------------------------------------------------------
# Primary seam: SYMFLUENCE attribute processor
# ---------------------------------------------------------------------------


class CASAttributeProcessor(_ProcessorBase):  # type: ignore[misc, valid-type]
    """SYMFLUENCE attribute processor sourcing per-HRU attributes from CAS.

    Discovered through the ``symfluence.attribute_processors`` entry point and
    run by SYMFLUENCE's attribute machinery alongside the in-tree processors:
    constructed with ``(config, logger)``, its ``.process()`` dict is merged
    into the same results dict, so CAS values land in the per-HRU attribute
    table exactly like native ``elevation.*`` / ``soil.*`` attributes.

    Reads flat config keys:

    ``CAS_DATASETS``
        Required opt-in. Comma-separated CAS dataset ids, e.g.
        ``"copernicus_dem:elevation,isric_soilgrids:clay_0-5cm"``. When unset
        or empty the processor logs an info message and returns ``{}``.
    ``CAS_AGGREGATION``
        Optional zonal aggregation method (default ``'mean'``).
    ``CAS_API_CONFIG``
        Optional mapping of CAS settings passed to ``cas.configure()``.

    HRU polygons come from the catchment shapefile the inherited
    ``BaseAttributeProcessor`` resolves (``self.catchment_path``, via
    ``CATCHMENT_PATH`` / ``CATCHMENT_SHP_NAME`` / the
    ``{DOMAIN_NAME}_HRUs_{discretization}.shp`` convention) — the same
    geometry every in-tree processor reduces over.
    """

    #: SYMFLUENCE plugin-discovery name (entry-point key).
    name = "cas"

    def process(self) -> dict[str, Any]:
        """Extract CAS attributes for every HRU and return them keyed for SYMFLUENCE."""
        if not HAVE_SYMFLUENCE:  # pragma: no cover - guard for standalone use
            raise RuntimeError(
                "CASAttributeProcessor requires SYMFLUENCE. "
                "Install SYMFLUENCE in the same environment as CAS."
            )

        dataset_ids = parse_dataset_ids(
            self._get_config_value(lambda: None, default=None, dict_key="CAS_DATASETS")
        )
        if not dataset_ids:
            self.logger.info(f"CAS attribute processor installed but {_NO_DATASETS_MESSAGE}")
            return {}

        aggregation = str(self._get_config_value(lambda: None, default="mean", dict_key="CAS_AGGREGATION"))
        api_config = self._get_config_value(lambda: None, default=None, dict_key="CAS_API_CONFIG")

        gdf = load_catchment_wgs84(self.catchment_path, self.logger)
        hru_id_field = self._get_config_value(
            lambda: self.config.paths.catchment_hruid, default="HRU_ID", dict_key="CATCHMENT_SHP_HRUID"
        )
        hru_ids = hru_ids_from_gdf(gdf, hru_id_field, self.logger, source=Path(self.catchment_path).name)
        geometries = to_cas_geometries(gdf)

        self.logger.info(
            f"CAS attribute extraction: {len(dataset_ids)} dataset(s) x {len(geometries)} HRU(s) "
            f"(aggregation={aggregation})"
        )
        responses = _extract_responses(geometries, dataset_ids, aggregation, api_config)

        summary = quality_summary(responses)
        log = self.logger.info if set(summary) <= {"good"} else self.logger.warning
        log(f"CAS extraction quality summary: {summary or 'no results returned'}")

        attributes = responses_to_attributes(
            hru_ids, responses, lumped=self._is_lumped(), logger=self.logger
        )
        self.logger.info(f"CAS contributed {len(attributes)} attribute keys")
        return attributes


# ---------------------------------------------------------------------------
# Secondary seam: SYMFLUENCE acquisition handler (analysis CSV export)
# ---------------------------------------------------------------------------


class CASAttributeAcquirer(_AcquirerBase):  # type: ignore[misc, valid-type]
    """SYMFLUENCE acquisition handler exporting per-HRU CAS attributes to CSV.

    Registered under ``'CAS'`` in SYMFLUENCE's acquisition registry for
    *explicit* use — reference it from a custom attribute profile or invoke it
    directly; it no longer auto-joins the built-in profiles (the
    :class:`CASAttributeProcessor` seam is the primary integration). Reads the
    same ``CAS_DATASETS`` / ``CAS_AGGREGATION`` / ``CAS_API_CONFIG`` config
    keys and is likewise a no-op until ``CAS_DATASETS`` is set.
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
            self.logger.info(f"CAS adapter installed but {_NO_DATASETS_MESSAGE}")
            return output_dir

        aggregation = str(self._get_config_value(lambda: None, default="mean", dict_key="CAS_AGGREGATION"))
        api_config = self._get_config_value(lambda: None, default=None, dict_key="CAS_API_CONFIG")

        domain_name = self._get_config_value(
            lambda: self.config.domain.name, default="domain", dict_key="DOMAIN_NAME"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"{domain_name}_cas_attributes.csv"
        if self._skip_if_exists(out_path):
            return out_path

        catchment_path = self._find_catchment_path()
        gdf = load_catchment_wgs84(catchment_path, self.logger)
        hru_id_field = self._get_config_value(
            lambda: self.config.paths.catchment_hruid, default="HRU_ID", dict_key="CATCHMENT_SHP_HRUID"
        )
        hru_ids = hru_ids_from_gdf(gdf, hru_id_field, self.logger, source=catchment_path.name)
        geometries = to_cas_geometries(gdf)

        self.logger.info(
            f"CAS extraction: {len(dataset_ids)} dataset(s) x {len(geometries)} HRU(s) "
            f"(aggregation={aggregation})"
        )
        responses = _extract_responses(geometries, dataset_ids, aggregation, api_config)

        summary = quality_summary(responses)
        log = self.logger.info if set(summary) <= {"good"} else self.logger.warning
        log(f"CAS extraction quality summary: {summary or 'no results returned'}")

        fieldnames, rows = responses_to_rows(hru_ids, responses)
        self._write_csv(out_path, fieldnames, rows)
        self.logger.info(f"Wrote {len(fieldnames) - 1} columns x {len(rows)} HRUs to {out_path}")
        return out_path

    # -- internals ----------------------------------------------------------

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

    @staticmethod
    def _write_csv(out_path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
        import csv

        with out_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, restval="")
            writer.writeheader()
            writer.writerows(rows)


# ---------------------------------------------------------------------------
# Plugin entry point (acquisition-handler registration)
# ---------------------------------------------------------------------------


def register() -> None:
    """Register the CAS acquisition handler (``symfluence.plugins`` hook).

    Zero-arg callable invoked by SYMFLUENCE's plugin discovery at
    ``import symfluence``. Idempotent: safe to call repeatedly. Adds
    :class:`CASAttributeAcquirer` to ``R.acquisition_handlers`` under
    ``'CAS'`` (skipped when already present) for explicit use.

    The primary integration — :class:`CASAttributeProcessor` — needs no
    registration call: SYMFLUENCE discovers it directly through the
    ``symfluence.attribute_processors`` entry point. Accordingly, ``register``
    no longer appends the handler to the built-in attribute profiles.
    """
    if not HAVE_SYMFLUENCE:  # pragma: no cover - discovery never calls us then
        return

    from symfluence.core.registries import R

    if HANDLER_NAME not in R.acquisition_handlers:
        R.acquisition_handlers.add(HANDLER_NAME, CASAttributeAcquirer)


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
