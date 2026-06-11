# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the SYMFLUENCE integration (``cas.integrations.symfluence``).

Three layers:

1. Defensive-import tests — the module (and ``register()``) must work with
   SYMFLUENCE entirely absent (``sys.modules`` hiding pattern).
2. Pure-helper unit tests — request building and CSV shaping need neither
   SYMFLUENCE nor network.
3. Integration tests (``pytest.importorskip("symfluence")``) — entry-point
   auto-registration, profile idempotency, and ``download()`` behaviour with
   ``cas.batch_extract_sync`` monkeypatched. Run these with the SYMFLUENCE
   venv's interpreter; they skip cleanly in CAS's own (symfluence-free) suite.
"""

from __future__ import annotations

import builtins
import csv
import importlib
import logging
import sys
from pathlib import Path

import pytest

from cas.core.models import (
    AggregationMethod,
    AttributeResponse,
    AttributeResult,
    BatchAttributeRequest,
    BatchAttributeResponse,
    Geometry,
    QualityFlag,
)

INTEGRATION_MODULE = "cas.integrations.symfluence"

DATASET_DEM = "copernicus_dem:elevation"
DATASET_CLAY = "isric_soilgrids:clay_0-5cm"


def make_result(dataset_id: str = DATASET_DEM, value=123.4, **overrides) -> AttributeResult:
    provider, _, variable = dataset_id.partition(":")
    kwargs = {
        "dataset_id": dataset_id,
        "variable": variable,
        "value": value,
        "units": "m",
        "aggregation": AggregationMethod.MEAN,
        "quality": QualityFlag.GOOD,
        "coverage_fraction": 1.0,
        "pixel_count": 10,
        "provider": provider,
    }
    kwargs.update(overrides)
    return AttributeResult(**kwargs)


def make_response(results: list[AttributeResult], geometry_hash: str = "g0") -> AttributeResponse:
    return AttributeResponse(
        request_id="req", geometry_hash=geometry_hash, results=results, elapsed_ms=1
    )


# ---------------------------------------------------------------------------
# 1. Defensive import (no SYMFLUENCE)
# ---------------------------------------------------------------------------


def test_module_imports_without_symfluence(monkeypatch):
    """The module imports and register() no-ops when SYMFLUENCE is missing."""
    # Hide any already-imported symfluence modules (monkeypatch restores them)
    # and force a fresh import of the integration module.
    for name in list(sys.modules):
        if name == "symfluence" or name.startswith("symfluence."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.delitem(sys.modules, INTEGRATION_MODULE, raising=False)

    real_import = builtins.__import__

    def hide_symfluence(name, *args, **kwargs):
        if name == "symfluence" or name.startswith("symfluence."):
            raise ImportError(f"symfluence hidden for test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", hide_symfluence)
    try:
        module = importlib.import_module(INTEGRATION_MODULE)
        assert module.HAVE_SYMFLUENCE is False
        # Base degrades to object so the class definition still exists.
        assert module.CASAttributeAcquirer.__mro__[-1] is object
        module.register()  # must be a harmless no-op, not an ImportError
    finally:
        # Drop the symfluence-free module instance so later tests (and other
        # files) re-import it against the real environment.
        sys.modules.pop(INTEGRATION_MODULE, None)


def test_import_cas_does_not_require_symfluence():
    """`import cas` never pulls in symfluence (lazy/defensive integration)."""
    import cas  # noqa: F401

    integrations = importlib.import_module("cas.integrations")
    assert "symfluence" in integrations.__all__


# ---------------------------------------------------------------------------
# 2. Pure helpers (no SYMFLUENCE required)
# ---------------------------------------------------------------------------


@pytest.fixture
def integration():
    return importlib.import_module(INTEGRATION_MODULE)


class TestParseDatasetIds:
    def test_comma_separated_string(self, integration):
        raw = f" {DATASET_DEM}, {DATASET_CLAY} ,"
        assert integration.parse_dataset_ids(raw) == [DATASET_DEM, DATASET_CLAY]

    def test_list_input(self, integration):
        assert integration.parse_dataset_ids([DATASET_DEM]) == [DATASET_DEM]

    @pytest.mark.parametrize("raw", [None, "", "  ", ",", []])
    def test_empty_inputs(self, integration, raw):
        assert integration.parse_dataset_ids(raw) == []


class TestSanitizeColumn:
    def test_dataset_id(self, integration):
        assert integration.sanitize_column(DATASET_CLAY) == "isric_soilgrids_clay_0_5cm"
        assert integration.sanitize_column(DATASET_DEM) == "copernicus_dem_elevation"


class TestBuildBatchRequests:
    def test_single_chunk_covers_all_datasets(self, integration):
        geoms = [{"type": "Point", "coordinates": [-96.5 + i * 0.01, 39.0]} for i in range(3)]
        requests = integration.build_batch_requests(geoms, [DATASET_DEM, DATASET_CLAY])
        assert len(requests) == 1
        request = requests[0]
        assert isinstance(request, BatchAttributeRequest)
        assert request.dataset_ids == [DATASET_DEM, DATASET_CLAY]
        assert len(request.geometries) == 3
        assert request.aggregation == AggregationMethod.MEAN

    def test_chunks_at_1000_geometries(self, integration):
        geoms = [Geometry(type="Point", coordinates=[0.001 * i, 0.0]) for i in range(2003)]
        requests = integration.build_batch_requests(geoms, [DATASET_DEM])
        assert [len(r.geometries) for r in requests] == [1000, 1000, 3]
        # Geometry order is preserved across chunks.
        assert requests[2].geometries[-1].coordinates[0] == pytest.approx(0.001 * 2002)

    def test_aggregation_passthrough(self, integration):
        geoms = [{"type": "Point", "coordinates": [0.0, 0.0]}]
        (request,) = integration.build_batch_requests(geoms, [DATASET_DEM], aggregation="majority")
        assert request.aggregation == AggregationMethod.MAJORITY

    def test_invalid_chunk_size_rejected(self, integration):
        geoms = [{"type": "Point", "coordinates": [0.0, 0.0]}]
        with pytest.raises(ValueError, match="chunk_size"):
            integration.build_batch_requests(geoms, [DATASET_DEM], chunk_size=1001)


class TestResponsesToRows:
    def test_scalar_values_one_row_per_hru(self, integration):
        responses = [
            make_response([make_result(DATASET_DEM, 100.0), make_result(DATASET_CLAY, 21.5, units="%")]),
            make_response([make_result(DATASET_DEM, 200.0), make_result(DATASET_CLAY, 30.5, units="%")]),
        ]
        fieldnames, rows = integration.responses_to_rows([2, 1], responses)
        assert fieldnames[0] == "hru_id"
        assert "copernicus_dem_elevation" in fieldnames
        assert "isric_soilgrids_clay_0_5cm" in fieldnames
        # Rows sorted by str(hru_id): HRU 1 (second response) first.
        assert [row["hru_id"] for row in rows] == [1, 2]
        assert rows[0]["copernicus_dem_elevation"] == 200.0
        assert rows[1]["isric_soilgrids_clay_0_5cm"] == 21.5

    def test_metadata_columns_carried(self, integration):
        responses = [make_response([make_result(DATASET_DEM, 1.0, coverage_fraction=0.5)])]
        fieldnames, rows = integration.responses_to_rows([1], responses)
        assert rows[0]["copernicus_dem_elevation_units"] == "m"
        assert rows[0]["copernicus_dem_elevation_quality"] == "good"
        assert rows[0]["copernicus_dem_elevation_coverage_fraction"] == 0.5

    def test_metadata_columns_optional(self, integration):
        responses = [make_response([make_result(DATASET_DEM, 1.0)])]
        fieldnames, _ = integration.responses_to_rows([1], responses, include_metadata=False)
        assert fieldnames == ["hru_id", "copernicus_dem_elevation"]

    def test_class_fraction_dict_expands_to_columns(self, integration):
        landcover = "esa_worldcover:landcover"
        responses = [
            make_response(
                [
                    make_result(
                        landcover,
                        {"Tree cover": 0.7, "Grassland": 0.3},
                        units="1",
                        aggregation=AggregationMethod.DISTRIBUTION,
                    )
                ]
            )
        ]
        fieldnames, rows = integration.responses_to_rows([1], responses)
        assert rows[0]["esa_worldcover_landcover_Tree_cover"] == 0.7
        assert rows[0]["esa_worldcover_landcover_Grassland"] == 0.3
        assert "esa_worldcover_landcover" not in fieldnames  # no scalar column

    def test_missing_value_leaves_column_absent(self, integration):
        responses = [make_response([make_result(DATASET_DEM, None, quality=QualityFlag.MISSING)])]
        _, rows = integration.responses_to_rows([1], responses)
        assert "copernicus_dem_elevation" not in rows[0]
        assert rows[0]["copernicus_dem_elevation_quality"] == "missing"

    def test_length_mismatch_raises(self, integration):
        with pytest.raises(ValueError, match="align 1:1"):
            integration.responses_to_rows([1, 2], [make_response([])])


class TestQualitySummary:
    def test_counts_flags(self, integration):
        responses = [
            make_response([make_result(), make_result(quality=QualityFlag.PARTIAL)]),
            make_response([make_result()]),
        ]
        assert integration.quality_summary(responses) == {"good": 2, "partial": 1}


# ---------------------------------------------------------------------------
# 3. SYMFLUENCE integration (skipped when symfluence is not installed)
# ---------------------------------------------------------------------------


def _symfluence():
    return pytest.importorskip("symfluence")


def _make_domain(tmp_path: Path):
    """Write a 2-polygon HRU GeoJSON and return (config_dict, output_dir)."""
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon

    catchment_dir = tmp_path / "shapefiles" / "catchment"
    catchment_dir.mkdir(parents=True)
    gdf = gpd.GeoDataFrame(
        {
            "HRU_ID": [2, 1],
            "geometry": [
                Polygon([(8.0, 46.0), (8.1, 46.0), (8.1, 46.1), (8.0, 46.1)]),
                Polygon([(8.1, 46.0), (8.2, 46.0), (8.2, 46.1), (8.1, 46.1)]),
            ],
        },
        crs="EPSG:4326",
    )
    catchment_file = "test_domain_HRUs_test.geojson"
    gdf.to_file(catchment_dir / catchment_file, driver="GeoJSON")

    output_dir = tmp_path / "data" / "attributes" / "cas"
    config = {
        "DOMAIN_NAME": "test_domain",
        "SYMFLUENCE_DATA_DIR": str(tmp_path),
        "BOUNDING_BOX_COORDS": "47.0/8.0/46.0/9.0",
        "EXPERIMENT_TIME_START": "2020-01-01",
        "EXPERIMENT_TIME_END": "2020-01-31",
        "CATCHMENT_PATH": str(catchment_dir),
        "CATCHMENT_SHP_NAME": catchment_file,
        "CATCHMENT_SHP_HRUID": "HRU_ID",
    }
    return config, output_dir


def test_entry_point_auto_registration():
    """A plain `import symfluence` registers 'CAS' purely via the entry point."""
    _symfluence()
    from symfluence.data.acquisition.registry import AcquisitionRegistry

    assert "CAS" in [name.upper() for name in AcquisitionRegistry.list_datasets()]

    from symfluence.core.registries import R

    module = importlib.import_module(INTEGRATION_MODULE)
    assert R.acquisition_handlers["CAS"] is module.CASAttributeAcquirer


def test_profiles_contain_cas_exactly_once_and_register_is_idempotent():
    _symfluence()
    module = importlib.import_module(INTEGRATION_MODULE)
    module.register()
    module.register()  # double registration must not duplicate anything

    from symfluence.data.acquisition.attribute_profiles import PROFILES

    assert PROFILES, "expected SYMFLUENCE to define attribute profiles"
    for profile_name, datasets in PROFILES.items():
        cas_entries = [ds for ds in datasets if ds.handler_name == "CAS"]
        assert len(cas_entries) == 1, f"profile {profile_name!r} has {len(cas_entries)} CAS entries"
        entry = cas_entries[0]
        assert entry.output_subdir == "cas"
        assert entry.config_override_key == "DOWNLOAD_CAS"
        assert entry.fatal is False


def test_download_noops_without_cas_datasets(tmp_path):
    _symfluence()
    module = importlib.import_module(INTEGRATION_MODULE)
    config, output_dir = _make_domain(tmp_path)  # no CAS_DATASETS key
    output_dir.mkdir(parents=True)

    handler = module.CASAttributeAcquirer(config, logging.getLogger("test_cas_noop"))
    result = handler.download(output_dir)

    assert result == output_dir
    assert list(output_dir.iterdir()) == []  # nothing written


def test_download_writes_per_hru_csv(tmp_path, monkeypatch):
    _symfluence()
    module = importlib.import_module(INTEGRATION_MODULE)
    import cas

    config, output_dir = _make_domain(tmp_path)
    config["CAS_DATASETS"] = f"{DATASET_DEM}, {DATASET_CLAY}"
    config["CAS_AGGREGATION"] = "mean"

    captured_requests: list[BatchAttributeRequest] = []

    def fake_batch_extract_sync(request: BatchAttributeRequest) -> BatchAttributeResponse:
        captured_requests.append(request)
        responses = []
        for i, _geom in enumerate(request.geometries):
            results = [
                make_result(ds, value=100.0 * (i + 1) + j)
                for j, ds in enumerate(request.dataset_ids)
            ]
            responses.append(make_response(results, geometry_hash=f"g{i}"))
        return BatchAttributeResponse(
            request_id="batch",
            responses=responses,
            total_geometries=len(request.geometries),
            total_results=sum(len(r.results) for r in responses),
            elapsed_ms=1,
        )

    monkeypatch.setattr(cas, "batch_extract_sync", fake_batch_extract_sync)

    handler = module.CASAttributeAcquirer(config, logging.getLogger("test_cas_csv"))
    out_path = handler.download(output_dir)

    # One batch request covering both datasets and both HRUs.
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.dataset_ids == [DATASET_DEM, DATASET_CLAY]
    assert len(request.geometries) == 2
    assert all(g.type == "Polygon" for g in request.geometries)

    assert out_path == output_dir / "test_domain_cas_attributes.csv"
    assert out_path.exists()

    with out_path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    # Shapefile row order is HRU_ID [2, 1]; CSV rows are sorted by HRU id.
    assert [row["hru_id"] for row in rows] == ["1", "2"]
    # HRU 1 is geometry index 1 (values 200/201); HRU 2 is index 0 (100/101).
    assert float(rows[0]["copernicus_dem_elevation"]) == 200.0
    assert float(rows[0]["isric_soilgrids_clay_0_5cm"]) == 201.0
    assert float(rows[1]["copernicus_dem_elevation"]) == 100.0
    assert float(rows[1]["isric_soilgrids_clay_0_5cm"]) == 101.0
    # Metadata extras are carried alongside each value column.
    assert rows[0]["copernicus_dem_elevation_quality"] == "good"
    assert float(rows[0]["copernicus_dem_elevation_coverage_fraction"]) == 1.0

    # Idempotency: a second call skips the CAS extraction entirely.
    captured_requests.clear()
    assert handler.download(output_dir) == out_path
    assert captured_requests == []
