# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Geofabric path-delivery tests (slice 2b).

Hermetic: tiny synthetic archives per format (zip→gpkg, raw parquet/gpkg,
tar.gz→gpkg member). Covers the path-delivery contract (no bbox clip), the
HydroBASINS Exhibit B notice + ack-refusal-via-lazy-fetch, the MERIT
license-fork flag, the TDX sync-all refusal, NWS provisional disclaimer, and
the registry URL/license shape of all four geofabrics.
"""

from __future__ import annotations

import httpx
import pytest
import respx

pytest.importorskip("geopandas")
pytest.importorskip("pyarrow")

import cas
from cas.core.exceptions import MirrorLicenseError, MirrorUnitError
from cas.mirror import (
    dataset_dir,
    ensure_units_materialized,
    get_mirror_dataset,
    is_unit_materialized,
    load_manifest,
    mirror_fetch_sync,
    verify,
)
from cas.mirror.store import resolve_unit_ids

from .mirror_utils import (
    build_fake_gpkg_bytes,
    build_fake_gpkg_tar,
    build_fake_parquet_bytes,
    build_fake_zip,
    make_fake_unit_dataset,
    registered,
)

# ── Registry shape ──────────────────────────────────────────────────


class TestGeofabricRegistry:
    def test_all_four_registered_as_path_delivery(self):
        for slug in ("hydrobasins", "merit_basins", "tdx_hydro", "nws_hydrofabric"):
            ds = get_mirror_dataset(slug)
            assert ds.delivery == "path", slug

    def test_hydrobasins_dynamic_units_and_notice(self):
        ds = get_mirror_dataset("hydrobasins")
        assert ds.dynamic_units
        assert ds.license.requires_acknowledgment
        assert ds.notice_file == "hydrosheds_v1_exhibit_b.txt"
        # The factory builds a real HydroSHEDS URL for a region×level unit.
        from cas.mirror.datasets import sources_for_unit

        (src,) = sources_for_unit(ds, "na_lev06")
        assert src.url == (
            "https://data.hydrosheds.org/file/HydroBASINS/standard/"
            "hybas_na_lev06_v1c.zip"
        )

    def test_hydrobasins_bad_unit_rejected(self):
        ds = get_mirror_dataset("hydrobasins")
        from cas.mirror.datasets import sources_for_unit

        with pytest.raises(MirrorUnitError):
            sources_for_unit(ds, "na_lev99")
        with pytest.raises(MirrorUnitError):
            sources_for_unit(ds, "zz_lev06")

    def test_merit_nine_regions_with_pairs_and_fork_flag(self):
        ds = get_mirror_dataset("merit_basins")
        assert ds.unit_ids() == [str(i) for i in range(1, 10)]
        # One Google Drive zip per region (the Princeton host is DNS-dead;
        # reachhydro moved to Drive/Globus — verified live 2026-06-12),
        # carrying BOTH layers as per-role member patterns.
        (src,) = ds.sources_for_unit("7")
        assert src.url.startswith("https://drive.google.com/uc?export=download&id=")
        assert src.archive_name == "pfaf_7_MERIT_Hydro_v07_Basins_v01.zip"
        assert set(src.members) == {"catchments", "rivernet"}
        assert src.members["catchments"] == ["cat_pfaf_7_*.shp"]
        assert ds.license.license_flags == ["license-fork:odbl-or-cc-by-nc"]
        # Region 9 was fetched by a maintainer: sha256 baked in (TOFU → verified).
        (src9,) = ds.sources_for_unit("9")
        assert src9.sha256 is not None
        # Catchment shapefiles ship without .prj; WGS84 is assigned + recorded.
        assert ds.assumed_crs == "EPSG:4326"

    def test_tdx_dynamic_and_sync_all_refused(self):
        ds = get_mirror_dataset("tdx_hydro")
        assert ds.dynamic_units and ds.units_required_for_sync
        assert ds.license.license_flags == ["share-alike"]
        from cas.mirror.datasets import sources_for_unit

        srcs = sources_for_unit(ds, "714")
        assert {s.role for s in srcs} == {"catchments", "streams"}
        assert any(s.url.endswith("catchments_714.parquet") for s in srcs)
        assert any(s.url.endswith("streams_714.gpkg") for s in srcs)

    def test_nws_single_conus_unit_unverified(self):
        ds = get_mirror_dataset("nws_hydrofabric")
        assert ds.unit_ids() == ["conus"]
        assert ds.license.license_flags == ["license-unverified-at-source"]
        assert not ds.license.license_verified
        assert "PROVISIONAL" in ds.license.disclaimer
        assert "7 GB" in ds.disk_note or "~7 GB" in ds.disk_note


# ── Sync-all refusal ────────────────────────────────────────────────


class TestSyncAllRefusal:
    def test_tdx_sync_without_unit_refused(self):
        ds = get_mirror_dataset("tdx_hydro")
        with pytest.raises(MirrorUnitError, match="refused"):
            resolve_unit_ids(ds, None, explicit=True)

    def test_hydrobasins_sync_without_unit_refused(self):
        # dynamic_units ⇒ a bare sync can't enumerate region×level combos.
        ds = get_mirror_dataset("hydrobasins")
        with pytest.raises(MirrorUnitError):
            resolve_unit_ids(ds, None, explicit=True)

    def test_merit_sync_all_allowed(self):
        ds = get_mirror_dataset("merit_basins")
        assert resolve_unit_ids(ds, None) == [str(i) for i in range(1, 10)]


# ── Path-delivery materialization per format ────────────────────────


class TestPathDeliveryFormats:
    @respx.mock
    def test_gpkg_conversion_path_delivery(self, mirror_root, tmp_path):
        """Shapefile-zip unit → GeoPackage, delivered as a path (not clipped)."""
        url = "https://mirror.invalid/fakegf_a.zip"
        respx.get(url).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path, shp_name="data_a"))
        )
        ds = make_fake_unit_dataset(
            slug="fakegf", units=("a",), delivery="path", unit_processing="gpkg",
            url_template="https://mirror.invalid/{slug}_{unit}.zip",
        )
        with registered(ds):
            (result,) = mirror_fetch_sync("fakegf", unit="a")
            assert result.unit == "a"
            assert result.format == "gpkg"
            assert result.path.suffix == ".gpkg"
            assert result.path.is_file()
            # Path delivery returns the in-mirror path; provenance says so.
            assert "path-delivery" in result.provenance
            assert "NOT bbox-clipped" in result.provenance
            assert verify(ds) == []

    @respx.mock
    def test_raw_parquet_plus_gpkg_pair(self, mirror_root, tmp_path):
        """TDX-style raw delivery: parquet + gpkg kept byte-identical."""
        from cas.mirror.models import MirrorDataset, MirrorLicense, MirrorSource

        cat_url = "https://mirror.invalid/catchments_v1.parquet"
        streams_url = "https://mirror.invalid/streams_v1.gpkg"
        ds = MirrorDataset(
            slug="fakeraw", version="1.0", display_name="Fake Raw",
            sources=[
                MirrorSource(url=cat_url, archive_name="catchments_v1.parquet",
                             unit="v1", role="catchments", size_bytes_approx=10_000),
                MirrorSource(url=streams_url, archive_name="streams_v1.gpkg",
                             unit="v1", role="streams", size_bytes_approx=10_000),
            ],
            delivery="path", unit_scheme="vpu", unit_processing="raw",
            license=MirrorLicense(license="CC-BY-SA-4.0", license_verified=True,
                                  attribution="x", license_flags=["share-alike"]),
            citation="x. Accessed {access_date}.",
        )
        cat_bytes = build_fake_parquet_bytes(tmp_path)
        gpkg_bytes = build_fake_gpkg_bytes(tmp_path, layer="streams")
        respx.get(cat_url).mock(return_value=httpx.Response(200, content=cat_bytes))
        respx.get(streams_url).mock(return_value=httpx.Response(200, content=gpkg_bytes))
        with registered(ds):
            (result,) = mirror_fetch_sync("fakeraw", unit="v1")
            assert set(result.paths) == {"catchments", "streams"}
            assert result.paths["catchments"].suffix == ".parquet"
            assert result.paths["streams"].suffix == ".gpkg"
            # Raw mode keeps bytes identical.
            assert result.paths["catchments"].read_bytes() == cat_bytes
            assert "geoparquet" in result.format and "gpkg" in result.format
            assert verify(ds) == []

    @respx.mock
    def test_tar_member_extraction(self, mirror_root, tmp_path):
        """NWS-style: tar.gz → extract the gpkg member."""
        url = "https://mirror.invalid/fakenws_conus.zip"
        ds = make_fake_unit_dataset(
            slug="fakenws", units=("conus",), delivery="path",
            unit_processing="tar_member",
            url_template="https://mirror.invalid/{slug}_{unit}.zip",
        )
        ds = ds.model_copy(update={"shapefile_patterns": ["*conus_nextgen.gpkg", "*.gpkg"]})
        respx.get(url).mock(
            return_value=httpx.Response(200, content=build_fake_gpkg_tar(tmp_path))
        )
        with registered(ds):
            (result,) = mirror_fetch_sync("fakenws", unit="conus")
            assert result.path.name == "conus_nextgen.gpkg"
            assert result.format == "gpkg"
            # The decoy README was not kept.
            ddir = dataset_dir(ds)
            assert not list(ddir.rglob("README.txt"))
            assert not list(ddir.glob("*.tar.gz"))

    @respx.mock
    def test_output_dir_copies_files_and_notice(self, mirror_root, tmp_path):
        url = "https://mirror.invalid/fakegf_a.zip"
        respx.get(url).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path, shp_name="data_a"))
        )
        ds = make_fake_unit_dataset(
            slug="fakegf", units=("a",), delivery="path", unit_processing="gpkg",
            url_template="https://mirror.invalid/{slug}_{unit}.zip",
        )
        with registered(ds):
            out = tmp_path / "delivered"
            (result,) = mirror_fetch_sync("fakegf", unit="a", output_dir=out)
            assert result.path.parent == out
            assert result.path.is_file()


# ── HydroBASINS acknowledgment + Exhibit B notice ───────────────────


class TestHydroBasinsAck:
    def test_lazy_fetch_refuses_without_acknowledgment(self, mirror_root):
        # No network call should happen: the ack gate fires first.
        with pytest.raises(MirrorLicenseError) as exc:
            mirror_fetch_sync("hydrobasins", unit="na_lev06")
        msg = str(exc.value)
        assert "acknowledgment" in msg
        assert "CAS_MIRROR_ACCEPT_LICENSES=hydrobasins" in msg

    @respx.mock
    def test_env_acceptance_materializes_and_embeds_notice(self, mirror_root, tmp_path):
        ds = get_mirror_dataset("hydrobasins")
        from cas.mirror.datasets import sources_for_unit

        (src,) = sources_for_unit(ds, "na_lev06")
        respx.get(src.url).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path, shp_name="hybas_na_lev06_v1c"))
        )
        cas.configure(mirror_accept_licenses=["hydrobasins"])
        (result,) = mirror_fetch_sync("hydrobasins", unit="na_lev06")
        # Exhibit B notice copied next to the unit and referenced on the result.
        assert result.notice_path is not None and result.notice_path.is_file()
        assert "EXHIBIT B" in result.notice_path.read_text()
        assert "hydrosheds_v1_exhibit_b" in result.notice_path.name
        assert "notice" in result.provenance
        # Acknowledgment recorded in the manifest.
        manifest = load_manifest(ds)
        assert manifest.acknowledgments[0].accepted_via == "env"
        assert any(f.role == "license_notice" for f in manifest.files)

    @respx.mock
    def test_explicit_sync_records_acceptance(self, mirror_root, tmp_path):
        ds = get_mirror_dataset("hydrobasins")
        from cas.mirror.datasets import sources_for_unit

        (src,) = sources_for_unit(ds, "af_lev04")
        respx.get(src.url).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path, shp_name="hybas_af_lev04_v1c"))
        )
        ensure_units_materialized(
            ds, ["af_lev04"], explicit=True, licenses_accepted=True, ack_via="flag"
        )
        assert is_unit_materialized(ds, "af_lev04")
        assert load_manifest(ds).acknowledgments[0].accepted_via == "flag"


# ── Bbox/point unit resolution ──────────────────────────────────────


class TestGeofabricUnitResolution:
    def test_merit_pfaf_for_bbox(self):
        # Official ReadMe mapping: 1 Africa … 6 South America, 7 North
        # America, 9 Greenland (NOT the native handler's table).
        from cas.mirror.units import merit_pfaf_for_bbox

        # Amazon basin → region 6 (South America).
        assert "6" in merit_pfaf_for_bbox((-70.0, -5.0, -60.0, 0.0))
        # Alps → region 2 (Europe).
        assert merit_pfaf_for_bbox((5.0, 45.0, 10.0, 50.0)) == ["2"]
        # Greenland east coast → region 9 (empirically verified extent).
        assert "9" in merit_pfaf_for_bbox((-40.0, 64.0, -38.0, 66.0))
        # Logan/Bear box → region 7 (North America).
        assert "7" in merit_pfaf_for_bbox((-112.5, 41.0, -111.0, 42.5))

    def test_hydrobasins_regions_for_bbox(self):
        from cas.mirror.units import hydrobasins_regions_for_bbox, hydrobasins_units_for_bbox

        assert "na" in hydrobasins_regions_for_bbox((-100.0, 40.0, -90.0, 45.0))
        assert hydrobasins_units_for_bbox((-100.0, 40.0, -90.0, 45.0), 6) == ["na_lev06"]

    def test_hydrobasins_level_bounds(self):
        from cas.mirror.units import hydrobasins_units_for_bbox

        with pytest.raises(MirrorUnitError):
            hydrobasins_units_for_bbox((-100.0, 40.0, -90.0, 45.0), 13)

    def test_fetch_subset_dataset_rejected(self, mirror_root):
        from cas.core.exceptions import MirrorError

        with pytest.raises(MirrorError, match="not a path-delivery"):
            mirror_fetch_sync("wokam", unit="x")

    def test_subset_rejects_path_delivery_dataset(self, mirror_root, tmp_path):
        from cas.core.exceptions import MirrorError
        from cas.mirror.query import mirror_subset_sync

        with pytest.raises(MirrorError, match="path-delivery"):
            mirror_subset_sync("merit_basins", (0.0, 0.0, 1.0, 1.0), tmp_path)
