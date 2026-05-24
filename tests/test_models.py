"""Tests for core data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cas.core.models import (
    AggregationMethod,
    AttributeRequest,
    AttributeResult,
    BoundingBox,
    Geometry,
    Protocol,
    QualityFlag,
    TemporalType,
)


class TestGeometry:
    def test_valid_polygon(self, sample_geometry):
        assert sample_geometry.type == "Polygon"
        assert len(sample_geometry.coordinates[0]) == 5

    def test_rejects_point(self):
        with pytest.raises(ValidationError):
            Geometry(type="Point", coordinates=[0, 0])

    def test_multipolygon(self):
        geom = Geometry(
            type="MultiPolygon",
            coordinates=[
                [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]],
            ],
        )
        assert geom.type == "MultiPolygon"


class TestBoundingBox:
    def test_valid_bbox(self):
        bbox = BoundingBox(min_lon=-96.6, min_lat=39.0, max_lon=-96.5, max_lat=39.1)
        assert bbox.min_lon == -96.6

    def test_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            BoundingBox(min_lon=-200, min_lat=0, max_lon=0, max_lat=0)


class TestDataset:
    def test_create_dataset(self, sample_dataset):
        assert sample_dataset.id == "test_provider:test_var"
        assert sample_dataset.protocol == Protocol.WCS
        assert sample_dataset.temporal.temporal_type == TemporalType.STATIC

    def test_dataset_with_variables(self, sample_dataset):
        assert len(sample_dataset.variables) == 1
        assert sample_dataset.variables[0].name == "test_var"


class TestAttributeRequest:
    def test_valid_request(self, sample_request):
        assert len(sample_request.dataset_ids) == 1
        assert sample_request.aggregation == AggregationMethod.MEAN

    def test_rejects_empty_datasets(self, sample_geometry):
        with pytest.raises(ValidationError):
            AttributeRequest(
                geometry=sample_geometry,
                dataset_ids=[],
            )


class TestAttributeResult:
    def test_scalar_result(self, sample_result):
        assert sample_result.value == 245.0
        assert sample_result.quality == QualityFlag.GOOD

    def test_categorical_result(self):
        result = AttributeResult(
            dataset_id="esa_worldcover:land_cover",
            variable="land_cover",
            value={"Tree cover": 0.45, "Cropland": 0.30, "Grassland": 0.25},
            units="class_fraction",
            aggregation=AggregationMethod.DISTRIBUTION,
            quality=QualityFlag.GOOD,
            coverage_fraction=1.0,
            pixel_count=500,
            provider="esa_worldcover",
        )
        assert isinstance(result.value, dict)
        assert result.value["Tree cover"] == 0.45

    def test_missing_result(self):
        result = AttributeResult(
            dataset_id="test:missing",
            variable="test",
            value=None,
            units="m",
            aggregation=AggregationMethod.MEAN,
            quality=QualityFlag.MISSING,
            coverage_fraction=0.0,
            pixel_count=0,
            provider="test",
        )
        assert result.value is None


class TestEnums:
    def test_temporal_types(self):
        assert TemporalType.STATIC == "static"
        assert TemporalType.HOURLY == "hourly"

    def test_aggregation_methods(self):
        assert AggregationMethod.DISTRIBUTION == "distribution"
        assert AggregationMethod.MAJORITY == "majority"

    def test_quality_flags(self):
        assert QualityFlag.PARTIAL == "partial"
