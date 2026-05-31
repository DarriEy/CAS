# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the TerraClimate connector (Planetary Computer Zarr datacube)."""

from __future__ import annotations

import pytest

from cas.connectors.terraclimate import ARIDITY_KEY, TC_VARS, TerraClimateConnector
from cas.core.models import Geometry, QualityFlag

network = pytest.mark.network


@pytest.fixture
def geom() -> Geometry:
    # Small box in central Iraq (arid).
    return Geometry(
        type="Polygon",
        coordinates=[[
            [44.0, 33.0], [44.1, 33.0], [44.1, 33.1], [44.0, 33.1], [44.0, 33.0],
        ]],
    )


class TestTerraClimateMetadata:
    @pytest.mark.asyncio
    async def test_list_datasets(self):
        conn = TerraClimateConnector()
        datasets = await conn.list_datasets()
        # one per curated variable plus the derived aridity index
        assert len(datasets) == len(TC_VARS) + 1
        ids = {d.id for d in datasets}
        assert "terraclimate:pet" in ids
        assert f"terraclimate:{ARIDITY_KEY}" in ids
        for d in datasets:
            assert d.provider == "terraclimate"
            assert d.id.startswith("terraclimate:")
            assert d.resolution_m > 0
            assert d.variables[0].data_type.value == "continuous"

    def test_protocol_and_slug(self):
        conn = TerraClimateConnector()
        assert conn.slug == "terraclimate"
        # Datacube, not COG — modelled as the opendap (remote multidim) protocol.
        assert conn.protocol == "opendap"

    @pytest.mark.asyncio
    async def test_unknown_dataset_raises(self, geom):
        from cas.core.exceptions import DataFormatError

        conn = TerraClimateConnector()
        with pytest.raises(DataFormatError):
            await conn.extract("terraclimate:not_a_var", geom)


class TestTerraClimateReduction:
    """Unit-test the pure array reductions without touching the network, by
    feeding a tiny in-memory xarray Dataset."""

    @staticmethod
    def _toy_dataset():
        xr = pytest.importorskip("xarray")
        import numpy as np

        lat = np.array([33.2, 33.1, 33.0, 32.9])  # descending, like climate cubes
        lon = np.array([43.9, 44.0, 44.1, 44.2])
        time = np.array(["2023-01-01", "2023-02-01"], dtype="datetime64[ns]")
        shape = (len(time), len(lat), len(lon))
        pet = np.full(shape, 100.0)
        ppt = np.full(shape, 25.0)
        return xr.Dataset(
            {
                "pet": (("time", "lat", "lon"), pet),
                "ppt": (("time", "lat", "lon"), ppt),
            },
            coords={"time": time, "lat": lat, "lon": lon},
        )

    def test_reduce_variable_latest_time(self):
        conn = TerraClimateConnector()
        ds = self._toy_dataset()
        value, n = conn._reduce_variable(ds, "pet", (43.95, 32.95, 44.15, 33.15), None)
        assert value == pytest.approx(100.0)
        assert n > 0

    def test_aridity_ratio(self):
        conn = TerraClimateConnector()
        ds = self._toy_dataset()
        # 2 months each: annual P = 25*2 = 50, annual PET = 100*2 = 200 -> AI 0.25
        value, n = conn._compute_aridity(ds, (43.95, 32.95, 44.15, 33.15))
        assert value == pytest.approx(0.25)
        assert n > 0


@network
class TestTerraClimateExtract:
    """Live extraction against the Planetary Computer Zarr store (needs the
    'climate' extra installed and network access)."""

    @pytest.mark.asyncio
    async def test_pet_arid_site(self, geom):
        conn = TerraClimateConnector()
        r = await conn.extract("terraclimate:pet", geom)
        assert r.quality == QualityFlag.GOOD
        assert isinstance(r.value, float) and r.value > 0

    @pytest.mark.asyncio
    async def test_aridity_arid_site(self, geom):
        conn = TerraClimateConnector()
        r = await conn.extract(f"terraclimate:{ARIDITY_KEY}", geom)
        assert r.quality == QualityFlag.GOOD
        # central Iraq is arid: aridity index well below the humid threshold (0.65)
        assert isinstance(r.value, float) and 0.0 <= r.value < 0.5
