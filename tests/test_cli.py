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


def test_verify_help():
    result = CliRunner().invoke(cli, ["verify", "--help"])
    assert result.exit_code == 0
    assert "reachability" in result.output.lower()


def test_export_inventory_help():
    result = CliRunner().invoke(cli, ["export-inventory", "--help"])
    assert result.exit_code == 0
    assert "providers.yaml" in result.output


def test_health_help():
    result = CliRunner().invoke(cli, ["health", "--help"])
    assert result.exit_code == 0
    assert "polygon" in result.output
