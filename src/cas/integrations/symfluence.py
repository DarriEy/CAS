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

**Tertiary — attribute backend** (``symfluence.plugins`` → ``R.attribute_backends``)
    :func:`register` also adds :class:`CommunityAttributeBackend` (SYMFLUENCE
    backend-protocol contract 0.3.0) under ``'community'``. This is the proper
    Phase-C path, mirroring the CFS forcing backend and the CSFS observation
    backend: under ``DATA_ACCESS: community`` SYMFLUENCE's attribute pipeline
    selects it FIRST (parity-gated), and ``acquire()`` delivers a per-HRU
    ``HRU_STATS_V1`` CSV (to ``data/attributes/cas/``) plus the sidecar
    acquisition manifest, ingested by the model-ready ``AttributesNetCDFBuilder``
    as a ``cas`` group. Layering: all three seams wrap the SAME extraction
    helpers; when the backend serves ``CAS``, SYMFLUENCE excludes the ``cas``
    processor plugin from its plugin loop so CAS is extracted exactly once.

**Quaternary — mirror-acquisition delegation** (``symfluence.plugins`` → ``R.acquisition_handlers``)
    :func:`register` also wires :class:`CASMirrorAcquirer`: vector-attribute
    acquirers backed by the CAS curated-mirror tier (``cas.mirror_subset_sync``)
    that reproduce the GeoPackage output of SYMFLUENCE's native bulk-download
    handlers — WOKAM, HydroLAKES, GLHYMPS — now that the mirror-vs-native parity
    gate (CAS ``docs/mirror.md``) certified them equivalent. They register under
    additive ``CAS_*`` keys always, and *override* the native handler keys when
    ``CAS_SYMFLUENCE_MIRROR_ACQUISITION`` is set, so an unmodified SYMFLUENCE
    config routes those datasets through the audited mirror. RGI/glacier is only
    half-delegated — its handler also builds rasters — via the
    :func:`mirror_rgi_outlines` helper, which the glacier handler calls rather
    than being overridden. Unlike the stats seams this is *not* gated on
    ``CAS_DATASETS``; it is gated on the env flag (override) and is otherwise a
    purely additive set of explicit handler keys.

The first three (stats) seams are strict no-ops until ``CAS_DATASETS`` is set
in the SYMFLUENCE configuration.

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

import os
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

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

#: Contract version the AttributeBackend targets (SYMFLUENCE backend protocol
#: 0.3.0 added the attribute flavour). Frameworks predating 0.3.0 simply have
#: no ``R.attribute_backends`` registry and skip the backend tier.
TARGET_INTERFACE_VERSION = "0.3.0"

#: Acquisition-registry key for the secondary (handler) seam.
HANDLER_NAME = "CAS"

#: Provider id the AttributeBackend claims under ``R.attribute_backends``.
BACKEND_PROVIDER_ID = "CAS"

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
# Tertiary seam: SYMFLUENCE AttributeBackend protocol (contract 0.3.0)
# ---------------------------------------------------------------------------


def _backend_contract() -> Any:  # pragma: no cover - symfluence-only import
    from symfluence.data.backends import contract

    return contract


def _backend_errors() -> Any:  # pragma: no cover - symfluence-only import
    from symfluence.data.backends import errors

    return errors


def _integration_logger() -> Any:
    import logging

    return logging.getLogger("cas.integrations.symfluence")


class CommunityAttributeBackend:
    """CAS exposed through SYMFLUENCE's AttributeBackend protocol (0.3.0).

    The proper Phase-C tier: a thin wrapper over the same CAS extraction the
    :class:`CASAttributeProcessor` / :class:`CASAttributeAcquirer` use
    (``batch_extract`` over per-HRU geometries → per-HRU stats), reusing the
    pure ``load_catchment_wgs84`` / ``to_cas_geometries`` /
    ``_extract_responses`` / ``responses_to_rows`` helpers. ``acquire()``
    delivers an :attr:`SchemaId.HRU_STATS_V1` per-HRU CSV plus the shared
    sidecar manifest.

    Capabilities come from the configured ``CAS_DATASETS`` (the framework opt-in)
    so the backend only claims to serve what the run actually requests; the
    parity grade is the tolerance-based ``"value-within:resampling-tolerance"``
    (attribute zonal stats are not bitwise-reproducible — they depend on
    resampling/masking/source-grid alignment, unlike forcing/observations).

    Instantiated by SYMFLUENCE's selection layer with ``(config, logger)``.
    Coexists with the entry-point processor seam; SYMFLUENCE excludes the
    backend-served provider from the plugin loop so CAS is extracted once.
    """

    name = "community"
    interface_version = TARGET_INTERFACE_VERSION

    def __init__(self, config: Any = None, logger: Any = None) -> None:
        self.config = config
        self.logger = logger or _integration_logger()

    # -- helpers ------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        cfg = self.config
        if cfg is None:
            return default
        getter = getattr(cfg, "get", None)
        if callable(getter):
            value = getter(key, default)
            return default if value is None else value
        return default

    def _dataset_ids(self) -> list[str]:
        return parse_dataset_ids(self._cfg("CAS_DATASETS"))

    # -- protocol surface ---------------------------------------------------

    def capabilities(self) -> tuple[Any, ...]:  # pragma: no cover - symfluence-only
        """Attribute providers servable, as contract AttributeCapability.

        Inert until ``CAS_DATASETS`` is set: with no datasets configured the
        backend claims nothing, so selection declines and the in-tree/plugin
        path runs unchanged.
        """
        contract = _backend_contract()
        dataset_ids = self._dataset_ids()
        if not dataset_ids:
            return ()
        return (
            contract.AttributeCapability(
                provider_id=BACKEND_PROVIDER_ID,
                attribute_ids=frozenset(dataset_ids),
                output_kind="per_hru_stats",
                schema=contract.SchemaId.HRU_STATS_V1,
                auth=frozenset(),
                parity_grade="value-within:resampling-tolerance",
                notes="Per-HRU zonal statistics from the CAS dataset registry. "
                      "Zonal stats are not bitwise-reproducible (resampling/masking "
                      "dependent); parity is tolerance-based, not bit-identical.",
            ),
        )

    def acquire(self, request: Any) -> Any:  # pragma: no cover - exercised by integration tests
        """Serve an ``AttributeRequest`` via the existing CAS extraction internals."""
        contract = _backend_contract()
        errors = _backend_errors()

        if str(request.provider_id).lower() != BACKEND_PROVIDER_ID.lower():
            raise errors.DatasetUnsupported(
                f"The community attribute backend serves provider "
                f"'{BACKEND_PROVIDER_ID}', not {request.provider_id!r}",
                dataset_id=request.provider_id,
                backend=self.name,
            )

        dataset_ids = list(request.attribute_ids) or self._dataset_ids()
        if not dataset_ids:
            raise errors.DatasetUnsupported(
                "CAS attribute backend invoked but CAS_DATASETS is not configured",
                dataset_id=request.provider_id,
                backend=self.name,
            )

        aggregation = str(self._cfg("CAS_AGGREGATION", "mean"))
        api_config = self._cfg("CAS_API_CONFIG")

        # Geometry: prefer inline request geometries, else the catchment path.
        if request.geometries:
            from cas.core.models import Geometry

            geometries = [Geometry(**dict(g)) for g in request.geometries]
            hru_ids = list(request.hru_ids)
        else:
            if not request.catchment_path:
                raise errors.AcquisitionError(
                    "CAS attribute backend requires either inline geometries or a "
                    "catchment_path; got neither (run domain discretization first)"
                )
            try:
                gdf = load_catchment_wgs84(Path(request.catchment_path), self.logger)
            except FileNotFoundError as exc:
                raise errors.AcquisitionError(
                    f"CAS attribute backend could not read catchment geometry: {exc}"
                ) from exc
            hru_id_field = self._cfg("CATCHMENT_SHP_HRUID", "HRU_ID")
            hru_ids = hru_ids_from_gdf(
                gdf, hru_id_field, self.logger, source=Path(request.catchment_path).name
            )
            geometries = to_cas_geometries(gdf)

        self.logger.info(
            f"CAS attribute backend: {len(dataset_ids)} dataset(s) x {len(geometries)} HRU(s) "
            f"(aggregation={aggregation})"
        )

        from cas.core.exceptions import CASError

        try:
            responses = _extract_responses(geometries, dataset_ids, aggregation, api_config)
        except CASError as exc:
            raise errors.UpstreamOutage(
                f"CAS extraction failed for provider '{request.provider_id}': {exc}",
                upstream="cas",
            ) from exc
        except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
            raise errors.AcquisitionError(
                f"CAS extraction failed for provider '{request.provider_id}': {exc}"
            ) from exc

        summary = quality_summary(responses)
        log = self.logger.info if set(summary) <= {"good"} else self.logger.warning
        log(f"CAS extraction quality summary: {summary or 'no results returned'}")

        target_dir = Path(request.target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        domain_name = self._cfg("DOMAIN_NAME", "domain")
        out_path = target_dir / f"{domain_name}_cas_attributes.csv"
        fieldnames, rows = responses_to_rows(hru_ids, responses)
        CASAttributeAcquirer._write_csv(out_path, fieldnames, rows)
        self.logger.info(
            f"✓ Wrote {len(fieldnames) - 1} column(s) x {len(rows)} HRU(s) to {out_path}"
        )

        import cas

        result = contract.AcquisitionResult(
            paths=(out_path,),
            schema=contract.SchemaId.HRU_STATS_V1,
            dataset_id=request.provider_id,
            backend=self.name,
            provenance={
                "integration": f"{__name__}.CommunityAttributeBackend",
                "cas_version": getattr(cas, "__version__", "unknown"),
                "provider_id": str(request.provider_id),
                "datasets": ",".join(dataset_ids),
                "aggregation": aggregation,
                "quality_summary": str(summary),
            },
            variables_delivered=frozenset(sanitize_column(d) for d in dataset_ids),
        )
        contract.write_manifest(result, target_dir)
        return result


# ---------------------------------------------------------------------------
# Quaternary seam: mirror-acquisition delegation (vector attribute datasets)
# ---------------------------------------------------------------------------
#
# The native SYMFLUENCE handlers wokam.py / hydrolakes.py / glhymps.py download
# a global vector distribution and clip it to the domain — exactly what the CAS
# curated-mirror tier now does (version-pinned, checksummed, audited). The
# mirror-vs-native parity gate (CAS ``docs/mirror.md``) found CAS's bbox subset
# feature- and geometry-equivalent to each native download, which is the green
# light to retire the duplicated download/extract/clip code. SYMFLUENCE's
# attribute *profiles* reference these handlers by registry key (``WOKAM`` …),
# so the only way to route them through the mirror *without editing SYMFLUENCE*
# is to override those keys in :func:`register` — opt-in via the
# :data:`MIRROR_ACQUISITION_ENV` flag. The explicit ``CAS_*`` keys are always
# registered for scripted/explicit use regardless.


#: Environment flag that makes :func:`register` *override* the native
#: bulk-download acquisition handlers (WOKAM / HydroLAKES / GLHYMPS) with
#: CAS curated-mirror-backed equivalents, so an existing SYMFLUENCE config
#: routes its acquisition through CAS with no SYMFLUENCE edit. Off by default;
#: the additive ``CAS_*`` handler keys are registered either way.
MIRROR_ACQUISITION_ENV = "CAS_SYMFLUENCE_MIRROR_ACQUISITION"


class MirrorAcquisition(NamedTuple):
    """One deprecated native handler's delegation to the CAS curated mirror."""

    spec: str                              # CAS mirror dataset spec
    subdir: str                            # output dir under ``attributes/``
    filename: str                          # output GeoPackage name ({domain} expanded)
    keep_columns: tuple[str, ...] | None   # attribute projection (None keeps all)
    native_keys: tuple[str, ...]           # SYMFLUENCE registry keys this supersedes
    explicit_key: str                      # additive, always-registered key
    label: str                             # human label for logs


#: The three pure-vector datasets whose native handlers download a global
#: distribution and clip it — fully replaceable by the mirror. (RGI/glacier is
#: deliberately absent: its handler also builds rasters/intersection
#: shapefiles, so only its *acquisition* half is delegated, via
#: :func:`mirror_rgi_outlines`, and its key is never overridden here.)
MIRROR_ACQUISITIONS: tuple[MirrorAcquisition, ...] = (
    MirrorAcquisition(
        spec="wokam",
        subdir="geology/karst",
        filename="domain_{domain}_wokam_karst.gpkg",
        keep_columns=None,
        native_keys=("WOKAM", "KARST", "KARST_AQUIFER"),
        explicit_key="CAS_WOKAM",
        label="WOKAM karst",
    ),
    MirrorAcquisition(
        spec="hydrolakes",
        subdir="lakes",
        filename="domain_{domain}_hydrolakes.gpkg",
        keep_columns=(
            "Hylak_id", "Lake_name", "Lake_type", "Lake_area", "Shore_len",
            "Shore_dev", "Vol_total", "Depth_avg", "Dis_avg", "Res_time",
            "Elevation", "Wshd_area", "Pour_long", "Pour_lat",
        ),
        native_keys=("HYDROLAKES", "HYDROLAKES_V10"),
        explicit_key="CAS_HYDROLAKES",
        label="HydroLAKES",
    ),
    MirrorAcquisition(
        spec="glhymps",
        subdir="geology/glhymps",
        filename="domain_{domain}_glhymps.gpkg",
        keep_columns=("Porosity", "logK_Ice", "logK_Ferr", "Porosity_x", "logK_Ice_x"),
        native_keys=("GLHYMPS", "GLHYMPS_V2"),
        explicit_key="CAS_GLHYMPS",
        label="GLHYMPS",
    ),
)


def _symfluence_bbox_to_cas(bbox: Any) -> Any:
    """Convert a SYMFLUENCE bbox dict (``{lat_min, lon_min, lat_max, lon_max}``)
    to the CAS ``(min_lon, min_lat, max_lon, max_lat)`` tuple; pass tuples,
    lists and ``BoundingBox`` through unchanged (CAS normalizes them)."""
    if isinstance(bbox, dict) and "lon_min" in bbox:
        return (
            float(bbox["lon_min"]), float(bbox["lat_min"]),
            float(bbox["lon_max"]), float(bbox["lat_max"]),
        )
    return bbox


def mirror_subset_geodataframe(
    spec: str,
    bbox: Any,
    workdir: Path,
    logger: Any,
    label: str,
    target_crs: str | None = "EPSG:4326",
):
    """Acquire a bbox subset of a mirrored vector dataset via the CAS mirror.

    Calls ``cas.mirror_subset_sync`` — which applies the dataset's own default
    buffer (0.5° WOKAM, 0.1° GLHYMPS/HydroLAKES, 0.0° RGI) and writes a
    source-CRS GeoPackage — then reads it back and reprojects to *target_crs*
    (the native handlers' EPSG:4326 output contract). Returns
    ``(geodataframe, MirrorSubsetResult)``; an empty subset is a valid result.
    """
    import geopandas as gpd

    import cas

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Acquiring {label} via the CAS curated mirror ({spec})")
    result = cas.mirror_subset_sync(spec, _symfluence_bbox_to_cas(bbox), output_dir=workdir)
    gdf = gpd.read_file(result.path)
    logger.info(
        f"{label}: {result.feature_count} feature(s) from the CAS mirror "
        f"(license {result.license})"
    )
    if result.attribution:
        logger.info(f"  attribution: {result.attribution}")
    if target_crs is not None and len(gdf) > 0:
        gdf = gdf.set_crs(target_crs) if gdf.crs is None else gdf.to_crs(target_crs)
    return gdf, result


def mirror_rgi_outlines(bbox: Any, output_dir: Path, logger: Any = None) -> Path:
    """Deliver RGI 7.0 glacier outlines for *bbox* through the CAS mirror.

    The acquisition-only delegation for SYMFLUENCE's glacier handler: CAS
    replaces the NSIDC/GLIMS download with the curated ``rgi7`` mirror and
    returns a clipped EPSG:4326 GeoPackage of outlines. Raster and intersection
    creation stay in SYMFLUENCE — this seam is *called by* the glacier handler,
    it does not override it (unlike the pure-vector datasets above).
    """
    logger = logger or _integration_logger()
    _gdf, result = mirror_subset_geodataframe(
        "rgi7", bbox, Path(output_dir), logger, "RGI 7.0 glaciers"
    )
    return Path(result.path)


class CASMirrorAcquirer(_AcquirerBase):  # type: ignore[misc, valid-type]
    """SYMFLUENCE acquisition handler sourcing a vector attribute dataset from
    the CAS curated mirror instead of the native bulk download.

    Bound to a :class:`MirrorAcquisition` through the ``_mirror`` class
    attribute (:data:`MIRROR_ACQUIRERS` holds one subclass per dataset). Writes
    the same canonical GeoPackage — path, projected columns, EPSG:4326 — the
    native handler produced, which the parity gate proved equivalent, so
    downstream SYMFLUENCE steps are unchanged. A no-op-safe ``_skip_if_exists``
    guard and ``DOMAIN_NAME``/bbox plumbing mirror the native handlers.
    """

    #: Set on the per-dataset subclasses in :data:`MIRROR_ACQUIRERS`.
    _mirror: MirrorAcquisition | None = None

    def download(self, output_dir: Path) -> Path:
        if not HAVE_SYMFLUENCE:  # pragma: no cover - guard for standalone use
            raise RuntimeError(
                "CASMirrorAcquirer requires SYMFLUENCE. "
                "Install SYMFLUENCE in the same environment as CAS."
            )
        entry = self._mirror
        if entry is None:  # pragma: no cover - base class is never registered
            raise RuntimeError("CASMirrorAcquirer must be bound to a MirrorAcquisition")

        domain_name = self._get_config_value(
            lambda: self.config.domain.name, default="domain", dict_key="DOMAIN_NAME"
        )
        target_dir = Path(self._attribute_dir(entry.subdir))
        out_gpkg = target_dir / entry.filename.format(domain=domain_name)
        if self._skip_if_exists(out_gpkg):
            return target_dir

        self.logger.info(f"Starting {entry.label} acquisition (CAS curated mirror)")
        gdf, _ = mirror_subset_geodataframe(
            entry.spec, self.bbox, target_dir / "cache", self.logger, entry.label
        )

        if entry.keep_columns and len(gdf) > 0:
            geometry_column = gdf.geometry.name
            cols = [c for c in entry.keep_columns if c in gdf.columns]
            if geometry_column not in cols:
                cols.append(geometry_column)
            gdf = gdf[cols].copy()

        gdf.to_file(out_gpkg, driver="GPKG")
        self.logger.info(
            f"{entry.label} mirror subset: {len(gdf)} feature(s) -> {out_gpkg}"
        )
        return target_dir


def _make_mirror_acquirer(entry: MirrorAcquisition) -> type:
    """Build the per-dataset :class:`CASMirrorAcquirer` subclass for *entry*."""
    return type(
        f"CASMirrorAcquirer_{sanitize_column(entry.spec)}",
        (CASMirrorAcquirer,),
        {
            "_mirror": entry,
            "__module__": __name__,
            "__doc__": f"CAS curated-mirror acquirer for {entry.label} "
                       f"(mirror dataset {entry.spec!r}).",
        },
    )


#: Stable per-dataset acquirer classes (built once so registry identity is
#: stable across repeated :func:`register` calls). Created even without
#: SYMFLUENCE — the base degrades to ``object`` — so the module always imports.
MIRROR_ACQUIRERS: dict[str, type] = {
    entry.spec: _make_mirror_acquirer(entry) for entry in MIRROR_ACQUISITIONS
}


def _mirror_override_enabled() -> bool:
    """Whether :data:`MIRROR_ACQUISITION_ENV` opts into overriding native keys."""
    raw = os.environ.get(MIRROR_ACQUISITION_ENV)
    return raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}


def _register_mirror_acquisition(registry: Any) -> bool:
    """Register the mirror-backed acquirers on ``R.acquisition_handlers``.

    Always adds the additive ``CAS_*`` keys; overrides the native handler keys
    only when :func:`_mirror_override_enabled`. Returns whether the override was
    applied (for the caller's log line).
    """
    override = _mirror_override_enabled()
    for entry in MIRROR_ACQUISITIONS:
        acquirer = MIRROR_ACQUIRERS[entry.spec]
        if entry.explicit_key not in registry:
            registry.add(entry.explicit_key, acquirer)
        if override:
            for key in entry.native_keys:
                registry.add(key, acquirer)  # last-writer-wins over the native decorator
    return override


# ---------------------------------------------------------------------------
# Plugin entry point (acquisition-handler registration)
# ---------------------------------------------------------------------------


def register() -> None:
    """Register the CAS SYMFLUENCE integrations (``symfluence.plugins`` hook).

    Zero-arg callable invoked by SYMFLUENCE's plugin discovery at
    ``import symfluence``. Idempotent: safe to call repeatedly. Wires three
    coexisting seams (see the module docstring for the layering):

    * :class:`CASAttributeAcquirer` → ``R.acquisition_handlers['CAS']``
      (secondary handler seam, explicit use only).
    * :class:`CASMirrorAcquirer` → ``R.acquisition_handlers['CAS_WOKAM'|
      'CAS_HYDROLAKES'|'CAS_GLHYMPS']`` (quaternary mirror-acquisition seam),
      additionally *overriding* the native ``WOKAM``/``HYDROLAKES``/``GLHYMPS``
      keys when ``CAS_SYMFLUENCE_MIRROR_ACQUISITION`` is set.
    * :class:`CommunityAttributeBackend` → ``R.attribute_backends['community']``
      (tertiary, contract-0.3.0 protocol tier; the proper Phase-C path,
      consulted FIRST under ``DATA_ACCESS: community``). Skipped on frameworks
      predating 0.3.0 (no ``attribute_backends`` registry) — the processor
      plugin seam then still serves community mode.

    The primary integration — :class:`CASAttributeProcessor` — needs no
    registration call: SYMFLUENCE discovers it directly through the
    ``symfluence.attribute_processors`` entry point. When the backend serves a
    provider, SYMFLUENCE excludes the matching plugin to avoid double extraction.
    """
    if not HAVE_SYMFLUENCE:  # pragma: no cover - discovery never calls us then
        return

    from symfluence.core.registries import R

    if HANDLER_NAME not in R.acquisition_handlers:
        R.acquisition_handlers.add(HANDLER_NAME, CASAttributeAcquirer)

    # Quaternary seam: mirror-acquisition delegation for the deprecated native
    # bulk-download vector handlers. The additive CAS_* keys are always added;
    # the native keys are overridden only under CAS_SYMFLUENCE_MIRROR_ACQUISITION.
    # Last-writer-wins over the native decorators, which already ran when CAS
    # imported ``symfluence.data.acquisition.base`` above (its package __init__
    # imports the handler modules), so the override lands after them and sticks.
    if _register_mirror_acquisition(R.acquisition_handlers):
        _integration_logger().info(
            "CAS curated mirror is overriding the native WOKAM/HydroLAKES/GLHYMPS "
            "acquisition handlers (%s set)", MIRROR_ACQUISITION_ENV,
        )

    # Protocol tier (contract 0.3.0). Registered as a CLASS: SYMFLUENCE's
    # selection layer instantiates it with (config, logger). Older frameworks
    # without the registry simply skip this tier.
    backends = getattr(R, "attribute_backends", None)
    if backends is not None and "community" not in backends:
        backends.add("community", CommunityAttributeBackend)


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
