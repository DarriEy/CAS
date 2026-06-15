# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Manual-staging import tests (``cas mirror import``).

Hermetic: synthetic archives written to disk and imported — NO network at
all (respx with no routes guards against any escape). Covers the verify →
record-as-tofu-import → same-pipeline contract, wrong-content rejection,
registry-checksum enforcement, the acknowledgment gate, unit-scoped imports
(including a MERIT-style multi-layer zip), raw-format validation, and the
CLI surface.
"""

from __future__ import annotations

import zipfile

import pytest
import respx

pytest.importorskip("geopandas")
pytest.importorskip("pyarrow")

import cas
from cas.core.exceptions import (
    MirrorError,
    MirrorIntegrityError,
    MirrorLicenseError,
    MirrorUnitError,
)
from cas.mirror import (
    is_materialized,
    is_unit_materialized,
    load_manifest,
    manifest_path,
    mirror_import_sync,
    verify,
)
from cas.mirror.models import MirrorDataset, MirrorLicense, MirrorSource

from .mirror_utils import (
    FAKE_COLUMNS,
    build_fake_gpkg_bytes,
    build_fake_zip,
    make_fake_dataset,
    make_fake_unit_dataset,
    registered,
)


@pytest.fixture
def staged_zip(tmp_path):
    """A synthetic archive 'obtained by the user' (e.g. via Globus)."""
    path = tmp_path / "staging" / "fakeveg.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_fake_zip(tmp_path))
    return path


def _sha256(path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestGlobalImport:
    @respx.mock
    def test_import_runs_full_pipeline_offline(self, mirror_root, staged_zip):
        """No network: verify → extract → convert → manifest, like sync."""
        with registered(make_fake_dataset()) as ds:
            result = mirror_import_sync("fakeveg", staged_zip)

            assert is_materialized(ds)
            assert result.path.is_file() and result.path.suffix == ".parquet"
            assert result.paths == {"query_layer": result.path}
            assert result.unit is None
            assert result.imported_from == str(staged_zip)
            assert "manual-import" in result.provenance
            assert str(staged_zip) in result.provenance
            # The original archive is untouched.
            assert staged_zip.is_file()

            manifest = load_manifest(ds)
            (archive,) = manifest.archives
            assert archive.sha256_source == "tofu-import"
            assert archive.source == "manual-import"
            assert archive.imported_from == str(staged_zip)
            assert archive.sha256 == _sha256(staged_zip)
            assert manifest.feature_count == 4
            assert manifest.columns == FAKE_COLUMNS
            assert verify(ds) == []

    @respx.mock
    def test_imported_mirror_serves_subsets_with_import_provenance(
        self, mirror_root, staged_zip, tmp_path
    ):
        with registered(make_fake_dataset()):
            mirror_import_sync("fakeveg", staged_zip)
            result = cas.mirror_subset_sync(
                "fakeveg", (2.0, 2.0, 4.0, 4.0), tmp_path / "out"
            )
            assert result.feature_count > 0
            assert "manual-import from" in result.provenance

    @respx.mock
    def test_import_from_directory_by_exact_archive_name(self, mirror_root, tmp_path):
        staging = tmp_path / "globus"
        staging.mkdir()
        (staging / "fakeveg.zip").write_bytes(build_fake_zip(tmp_path))
        with registered(make_fake_dataset()) as ds:
            mirror_import_sync("fakeveg", staging)
            assert is_materialized(ds)

    @respx.mock
    def test_directory_missing_expected_archive_is_actionable(self, mirror_root, tmp_path):
        staging = tmp_path / "globus"
        staging.mkdir()
        (staging / "wrong_name.zip").write_bytes(build_fake_zip(tmp_path))
        with registered(make_fake_dataset()) as ds:
            with pytest.raises(MirrorError, match="fakeveg.zip"):
                mirror_import_sync("fakeveg", staging)
            assert not is_materialized(ds)

    @respx.mock
    def test_wrong_content_rejected_no_partial_state(self, mirror_root, tmp_path):
        """A zip without the expected member is refused; nothing materializes."""
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("README.txt", "not a shapefile")
        bad = tmp_path / "fakeveg.zip"
        bad.write_bytes(buf.getvalue())
        with registered(make_fake_dataset()) as ds:
            with pytest.raises(MirrorError, match="No member matching"):
                mirror_import_sync("fakeveg", bad)
            assert not is_materialized(ds)
            assert not manifest_path(ds).exists()

    @respx.mock
    def test_not_a_zip_rejected(self, mirror_root, tmp_path):
        bad = tmp_path / "fakeveg.zip"
        bad.write_text("<html>quota exceeded</html>")
        with registered(make_fake_dataset()) as ds:
            with pytest.raises(MirrorError, match="not a valid zip"):
                mirror_import_sync("fakeveg", bad)
            assert not is_materialized(ds)

    @respx.mock
    def test_registry_checksum_mismatch_is_hard_error(self, mirror_root, staged_zip):
        ds = make_fake_dataset(sha256="0" * 64)
        with registered(ds):
            with pytest.raises(MirrorIntegrityError):
                mirror_import_sync("fakeveg", staged_zip)
            assert not is_materialized(ds)

    @respx.mock
    def test_registry_checksum_match_upgrades_to_verified(self, mirror_root, staged_zip):
        ds = make_fake_dataset(sha256=_sha256(staged_zip))
        with registered(ds):
            mirror_import_sync("fakeveg", staged_zip)
            (archive,) = load_manifest(ds).archives
            assert archive.sha256_source == "registry"
            assert archive.source == "manual-import"

    @respx.mock
    def test_already_materialized_refused(self, mirror_root, staged_zip):
        with registered(make_fake_dataset()):
            mirror_import_sync("fakeveg", staged_zip)
            with pytest.raises(MirrorError, match="already materialized"):
                mirror_import_sync("fakeveg", staged_zip)

    @respx.mock
    def test_unit_arg_rejected_for_global(self, mirror_root, staged_zip):
        with registered(make_fake_dataset()), pytest.raises(MirrorUnitError, match="no unit"):
            mirror_import_sync("fakeveg", staged_zip, unit="x")

    @respx.mock
    def test_missing_source_path(self, mirror_root, tmp_path):
        with registered(make_fake_dataset()), pytest.raises(MirrorError, match="does not exist"):
            mirror_import_sync("fakeveg", tmp_path / "nope.zip")


class TestAcknowledgmentGate:
    @respx.mock
    def test_import_refuses_unacknowledged_terms(self, mirror_root, staged_zip):
        """Even with user-supplied bytes, terms are never accepted silently."""
        ds = make_fake_dataset(requires_acknowledgment=True)
        with registered(ds):
            with pytest.raises(MirrorLicenseError, match="acknowledgment"):
                mirror_import_sync("fakeveg", staged_zip)
            assert not is_materialized(ds)

    @respx.mock
    def test_import_records_acceptance(self, mirror_root, staged_zip):
        ds = make_fake_dataset(requires_acknowledgment=True)
        with registered(ds):
            mirror_import_sync(
                "fakeveg", staged_zip, licenses_accepted=True, ack_via="flag"
            )
            manifest = load_manifest(ds)
            assert manifest.acknowledgments[0].accepted_via == "flag"


class TestUnitScopedImport:
    @respx.mock
    def test_unit_required_for_unit_structured(self, mirror_root, staged_zip):
        ds = make_fake_unit_dataset()
        with registered(ds), pytest.raises(MirrorUnitError, match="--unit"):
            mirror_import_sync("fakeunits", staged_zip)

    @respx.mock
    def test_unit_import_materializes_only_that_unit(self, mirror_root, tmp_path):
        staged = tmp_path / "fakeunits_a.zip"
        staged.write_bytes(build_fake_zip(tmp_path))
        ds = make_fake_unit_dataset()  # units ("a", "b"), geoparquet
        with registered(ds):
            result = mirror_import_sync("fakeunits", staged, unit="a")
            assert result.unit == "a"
            assert is_unit_materialized(ds, "a")
            assert not is_unit_materialized(ds, "b")
            (archive,) = load_manifest(ds).archives
            assert archive.unit == "a"
            assert archive.sha256_source == "tofu-import"
            assert verify(ds) == []

    @respx.mock
    def test_unknown_unit_rejected(self, mirror_root, staged_zip):
        ds = make_fake_unit_dataset()
        with registered(ds), pytest.raises(MirrorUnitError):
            mirror_import_sync("fakeunits", staged_zip, unit="zz")

    @respx.mock
    def test_merit_style_multi_layer_zip_import(self, mirror_root, tmp_path):
        """One zip carrying two role layers (cat_* + riv_*) → two outputs."""
        import io

        import geopandas as gpd
        from shapely.geometry import box

        build = tmp_path / "_multi"
        build.mkdir()
        cat = gpd.GeoDataFrame(
            {"COMID": [1, 2], "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1)]},
            crs="EPSG:4326",
        )
        riv = gpd.GeoDataFrame(
            {"COMID": [1], "NextDownID": [0], "geometry": [box(0, 0, 2, 1).boundary]},
            crs="EPSG:4326",
        )
        cat.to_file(build / "cat_pfaf_7_test.shp")
        riv.to_file(build / "riv_pfaf_7_test.shp")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for f in sorted(build.iterdir()):
                zf.write(f, arcname=f.name)
        staged = tmp_path / "fakemerit_7.zip"
        staged.write_bytes(buf.getvalue())

        ds = MirrorDataset(
            slug="fakemerit",
            version="1",
            display_name="Fake MERIT",
            sources=[
                MirrorSource(
                    url="https://drive.google.com/uc?export=download&id=FAKE",
                    archive_name="fakemerit_7.zip",
                    unit="7",
                    role="data",
                    members={
                        "catchments": ["cat_pfaf_7_*.shp"],
                        "rivernet": ["riv_pfaf_7_*.shp"],
                    },
                )
            ],
            delivery="path",
            unit_scheme="Pfaf-L1",
            unit_processing="gpkg",
            license=MirrorLicense(license="ODbL-1.0", attribution="x"),
            citation="x",
        )
        with registered(ds):
            result = mirror_import_sync("fakemerit", staged, unit="7")
            assert set(result.paths) == {"catchments", "rivernet"}
            assert result.path == result.paths["catchments"]
            assert all(p.suffix == ".gpkg" for p in result.paths.values())
            # Both layers land as separate files with the role recorded.
            (fetched,) = cas.mirror_fetch_sync("fakemerit", unit="7")
            assert set(fetched.paths) == {"catchments", "rivernet"}
            assert "manual-import" in fetched.provenance

    @respx.mock
    def test_multi_archive_unit_needs_directory(self, mirror_root, tmp_path):
        """A unit with several expected archives refuses a single file."""
        staged = tmp_path / "one.zip"
        staged.write_bytes(build_fake_zip(tmp_path))
        ds = make_fake_unit_dataset(roles=("catchments", "streams"))
        with registered(ds), pytest.raises(MirrorError, match="directory"):
            mirror_import_sync("fakeunits", staged, unit="a")

    @respx.mock
    def test_raw_unit_import_validates_magic(self, mirror_root, tmp_path):
        """Raw-delivery imports check parquet/gpkg magic bytes."""
        ds = make_fake_unit_dataset(
            slug="fakeraw2",
            units=("v1",),
            delivery="path",
            unit_processing="raw",
            url_template="https://mirror.invalid/{slug}_{unit}.zip",
        )
        ds = ds.model_copy(
            update={
                "sources": [
                    MirrorSource(
                        url="https://mirror.invalid/streams_v1.gpkg",
                        archive_name="streams_v1.gpkg",
                        unit="v1",
                        role="streams",
                    )
                ]
            }
        )
        bad = tmp_path / "streams_v1.gpkg"
        bad.write_text("<html>not a geopackage</html>")
        with registered(ds):
            with pytest.raises(MirrorError, match="magic"):
                mirror_import_sync("fakeraw2", bad, unit="v1")
            assert not is_unit_materialized(ds, "v1")

            good = tmp_path / "ok" / "streams_v1.gpkg"
            good.parent.mkdir()
            good.write_bytes(build_fake_gpkg_bytes(tmp_path, layer="streams"))
            result = mirror_import_sync("fakeraw2", good, unit="v1")
            assert result.paths["streams"].read_bytes() == good.read_bytes()


class TestImportCli:
    def _invoke(self, args):
        from click.testing import CliRunner

        from cas.cli.main import cli

        return CliRunner().invoke(cli, args)

    @respx.mock
    def test_cli_import_happy_path(self, mirror_root, staged_zip):
        with registered(make_fake_dataset()) as ds:
            result = self._invoke(["mirror", "import", "fakeveg", str(staged_zip)])
            assert result.exit_code == 0, result.output
            assert "OK" in result.output
            assert "tofu-import" in result.output
            assert is_materialized(ds)

    @respx.mock
    def test_cli_unit_via_spec_and_flag_conflict(self, mirror_root, staged_zip):
        ds = make_fake_unit_dataset()
        with registered(ds):
            result = self._invoke(
                ["mirror", "import", "fakeunits:a", str(staged_zip), "--unit", "b"]
            )
            assert result.exit_code != 0
            assert "Conflicting units" in result.output

    @respx.mock
    def test_cli_unit_via_spec(self, mirror_root, tmp_path):
        staged = tmp_path / "fakeunits_a.zip"
        staged.write_bytes(build_fake_zip(tmp_path))
        ds = make_fake_unit_dataset()
        with registered(ds):
            result = self._invoke(["mirror", "import", "fakeunits:a", str(staged)])
            assert result.exit_code == 0, result.output
            assert is_unit_materialized(ds, "a")

    @respx.mock
    def test_cli_ack_required_noninteractive_fails_actionably(
        self, mirror_root, staged_zip, monkeypatch
    ):
        import cas.cli.main as cli_main

        monkeypatch.setattr(cli_main, "_stdin_is_interactive", lambda: False)
        ds = make_fake_dataset(requires_acknowledgment=True)
        with registered(ds):
            result = self._invoke(["mirror", "import", "fakeveg", str(staged_zip)])
            assert result.exit_code != 0
            assert "--accept-licenses" in result.output
            assert not is_materialized(ds)

            ok = self._invoke(
                ["mirror", "import", "fakeveg", str(staged_zip), "--accept-licenses"]
            )
            assert ok.exit_code == 0, ok.output
            assert load_manifest(ds).acknowledgments[0].accepted_via == "flag"
