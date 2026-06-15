# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""CLI tests for ``cas mirror sync|status|verify|remove`` including the
interactive / flag / env license-acknowledgment paths."""

from __future__ import annotations

import httpx
import pytest
import respx
from click.testing import CliRunner

pytest.importorskip("geopandas")
pytest.importorskip("pyarrow")

from cas.cli.main import cli

from .mirror_utils import (
    FAKE_URL,
    build_fake_zip,
    make_fake_dataset,
    make_fake_unit_dataset,
    registered,
)

# mirror_root fixture is shared from conftest.py.


def test_mirror_group_help():
    result = CliRunner().invoke(cli, ["mirror", "--help"])
    assert result.exit_code == 0
    assert "curated local mirrors".lower() in result.output.lower()
    for sub in ("sync", "status", "verify", "remove"):
        assert sub in result.output


def test_status_lists_shipped_datasets(mirror_root):
    result = CliRunner().invoke(cli, ["mirror", "status"])
    assert result.exit_code == 0
    assert "glhymps==2.0" in result.output
    assert "hydrolakes==1.0" in result.output
    assert "wokam==1.0" in result.output
    assert "not synced" in result.output
    assert "unconfirmed" in result.output  # WOKAM flag surfaced


class TestSyncCommand:
    @respx.mock
    def test_sync_materializes(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            result = CliRunner().invoke(cli, ["mirror", "sync", "fakeveg"])
            assert result.exit_code == 0, result.output
            assert "OK" in result.output
            assert "4 features" in result.output

            status = CliRunner().invoke(cli, ["mirror", "status"])
            assert "synced" in status.output

    @respx.mock
    def test_sync_unknown_dataset_fails(self, mirror_root):
        result = CliRunner().invoke(cli, ["mirror", "sync", "nope"])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    @respx.mock
    def test_sync_single_unit_spec(self, mirror_root, tmp_path):
        from .mirror_utils import make_fake_unit_dataset

        respx.get("https://mirror.invalid/fakeunits_a.zip").mock(
            return_value=httpx.Response(
                200, content=build_fake_zip(tmp_path, shp_name="data_a")
            )
        )
        with registered(make_fake_unit_dataset()):
            result = CliRunner().invoke(cli, ["mirror", "sync", "fakeunits:a"])
            assert result.exit_code == 0, result.output
            assert "fakeunits:a" in result.output

            status = CliRunner().invoke(cli, ["mirror", "status"])
            assert "1/2 units" in status.output

    @respx.mock
    def test_sync_unit_on_global_dataset_fails(self, mirror_root):
        with registered(make_fake_dataset()):
            result = CliRunner().invoke(cli, ["mirror", "sync", "fakeveg:a"])
            assert result.exit_code == 1
            assert "no" in result.output and "unit" in result.output

    def test_rgi_status_shows_disk_note(self, mirror_root):
        result = CliRunner().invoke(cli, ["mirror", "status"])
        assert "rgi7==7.0" in result.output
        assert "19 regions" in result.output or "1-2 regions" in result.output


class TestAckCli:
    @respx.mock
    def test_interactive_prompt_accept(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset(slug="ackveg", requires_acknowledgment=True)):
            # Force the interactive branch (CliRunner's stdin is not a tty).
            runner = CliRunner()
            with pytest.MonkeyPatch().context() as mp:
                mp.setattr("cas.cli.main._stdin_is_interactive", lambda: True)
                result = runner.invoke(cli, ["mirror", "sync", "ackveg"], input="y\n")
            assert result.exit_code == 0, result.output
            assert "requires license acknowledgment" in result.output
            assert "FAKE-ACK-1.0" in result.output

            from cas.mirror import get_mirror_dataset, load_manifest

            manifest = load_manifest(get_mirror_dataset("ackveg"))
            assert manifest.acknowledgments[0].accepted_via == "interactive"

    @respx.mock
    def test_flag_acceptance(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset(slug="ackveg", requires_acknowledgment=True)):
            result = CliRunner().invoke(
                cli, ["mirror", "sync", "ackveg", "--accept-licenses"]
            )
            assert result.exit_code == 0, result.output
            from cas.mirror import get_mirror_dataset, load_manifest

            manifest = load_manifest(get_mirror_dataset("ackveg"))
            assert manifest.acknowledgments[0].accepted_via == "flag"

    @respx.mock
    def test_noninteractive_refusal(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset(slug="ackveg", requires_acknowledgment=True)):
            # CliRunner stdin is not a tty → non-interactive branch.
            result = CliRunner().invoke(cli, ["mirror", "sync", "ackveg"])
            assert result.exit_code == 1
            assert "non-interactive" in result.output or "acknowledgment" in result.output


class TestVerifyRemoveCli:
    @respx.mock
    def test_verify_and_remove(self, mirror_root, tmp_path):
        respx.get(FAKE_URL).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path))
        )
        with registered(make_fake_dataset()):
            CliRunner().invoke(cli, ["mirror", "sync", "fakeveg"])

            verify = CliRunner().invoke(cli, ["mirror", "verify", "fakeveg"])
            assert verify.exit_code == 0
            assert "OK" in verify.output

            rm = CliRunner().invoke(cli, ["mirror", "remove", "fakeveg"])
            assert rm.exit_code == 0
            assert "Removed" in rm.output

            rm2 = CliRunner().invoke(cli, ["mirror", "remove", "fakeveg"])
            assert "not materialized" in rm2.output

    def test_verify_nothing_materialized(self, mirror_root):
        result = CliRunner().invoke(cli, ["mirror", "verify"])
        assert result.exit_code == 0
        assert "Nothing materialized" in result.output


UNIT_URL = "https://mirror.invalid/fakeunits_{unit}.zip"


class TestUnitSyncCli:
    @respx.mock
    def test_sync_single_unit(self, mirror_root, tmp_path):
        for u in ("a", "b"):
            respx.get(UNIT_URL.format(unit=u)).mock(
                return_value=httpx.Response(200, content=build_fake_zip(tmp_path, shp_name=f"data_{u}"))
            )
        with registered(make_fake_unit_dataset()):
            result = CliRunner().invoke(cli, ["mirror", "sync", "fakeunits:a"])
            assert result.exit_code == 0, result.output
            assert "fakeunits:a" in result.output
            # Only unit 'a' was fetched.
            from cas.mirror import get_mirror_dataset, load_manifest

            assert load_manifest(get_mirror_dataset("fakeunits")).units == ["a"]

    @respx.mock
    def test_status_shows_units_fraction(self, mirror_root, tmp_path):
        respx.get(UNIT_URL.format(unit="a")).mock(
            return_value=httpx.Response(200, content=build_fake_zip(tmp_path, shp_name="data_a"))
        )
        with registered(make_fake_unit_dataset()):
            CliRunner().invoke(cli, ["mirror", "sync", "fakeunits:a"])
            status = CliRunner().invoke(cli, ["mirror", "status"])
            assert "1/2 units" in status.output

    def test_sync_all_refused_for_units_required(self, mirror_root):
        ds = make_fake_unit_dataset(slug="bigunits", units_required_for_sync=True)
        with registered(ds):
            result = CliRunner().invoke(cli, ["mirror", "sync", "bigunits"])
            assert result.exit_code == 1
            assert "refused" in result.output.lower()

    def test_unit_on_global_dataset_is_rejected(self, mirror_root):
        result = CliRunner().invoke(cli, ["mirror", "sync", "wokam:7"])
        assert result.exit_code == 1
        assert "no unit" in result.output.lower()


class TestGeofabricCli:
    def test_tdx_sync_all_refused(self, mirror_root):
        result = CliRunner().invoke(cli, ["mirror", "sync", "tdx_hydro"])
        assert result.exit_code == 1
        assert "refused" in result.output.lower()

    def test_hydrobasins_noninteractive_ack_refusal(self, mirror_root):
        # Lazy/explicit non-interactive sync of an ack-requiring geofabric.
        result = CliRunner().invoke(cli, ["mirror", "sync", "hydrobasins:na_lev06"])
        assert result.exit_code == 1
        assert "acknowledgment" in result.output.lower()

    def test_status_shows_geofabric_disk_notes(self, mirror_root):
        result = CliRunner().invoke(cli, ["mirror", "status"])
        assert result.exit_code == 0
        assert "nws_hydrofabric==2.2" in result.output
        # The disk-cost note is surfaced before any download.
        assert "GeoPackage" in result.output or "tar.gz" in result.output

    @respx.mock
    def test_hydrobasins_sync_with_accept_flag_embeds_notice(self, mirror_root, tmp_path):
        from cas.mirror import get_mirror_dataset
        from cas.mirror.datasets import sources_for_unit

        ds = get_mirror_dataset("hydrobasins")
        (src,) = sources_for_unit(ds, "eu_lev05")
        respx.get(src.url).mock(
            return_value=httpx.Response(
                200, content=build_fake_zip(tmp_path, shp_name="hybas_eu_lev05_v1c")
            )
        )
        result = CliRunner().invoke(
            cli, ["mirror", "sync", "hydrobasins:eu_lev05", "--accept-licenses"]
        )
        assert result.exit_code == 0, result.output
        assert "hydrobasins:eu_lev05" in result.output
        from cas.mirror import load_manifest

        manifest = load_manifest(ds)
        assert any(f.role == "license_notice" for f in manifest.files)
