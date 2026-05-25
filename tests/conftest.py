"""Shared test fixtures for CAS."""

from __future__ import annotations

import numpy as np
import pytest

from cas.core.models import (
    AggregationMethod,
    AttributeRequest,
    AttributeResult,
    BoundingBox,
    Dataset,
    DataType,
    Geometry,
    Protocol,
    QualityFlag,
    TemporalExtent,
    TemporalType,
    Variable,
)


@pytest.fixture
def sample_geometry() -> Geometry:
    """Small polygon in central Kansas."""
    return Geometry(
        type="Polygon",
        coordinates=[[
            [-96.6, 39.0],
            [-96.5, 39.0],
            [-96.5, 39.1],
            [-96.6, 39.1],
            [-96.6, 39.0],
        ]],
    )


@pytest.fixture(autouse=True)
def _clear_result_cache():
    """Clear the engine's result cache between tests."""
    from cas.extract.engine import get_result_cache
    get_result_cache().clear()


@pytest.fixture
def sample_point_geometry() -> Geometry:
    """Point in central Kansas."""
    return Geometry(type="Point", coordinates=[-96.55, 39.05])


@pytest.fixture
def sample_dataset() -> Dataset:
    return Dataset(
        id="test_provider:test_var",
        provider="test_provider",
        name="Test Variable",
        variables=[
            Variable(name="test_var", units="m", data_type=DataType.CONTINUOUS),
        ],
        resolution_m=250,
        crs="EPSG:4326",
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        temporal=TemporalExtent(temporal_type=TemporalType.STATIC),
        protocol=Protocol.WCS,
    )


@pytest.fixture
def sample_result() -> AttributeResult:
    return AttributeResult(
        dataset_id="isric_soilgrids:clay_0-5cm",
        variable="clay",
        value=245.0,
        units="g/kg",
        aggregation=AggregationMethod.MEAN,
        quality=QualityFlag.GOOD,
        coverage_fraction=0.95,
        pixel_count=100,
        provider="isric_soilgrids",
        elapsed_ms=500,
        provenance="WCS GetCoverage: clay_0-5cm_mean",
    )


@pytest.fixture
def sample_request(sample_geometry) -> AttributeRequest:
    return AttributeRequest(
        geometry=sample_geometry,
        dataset_ids=["isric_soilgrids:clay_0-5cm"],
        aggregation=AggregationMethod.MEAN,
    )


@pytest.fixture
def sample_raster_continuous() -> tuple[np.ndarray, np.ndarray, float]:
    """5x5 raster with known values, mask, and nodata."""
    raster = np.array([
        [100, 200, 300, 400, 500],
        [150, 250, 350, 450, 550],
        [200, 300, 400, 500, 600],
        [250, 350, 450, 550, 650],
        [300, 400, 500, 600, -9999],
    ], dtype=np.float32)
    mask = np.ones((5, 5), dtype=bool)
    mask[4, 4] = False  # one pixel outside polygon
    return raster, mask, -9999.0


@pytest.fixture
def sample_raster_categorical() -> tuple[np.ndarray, np.ndarray, float]:
    """5x5 categorical raster (land cover classes)."""
    raster = np.array([
        [10, 10, 40, 40, 40],
        [10, 10, 40, 40, 40],
        [10, 30, 30, 40, 40],
        [30, 30, 30, 30, 50],
        [30, 30, 30, 50, 50],
    ], dtype=np.float32)
    mask = np.ones((5, 5), dtype=bool)
    return raster, mask, 0.0
