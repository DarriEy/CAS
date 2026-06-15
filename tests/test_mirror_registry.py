# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Mirror registry integrity, spec parsing, the ack-refusal gate, and the
HTTP-exclusion guarantee. No geopandas required — nothing materializes here."""

from __future__ import annotations

import pytest

import cas
from cas.core.exceptions import (
    MirrorDatasetNotFoundError,
    MirrorError,
    MirrorLicenseError,
    MirrorOfflineError,
)
from cas.mirror import (
    get_mirror_dataset,
    list_mirror_datasets,
    parse_dataset_spec,
)
from cas.mirror.datasets import register_mirror_dataset

from .mirror_utils import make_fake_dataset, registered


@pytest.fixture
def clean_settings():
    from cas.core.config import _overrides, get_settings

    saved = dict(_overrides)
    yield
    _overrides.clear()
    _overrides.update(saved)
    get_settings.cache_clear()


class TestShippedRegistry:
    def test_three_slice1_datasets_present(self):
        specs = {ds.spec for ds in list_mirror_datasets()}
        assert {"glhymps==2.0", "hydrolakes==1.0", "wokam==1.0"} <= specs

    def test_glhymps_declaration(self):
        ds = get_mirror_dataset("glhymps")
        assert ds.version == "2.0"
        assert ds.sources[0].url == "https://borealisdata.ca/api/access/datafile/71909"
        assert ds.default_buffer_deg == 0.1
        assert {"Porosity", "logK_Ice", "logK_Ferr"} <= set(ds.known_columns)
        assert ds.license.license == "CC-BY-4.0"
        assert ds.license.license_verified is True
        assert ds.license.license_flags == []
        assert "Huscroft" in ds.citation

    def test_hydrolakes_declaration(self):
        ds = get_mirror_dataset("hydrolakes")
        assert ds.version == "1.0"
        assert ds.sources[0].url == (
            "https://data.hydrosheds.org/file/hydrolakes/HydroLAKES_polys_v10_shp.zip"
        )
        assert ds.default_buffer_deg == 0.1
        assert {"Hylak_id", "Lake_area", "Depth_avg"} <= set(ds.known_columns)
        assert ds.license.license_verified is True
        assert "Messager" in ds.citation

    def test_wokam_declaration_and_unconfirmed_license(self):
        ds = get_mirror_dataset("wokam")
        assert ds.sources[0].url == (
            "https://download.bgr.de/bgr/grundwasser/whymap/shp/WHYMAP_WOKAM_v1.zip"
        )
        assert ds.default_buffer_deg == 0.5
        assert "rock_type" in ds.known_columns  # live v1 layer lowercases it
        # Design §8: explicit unconfirmed label + hard-wired attribution.
        assert ds.license.license == "BGR terms (GeoNutzV-eligible, unconfirmed)"
        assert ds.license.license_verified is False
        assert ds.license.license_flags == ["unconfirmed"]
        assert ds.license.attribution == (
            "Datenquelle: WHYMAP WOKAM, © BGR Berlin, "
            "IAH Reading, KIT Karlsruhe, UNESCO Paris 2017"
        )

    def test_no_slice1_dataset_requires_acknowledgment(self):
        for spec in ("glhymps", "hydrolakes", "wokam"):
            assert get_mirror_dataset(spec).license.requires_acknowledgment is False

    def test_every_dataset_has_citation_and_attribution(self):
        for ds in list_mirror_datasets():
            assert ds.citation
            assert ds.license.attribution
            assert ds.license.license_url

    def test_checksums_are_tofu_or_hex(self):
        for ds in list_mirror_datasets(all_versions=True):
            for source in ds.sources:
                assert source.sha256 is None or (
                    len(source.sha256) == 64
                    and all(c in "0123456789abcdef" for c in source.sha256)
                )


class TestSpecParsing:
    def test_plain_slug(self):
        assert parse_dataset_spec("wokam") == ("wokam", None)

    def test_pinned(self):
        assert parse_dataset_spec("glhymps==2.0") == ("glhymps", "2.0")

    def test_unknown_slug_lists_known(self):
        with pytest.raises(MirrorDatasetNotFoundError, match="glhymps"):
            get_mirror_dataset("nope")

    def test_unknown_version_lists_known(self):
        with pytest.raises(MirrorDatasetNotFoundError, match="2.0"):
            get_mirror_dataset("glhymps==99.0")

    def test_duplicate_registration_rejected(self):
        with registered(make_fake_dataset(slug="dupetest")) as ds, pytest.raises(ValueError):
            register_mirror_dataset(ds)


class TestRefusalGates:
    """Gates that fire before any download — hermetic without geopandas."""

    def test_lazy_path_refuses_unacknowledged_dataset(self, tmp_path, clean_settings):
        cas.configure(mirror_dir=tmp_path / "mirror")
        from cas.mirror import ensure_materialized

        with (
            registered(make_fake_dataset(slug="ackveg", requires_acknowledgment=True)),
            pytest.raises(MirrorLicenseError) as exc,
        ):
            ensure_materialized("ackveg")
        msg = str(exc.value)
        assert "cas mirror sync" in msg
        assert "--accept-licenses" in msg
        assert "CAS_MIRROR_ACCEPT_LICENSES=ackveg" in msg
        assert "Fake Data Consortium" in msg  # terms are surfaced

    def test_explicit_sync_also_refuses_without_acceptance(self, tmp_path, clean_settings):
        cas.configure(mirror_dir=tmp_path / "mirror")
        from cas.mirror import ensure_materialized

        with (
            registered(make_fake_dataset(slug="ackveg", requires_acknowledgment=True)),
            pytest.raises(MirrorLicenseError),
        ):
            ensure_materialized("ackveg", explicit=True)

    def test_offline_mode_is_actionable(self, tmp_path, clean_settings):
        cas.configure(mirror_dir=tmp_path / "mirror", mirror_offline=True)
        from cas.mirror import ensure_materialized

        with (
            registered(make_fake_dataset(slug="offveg")),
            pytest.raises(MirrorOfflineError, match="cas mirror sync offveg==1.0"),
        ):
            ensure_materialized("offveg")

    def test_auto_materialize_disabled_blocks_lazy_only(self, tmp_path, clean_settings):
        cas.configure(mirror_dir=tmp_path / "mirror", mirror_auto_materialize=False)
        from cas.mirror import ensure_materialized

        with registered(make_fake_dataset(slug="lazyveg")), pytest.raises(MirrorError, match="cas mirror sync"):
            ensure_materialized("lazyveg", explicit=False)


class TestSettings:
    def test_mirror_dir_default_and_expansion(self):
        from pathlib import Path

        from cas.core.config import Settings

        s = Settings()
        assert s.mirror_dir == Path("~/.cas/mirror").expanduser()
        assert "~" not in str(s.mirror_dir)

    def test_accept_licenses_env_csv(self, monkeypatch):
        from cas.core.config import Settings

        monkeypatch.setenv("CAS_MIRROR_ACCEPT_LICENSES", "wokam, hydrobasins")
        assert Settings().mirror_accept_licenses == ["wokam", "hydrobasins"]

    def test_configure_accepts_mirror_fields(self, tmp_path, clean_settings):
        s = cas.configure(mirror_dir=tmp_path, mirror_offline=True)
        assert s.mirror_dir == tmp_path
        assert s.mirror_offline is True


class TestHTTPExclusion:
    def test_no_mirror_routes_registered(self):
        fastapi = pytest.importorskip("fastapi")  # noqa: F841
        from cas.api.app import create_app

        app = create_app()
        paths = [route.path for route in app.routes]
        assert paths  # sanity: the app has routes
        assert not any("mirror" in p.lower() for p in paths)

    def test_facade_exports_mirror_names(self):
        assert "mirror_subset" in cas.__all__
        assert "mirror_subset_sync" in cas.__all__
        assert callable(cas.mirror_subset_sync)
