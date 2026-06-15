# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Unit-selection tests: bbox→unit resolvers, unit specs, sync expansion."""

from __future__ import annotations

import pytest

from cas.core.exceptions import MirrorUnitError
from cas.mirror.datasets import parse_unit_spec
from cas.mirror.units import (
    RGI_REGION_BBOXES,
    RGI_REGION_NAMES,
    rgi_regions_for_bbox,
)


class TestParseUnitSpec:
    def test_plain_slug(self):
        assert parse_unit_spec("wokam") == ("wokam", None)

    def test_slug_with_unit(self):
        assert parse_unit_spec("rgi7:11") == ("rgi7", "11")

    def test_pinned_version_with_unit(self):
        assert parse_unit_spec("rgi7==7.0:06") == ("rgi7==7.0", "06")

    def test_geofabric_style_unit(self):
        assert parse_unit_spec("hydrobasins:na_lev06") == ("hydrobasins", "na_lev06")


class TestRGIRegionTable:
    def test_nineteen_regions(self):
        # RGI 7.0 has 19 first-order regions (live-verified against NSIDC);
        # the 20-region table in older tooling includes a phantom region 20.
        assert len(RGI_REGION_BBOXES) == 19
        assert set(RGI_REGION_BBOXES) == set(RGI_REGION_NAMES)
        assert "20" not in RGI_REGION_BBOXES

    def test_iceland_bbox_selects_region_06_only(self):
        assert rgi_regions_for_bbox((-20.0, 64.0, -18.0, 65.0)) == ["06"]

    def test_alps_bbox_selects_central_europe(self):
        assert rgi_regions_for_bbox((7.0, 45.5, 11.0, 47.0)) == ["11"]

    def test_straddling_bbox_selects_both(self):
        # Western Canada/Alaska border country touches regions 01 and 02.
        regions = rgi_regions_for_bbox((-135.0, 58.0, -128.0, 61.0))
        assert "01" in regions and "02" in regions

    def test_open_ocean_bbox_selects_nothing(self):
        # Mid-Atlantic at 28-30°N: outside every region extent. (Note a
        # tropical *land* bbox legitimately selects region 16 Low Latitudes —
        # its true extent is the whole tropical belt.)
        assert rgi_regions_for_bbox((-40.0, 28.0, -38.0, 30.0)) == []

    def test_iceland_does_not_drag_greenland(self):
        # Greenland periphery (05) is modelled as two lobes precisely so an
        # Iceland domain doesn't pull a 66 MB Greenland download.
        assert "05" not in rgi_regions_for_bbox((-25.0, 63.0, -13.0, 67.0))
        # ...but Scoresby Sund country (east Greenland, ~70.5°N) does select 05.
        assert "05" in rgi_regions_for_bbox((-29.0, 70.0, -22.0, 72.0))

    def test_far_aleutians_antimeridian_guard(self):
        # Attu Island (~173°E) hosts region-01 glaciers east of the
        # antimeridian; the guard must still select Alaska.
        assert "01" in rgi_regions_for_bbox((172.0, 52.0, 174.0, 53.5))

    def test_subantarctic_islands(self):
        # South Georgia (~37°W, 54.5°S) — region 19.
        assert rgi_regions_for_bbox((-38.0, -55.0, -35.5, -53.5)) == ["19"]


class TestResolveUnitIds:
    def test_none_expands_to_all_static_units(self):
        from cas.mirror.datasets import get_mirror_dataset
        from cas.mirror.store import resolve_unit_ids

        ds = get_mirror_dataset("rgi7")
        assert resolve_unit_ids(ds, None) == [f"{i:02d}" for i in range(1, 20)]

    def test_unknown_unit_is_actionable(self):
        from cas.mirror.datasets import get_mirror_dataset
        from cas.mirror.store import resolve_unit_ids

        ds = get_mirror_dataset("rgi7")
        with pytest.raises(MirrorUnitError) as exc:
            resolve_unit_ids(ds, ["20"])
        assert "Known units" in str(exc.value)

    def test_duplicates_are_collapsed(self):
        from cas.mirror.datasets import get_mirror_dataset
        from cas.mirror.store import resolve_unit_ids

        ds = get_mirror_dataset("rgi7")
        assert resolve_unit_ids(ds, ["06", "06", "11"]) == ["06", "11"]


class TestRGIRegistryEntry:
    def test_units_and_urls(self):
        from cas.mirror.datasets import get_mirror_dataset

        ds = get_mirror_dataset("rgi7")
        assert ds.is_unit_structured
        assert ds.delivery == "subset"
        assert ds.auth == "earthdata"
        units = ds.unit_ids()
        assert len(units) == 19
        (iceland,) = ds.sources_for_unit("06")
        assert iceland.url.endswith("RGI2000-v7.0-G-06_iceland.zip")
        assert iceland.url.startswith(
            "https://daacdata.apps.nsidc.org/pub/DATASETS/nsidc0770_rgi_v7/"
            "regional_files/RGI2000-v7.0-G/"
        )
        assert ds.license.license == "CC-BY-4.0"
        assert ds.license.license_verified is True
        assert "{access_date}" in ds.citation
        assert "10.5067" in ds.citation  # NSIDC DOI

    def test_global_datasets_are_not_unit_structured(self):
        from cas.mirror.datasets import get_mirror_dataset

        for slug in ("glhymps", "hydrolakes", "wokam"):
            ds = get_mirror_dataset(slug)
            assert not ds.is_unit_structured
            assert ds.unit_ids() == []
