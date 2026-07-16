# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Smoke tests for CLI commands — verify they import and run without errors."""

from __future__ import annotations

from click.testing import CliRunner

from cas.cli.main import cli


def test_providers_command():
    result = CliRunner().invoke(cli, ["providers"])
    assert result.exit_code == 0
    assert "copernicus_dem" in result.output


def test_providers_support_command():
    result = CliRunner().invoke(cli, ["providers", "--support"])
    assert result.exit_code == 0
    assert "copernicus_dem" in result.output
    assert "verified" in result.output


def test_verify_help():
    result = CliRunner().invoke(cli, ["verify", "--help"])
    assert result.exit_code == 0
    assert "reachability" in result.output.lower()


def test_export_inventory_help():
    result = CliRunner().invoke(cli, ["export-inventory", "--help"])
    assert result.exit_code == 0
    assert "providers.yaml" in result.output


def test_export_support(tmp_path):
    output = tmp_path / "support.json"
    result = CliRunner().invoke(cli, ["export-support", "-o", str(output)])
    assert result.exit_code == 0
    assert '"verified"' in output.read_text()


def test_health_help():
    result = CliRunner().invoke(cli, ["health", "--help"])
    assert result.exit_code == 0
    assert "--strict" in result.output
