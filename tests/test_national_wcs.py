# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the NationalWCSConnector factory pattern."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from cas.connectors.national_wcs import NationalWCSConnector
from cas.core.models import Geometry
from cas.core.registry import discover, get_connector, list_providers


@pytest.fixture
def test_geometry() -> Geometry:
    return Geometry(
        type="Polygon",
        coordinates=[[
            [-96.6, 39.0],
            [-96.5, 39.0],
            [-96.5, 39.1],
            [-96.6, 39.1],
            [-96.6, 39.0],
        ]],
    )


class TestNationalWCSFactory:
    @pytest.fixture(autouse=True)
    def _discover(self):
        discover()

    def test_national_wcs_connectors_registered(self):
        """At least 40 providers should use the NationalWCSConnector factory."""
        national = [
            slug for slug in list_providers()
            if issubclass(get_connector(slug), NationalWCSConnector)
        ]
        assert len(national) >= 40, f"Expected 40+ national WCS connectors, got {len(national)}"

    def test_all_national_connectors_have_config(self):
        for slug in list_providers():
            cls = get_connector(slug)
            if issubclass(cls, NationalWCSConnector) and cls is not NationalWCSConnector:
                assert hasattr(cls, "_config"), f"{slug} missing _config"
                cfg = cls._config
                assert cfg.slug == slug, f"{slug}: config slug mismatch"
                assert cfg.wcs_url, f"{slug}: empty wcs_url"
                assert cfg.coverage_id, f"{slug}: empty coverage_id"
                assert cfg.variable is not None, f"{slug}: no variable"
                assert cfg.resolution_m > 0, f"{slug}: invalid resolution"
                assert cfg.bbox is not None, f"{slug}: no bbox"

    def test_country_coverage(self):
        """Verify we have providers for key countries."""
        providers = list_providers()
        countries = {
            "norway": ["norway_dem", "norway_ar5", "norway_geology"],
            "germany": ["germany_dgm200", "germany_nrw_dgm1", "germany_soil"],
            "switzerland": ["swiss_dem", "swiss_groundwater", "swiss_stream_order"],
            "ireland": ["ireland_soil", "ireland_aquifer", "ireland_catchments"],
            "uk": ["uk_lidar", "uk_bgs_geology"],
            "finland": ["finland_dem", "finland_soil", "finland_lc"],
            "netherlands": ["netherlands_ahn", "netherlands_soil"],
            "spain": ["spain_mdt", "spain_lithology", "spain_hydro"],
            "denmark": ["denmark_dem", "denmark_terrain"],
            "australia": ["australia_dem", "australia_dea_lc"],
        }
        for country, expected in countries.items():
            for slug in expected:
                assert slug in providers, f"Missing {slug} for {country}"


class TestNationalWCSExtract:
    """Test extract flow with mocked HTTP responses."""

    @pytest.fixture(autouse=True)
    def _discover(self):
        discover()

    @pytest.mark.asyncio
    async def test_extract_with_mock_geotiff(self, test_geometry):
        """Test that NationalWCSConnector.extract works with mocked WCS response."""
        from rasterio.transform import from_bounds

        mock_raster = np.array([[400, 410], [390, 405]], dtype=np.float32)
        mock_transform = from_bounds(-96.6, 39.0, -96.5, 39.1, 2, 2)

        # Pick a known national connector
        cls = get_connector("norway_dem")
        instance = cls()

        import httpx

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/tiff"}
        mock_response.content = _make_mock_geotiff(mock_raster, mock_transform)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await instance.extract(
                dataset_id="norway_dem:elevation",
                geometry=test_geometry,
            )

        assert result.provider == "norway_dem"
        assert result.variable == "elevation"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slug", ["finland_dem", "denmark_dem", "denmark_terrain", "germany_dgm200"])
    async def test_gated_connector_raises_before_request(self, slug, test_geometry, monkeypatch):
        """A credential-gated connector raises RegistrationRequiredError up front.

        Without this, a tokenless request hits the server and returns a bare
        401/403 that surfaces as a confusing 'down' provider.
        """
        from cas.core.exceptions import RegistrationRequiredError

        cls = get_connector(slug)
        monkeypatch.delenv(cls._config.auth_token_env, raising=False)
        instance = cls()

        # Fail loudly if any HTTP call is attempted before the guard fires.
        def _boom(*args, **kwargs):
            raise AssertionError("network call attempted before credential check")

        with patch("httpx.AsyncClient", _boom), pytest.raises(RegistrationRequiredError) as exc:
            await instance.extract(dataset_id=f"{slug}:x", geometry=test_geometry)

        assert exc.value.registration_url
        assert cls._config.auth_token_env in exc.value.instructions


def _make_mock_geotiff(data: np.ndarray, transform) -> bytes:
    from rasterio.io import MemoryFile

    with MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff",
            dtype=str(data.dtype),
            width=data.shape[1],
            height=data.shape[0],
            count=1,
            crs="EPSG:4326",
            transform=transform,
            nodata=-9999,
        ) as dst:
            dst.write(data, 1)
        return memfile.read()
