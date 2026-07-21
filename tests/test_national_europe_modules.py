# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Compatibility tests for the decomposed European connector catalog."""

import pytest

from cas.connectors import national_europe
from cas.connectors import national_europe_ireland as ireland
from cas.connectors import national_europe_norway as norway
from cas.core.registry import discover, get_connector


@pytest.mark.parametrize(
    "class_name",
    [
        "IrelandAquiferConnector",
        "IrelandBedrockConnector",
        "IrelandCatchmentsConnector",
        "IrelandFloodConnector",
        "IrelandGravelAquiferConnector",
        "IrelandGroundwaterConnector",
        "IrelandGWVulnerabilityConnector",
        "IrelandPeatlandConnector",
        "IrelandSoilWetDryConnector",
        "IrelandSubsoilConnector",
    ],
)
def test_irish_connectors_remain_available_from_legacy_module(class_name):
    assert getattr(national_europe, class_name) is getattr(ireland, class_name)


def test_norway_connector_remains_available_from_legacy_module():
    assert national_europe.NorwayDEMConnector is norway.NorwayDEMConnector


def test_norway_connector_keeps_registration_behavior():
    discover()
    assert get_connector("norway_dem") is norway.NorwayDEMConnector


def test_irish_connectors_keep_registration_behavior():
    discover()
    expected = {
        "ireland_aquifer": ireland.IrelandAquiferConnector,
        "ireland_bedrock": ireland.IrelandBedrockConnector,
        "ireland_catchments": ireland.IrelandCatchmentsConnector,
        "ireland_flood": ireland.IrelandFloodConnector,
        "ireland_gravel_aquifer": ireland.IrelandGravelAquiferConnector,
        "ireland_groundwater": ireland.IrelandGroundwaterConnector,
        "ireland_gw_vulnerability": ireland.IrelandGWVulnerabilityConnector,
        "ireland_peatland": ireland.IrelandPeatlandConnector,
        "ireland_soil_wetdry": ireland.IrelandSoilWetDryConnector,
        "ireland_subsoil": ireland.IrelandSubsoilConnector,
    }
    assert {slug: get_connector(slug) for slug in expected} == expected
