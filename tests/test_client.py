"""Tests for the CAS Python client SDK.

The synchronous client is exercised against mocked HTTP (respx); the async
client is exercised against the real ASGI app in-process (no network).
"""

from __future__ import annotations

import httpx
import pytest

from cas.client import AsyncCASClient, CASClient, CASError

_EXTRACT_RESPONSE = {
    "request_id": "req-1",
    "geometry_hash": "deadbeef",
    "results": [
        {
            "dataset_id": "copernicus_dem:elevation",
            "variable": "elevation",
            "value": 42.0,
            "units": "m",
            "aggregation": "mean",
            "quality": "good",
            "coverage_fraction": 1.0,
            "pixel_count": 10,
            "provider": "copernicus_dem",
            "elapsed_ms": 5,
            "provenance": "",
        }
    ],
    "warnings": [],
    "elapsed_ms": 12,
}

_PROVIDERS_RESPONSE = {
    "total": 1,
    "limit": 100,
    "offset": 0,
    "count": 1,
    "providers": [
        {
            "slug": "copernicus_dem",
            "name": "Copernicus DEM GLO-30",
            "protocol": "stac_cog",
            "base_url": "https://planetarycomputer.microsoft.com/api/stac/v1",
        }
    ],
}

_DATASET = {
    "id": "copernicus_dem:elevation",
    "provider": "copernicus_dem",
    "name": "Copernicus DEM",
    "variables": [{"name": "elevation", "units": "m", "data_type": "continuous"}],
    "resolution_m": 30.0,
    "crs": "EPSG:4326",
    "bbox": {"min_lon": -180, "min_lat": -60, "max_lon": 180, "max_lat": 84},
    "protocol": "stac_cog",
}


def _route(monkeypatch_respx, method, path, json, status=200):  # pragma: no cover - helper
    return monkeypatch_respx.route(method=method, url__regex=f".*{path}$").mock(
        return_value=httpx.Response(status, json=json)
    )


# ── Synchronous client (respx-mocked) ────────────────────────────────


class TestSyncClient:
    def test_extract_parses_response(self):
        respx = pytest.importorskip("respx")
        with respx.mock(base_url="http://test") as mock:
            mock.post("/api/v1/extract").mock(
                return_value=httpx.Response(200, json=_EXTRACT_RESPONSE)
            )
            with CASClient("http://test") as cas:
                resp = cas.extract(
                    geometry={"type": "Point", "coordinates": [-96.5, 39.0]},
                    dataset_ids=["copernicus_dem:elevation"],
                )
        assert resp.request_id == "req-1"
        assert resp.results[0].value == 42.0
        assert resp.results[0].provider == "copernicus_dem"

    def test_extract_unwraps_feature_geometry(self):
        respx = pytest.importorskip("respx")
        captured = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return httpx.Response(200, json=_EXTRACT_RESPONSE)

        with respx.mock(base_url="http://test") as mock:
            mock.post("/api/v1/extract").mock(side_effect=_capture)
            feature = {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
            }
            with CASClient("http://test") as cas:
                cas.extract(geometry=feature, dataset_ids=["p:v"])
        assert captured["geometry"] == {"type": "Point", "coordinates": [1.0, 2.0]}

    def test_providers_typed(self):
        respx = pytest.importorskip("respx")
        with respx.mock(base_url="http://test") as mock:
            mock.get("/api/v1/providers").mock(
                return_value=httpx.Response(200, json=_PROVIDERS_RESPONSE)
            )
            with CASClient("http://test") as cas:
                resp = cas.providers()
        assert resp.total == 1
        assert resp.providers[0].slug == "copernicus_dem"
        assert resp.providers[0].base_url.startswith("https://")

    def test_datasets_typed(self):
        respx = pytest.importorskip("respx")
        payload = {"total": 1, "limit": 100, "offset": 0, "count": 1, "datasets": [_DATASET]}
        with respx.mock(base_url="http://test") as mock:
            mock.get("/api/v1/datasets").mock(return_value=httpx.Response(200, json=payload))
            with CASClient("http://test") as cas:
                resp = cas.datasets(provider="copernicus_dem")
        assert resp.datasets[0].id == "copernicus_dem:elevation"
        assert resp.datasets[0].resolution_m == 30.0

    def test_error_envelope_raises_caserror(self):
        respx = pytest.importorskip("respx")
        envelope = {
            "error": {
                "type": "validation_error",
                "message": "dataset_ids is required",
                "request_id": "req-err",
                "detail": [{"loc": ["body", "dataset_ids"]}],
            }
        }
        with respx.mock(base_url="http://test") as mock:
            mock.post("/api/v1/extract").mock(
                return_value=httpx.Response(422, json=envelope)
            )
            with CASClient("http://test") as cas:  # noqa: SIM117
                with pytest.raises(CASError) as exc_info:
                    cas.extract(geometry={"type": "Point", "coordinates": [0, 0]}, dataset_ids=["p:v"])
        err = exc_info.value
        assert err.status_code == 422
        assert err.error_type == "validation_error"
        assert err.request_id == "req-err"

    def test_api_key_header_sent(self):
        respx = pytest.importorskip("respx")
        with respx.mock(base_url="http://test") as mock:
            route = mock.get("/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
            with CASClient("http://test", api_key="secret123") as cas:
                cas.health()
        assert route.calls.last.request.headers["X-API-Key"] == "secret123"


# ── Asynchronous client against the real ASGI app (no network) ───────


class TestAsyncClientIntegration:
    async def test_health_and_providers_in_process(self):
        pytest.importorskip("fastapi")
        from cas.api.app import create_app
        from cas.core.registry import discover, list_providers

        # ASGITransport does not run lifespan, so populate the registry directly.
        discover()
        app = create_app()
        transport = httpx.ASGITransport(app=app)

        async with AsyncCASClient("http://app", transport=transport) as cas:
            health = await cas.health()
            assert health["status"] == "ok"

            providers = await cas.providers(limit=5)
            assert providers.total == len(list_providers())
            assert providers.total > 0

            first = providers.providers[0].slug
            detail = await cas.provider(first)
            assert detail.slug == first
