"""Tests for QC validation module."""

from __future__ import annotations

from cas.core.models import AggregationMethod, AttributeResult, QualityFlag
from cas.core.qc import check_cross_provider_consistency, validate_result


def _make_result(
    variable: str = "clay",
    value: float | None = 250.0,
    coverage: float = 0.95,
    quality: QualityFlag = QualityFlag.GOOD,
    dataset_id: str = "test:clay",
    units: str = "g/kg",
    provider: str = "test",
) -> AttributeResult:
    return AttributeResult(
        dataset_id=dataset_id,
        variable=variable,
        value=value,
        units=units,
        aggregation=AggregationMethod.MEAN,
        quality=quality,
        coverage_fraction=coverage,
        pixel_count=100,
        provider=provider,
    )


class TestValidateResult:
    def test_good_result_passes(self):
        result = _make_result()
        warnings = validate_result(result)
        assert warnings == []
        assert result.quality == QualityFlag.GOOD

    def test_low_coverage_marks_partial(self):
        result = _make_result(coverage=0.5)
        warnings = validate_result(result)
        assert result.quality == QualityFlag.PARTIAL
        assert len(warnings) == 1

    def test_very_low_coverage_marks_missing(self):
        result = _make_result(coverage=0.1)
        validate_result(result)
        assert result.quality == QualityFlag.MISSING

    def test_out_of_range_marks_suspect(self):
        result = _make_result(value=2000.0)
        warnings = validate_result(result)
        assert result.quality == QualityFlag.SUSPECT
        assert "outside expected range" in warnings[0]

    def test_elevation_valid(self):
        result = _make_result(variable="elevation", value=400.0, units="m")
        warnings = validate_result(result)
        assert warnings == []

    def test_elevation_out_of_range(self):
        result = _make_result(variable="elevation", value=10000.0, units="m")
        validate_result(result)
        assert result.quality == QualityFlag.SUSPECT

    def test_ndvi_valid(self):
        result = _make_result(variable="ndvi", value=0.65, units="dimensionless")
        warnings = validate_result(result)
        assert warnings == []

    def test_ndvi_out_of_range(self):
        result = _make_result(variable="ndvi", value=2.0, units="dimensionless")
        validate_result(result)
        assert result.quality == QualityFlag.SUSPECT

    def test_metadata_driven_range_overrides_global(self):
        # Global NDVI range is [-1, 1]. Let's provide a result with valid_range=[0, 10]
        # and value=5. It should PASS.
        result = _make_result(variable="ndvi", value=5.0, units="dimensionless")
        result.valid_range = (0.0, 10.0)
        warnings = validate_result(result)
        assert warnings == []
        assert result.quality == QualityFlag.GOOD

    def test_metadata_driven_range_fails_correctly(self):
        result = _make_result(variable="ndvi", value=15.0, units="dimensionless")
        result.valid_range = (0.0, 10.0)
        warnings = validate_result(result)
        assert result.quality == QualityFlag.SUSPECT
        assert "(metadata-driven)" in warnings[0]


class TestCrossProviderConsistency:
    def test_consistent_values(self):
        results = [
            _make_result(variable="elevation", value=400.0, dataset_id="a:elev", provider="a"),
            _make_result(variable="elevation", value=410.0, dataset_id="b:elev", provider="b"),
        ]
        warnings = check_cross_provider_consistency(results, "elevation")
        assert warnings == []

    def test_divergent_values(self):
        results = [
            _make_result(variable="elevation", value=400.0, dataset_id="a:elev", provider="a"),
            _make_result(variable="elevation", value=800.0, dataset_id="b:elev", provider="b"),
        ]
        warnings = check_cross_provider_consistency(results, "elevation")
        assert len(warnings) > 0

    def test_single_provider_no_warning(self):
        results = [
            _make_result(variable="elevation", value=400.0, dataset_id="a:elev", provider="a"),
        ]
        warnings = check_cross_provider_consistency(results, "elevation")
        assert warnings == []
