"""Tests for zonal statistics computation."""

from __future__ import annotations

import numpy as np

from cas.core.models import AggregationMethod, DataType
from cas.extract.zonal import compute_zonal_stats


class TestContinuousStats:
    def test_mean(self, sample_raster_continuous):
        raster, mask, nodata = sample_raster_continuous
        value, coverage, count = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.MEAN, DataType.CONTINUOUS
        )
        assert value is not None
        assert coverage > 0.9
        assert count == 24  # 25 pixels - 1 nodata

    def test_median(self, sample_raster_continuous):
        raster, mask, nodata = sample_raster_continuous
        value, _, _ = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.MEDIAN, DataType.CONTINUOUS
        )
        assert value is not None

    def test_min_max(self, sample_raster_continuous):
        raster, mask, nodata = sample_raster_continuous
        vmin, _, _ = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.MIN, DataType.CONTINUOUS
        )
        vmax, _, _ = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.MAX, DataType.CONTINUOUS
        )
        assert vmin == 100.0
        assert vmax == 650.0

    def test_std(self, sample_raster_continuous):
        raster, mask, nodata = sample_raster_continuous
        value, _, _ = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.STD, DataType.CONTINUOUS
        )
        assert value > 0

    def test_sum(self, sample_raster_continuous):
        raster, mask, nodata = sample_raster_continuous
        value, _, count = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.SUM, DataType.CONTINUOUS
        )
        assert value is not None
        assert value > 0

    def test_nodata_excluded(self):
        raster = np.array([[100.0, -9999.0], [200.0, 300.0]])
        mask = np.ones((2, 2), dtype=bool)
        value, coverage, count = compute_zonal_stats(
            raster, mask, -9999.0, AggregationMethod.MEAN, DataType.CONTINUOUS
        )
        assert count == 3
        assert coverage == 0.75
        assert abs(value - 200.0) < 0.01

    def test_all_nodata(self):
        raster = np.array([[-9999.0, -9999.0]])
        mask = np.ones((1, 2), dtype=bool)
        value, coverage, count = compute_zonal_stats(
            raster, mask, -9999.0, AggregationMethod.MEAN, DataType.CONTINUOUS
        )
        assert value is None
        assert coverage == 0.0
        assert count == 0

    def test_empty_mask(self):
        raster = np.array([[100.0, 200.0]])
        mask = np.zeros((1, 2), dtype=bool)
        value, coverage, count = compute_zonal_stats(
            raster, mask, None, AggregationMethod.MEAN, DataType.CONTINUOUS
        )
        assert value is None
        assert count == 0


class TestCategoricalStats:
    def test_majority(self, sample_raster_categorical):
        raster, mask, nodata = sample_raster_categorical
        value, coverage, count = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.MAJORITY, DataType.CATEGORICAL
        )
        assert value in (10.0, 30.0, 40.0)
        assert count == 25

    def test_distribution(self, sample_raster_categorical):
        raster, mask, nodata = sample_raster_categorical
        value, coverage, count = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.DISTRIBUTION, DataType.CATEGORICAL
        )
        assert isinstance(value, dict)
        assert sum(value.values()) == pytest.approx(1.0, abs=0.01)
        assert "10" in value
        assert "40" in value

    def test_unique(self, sample_raster_categorical):
        raster, mask, nodata = sample_raster_categorical
        value, _, _ = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.UNIQUE, DataType.CATEGORICAL
        )
        assert value == 4.0  # classes: 10, 30, 40, 50

    def test_minority(self, sample_raster_categorical):
        raster, mask, nodata = sample_raster_categorical
        value, _, _ = compute_zonal_stats(
            raster, mask, nodata, AggregationMethod.MINORITY, DataType.CATEGORICAL
        )
        assert value == 50.0  # least common class


# Need pytest import for approx
import pytest  # noqa: E402
