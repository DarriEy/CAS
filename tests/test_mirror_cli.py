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

import cas
from cas.cli.main import cli

from .mirror_utils import FAKE_URL, build_fake_zip, make_fake_dataset, registered


@pytest.fixture
def mirror_root(tmp_path):
    from cas.core.config import _overrides, get_settings

    saved = dict(_overrides)
    cas.configure(mirror_dir=tmp_path / "mirror")
    yield tmp_path / "mirror"
    _overrides.clear()
    _overrides.update(saved)
    get_settings.cache_clear()


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
