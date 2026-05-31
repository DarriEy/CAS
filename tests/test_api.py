"""Tests for the FastAPI service layer: hardening, pagination, observability."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="API tests require the [api] extra")

from fastapi.testclient import TestClient  # noqa: E402

from cas.api.app import create_app  # noqa: E402
from cas.api.security import get_rate_limiter  # noqa: E402
from cas.core.config import get_settings  # noqa: E402
from cas.core.models import (  # noqa: E402
    AggregationMethod,
    AttributeResult,
    BoundingBox,
    Dataset,
    DataType,
    Protocol,
    QualityFlag,
    TemporalExtent,
    Variable,
)

VALID_BODY = {
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [-96.6, 39.0], [-96.5, 39.0],
            [-96.5, 39.1], [-96.6, 39.1], [-96.6, 39.0],
        ]],
    },
    "dataset_ids": ["test_provider:test_var"],
    "aggregation": "mean",
}


@pytest.fixture(autouse=True)
def _reset_settings_and_limiter():
    """Settings are lru_cached and the rate limiter is process-global."""
    get_settings.cache_clear()
    get_rate_limiter().reset()
    yield
    get_settings.cache_clear()
    get_rate_limiter().reset()


def _mock_connector():
    result = AttributeResult(
        dataset_id="test_provider:test_var",
        variable="test_var",
        value=42.0,
        units="m",
        aggregation=AggregationMethod.MEAN,
        quality=QualityFlag.GOOD,
        coverage_fraction=0.95,
        pixel_count=100,
        provider="test_provider",
        elapsed_ms=50,
    )
    inst = AsyncMock()
    inst.extract = AsyncMock(return_value=result)
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=inst)


def _mock_engine():
    """Patch the engine's discover + get_connector with a stub connector."""
    return (
        patch("cas.extract.engine.discover"),
        patch("cas.extract.engine.get_connector", return_value=_mock_connector()),
    )


class TestExtractEndpoint:
    def test_happy_path_returns_results_and_request_id(self):
        d, g = _mock_engine()
        with d, g, TestClient(create_app()) as client:
            resp = client.post("/api/v1/extract", json=VALID_BODY)
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"][0]["value"] == 42.0
        assert resp.headers["X-Request-ID"]

    def test_dataset_limit_returns_422_envelope(self, monkeypatch):
        monkeypatch.setenv("CAS_MAX_DATASETS_PER_REQUEST", "2")
        get_settings.cache_clear()
        body = {**VALID_BODY, "dataset_ids": ["p:a", "p:b", "p:c"]}
        d, g = _mock_engine()
        with d, g, TestClient(create_app()) as client:
            resp = client.post("/api/v1/extract", json=body)
        assert resp.status_code == 422
        err = resp.json()["error"]
        assert err["type"] == "request_limit"
        assert err["request_id"]
        assert "Too many datasets" in err["message"]

    def test_pydantic_validation_uses_error_envelope(self):
        """A schema-invalid body returns the CAS envelope, not FastAPI's {detail}."""
        with TestClient(create_app()) as client:
            resp = client.post("/api/v1/extract", json={"geometry": VALID_BODY["geometry"]})
        assert resp.status_code == 422
        body = resp.json()
        assert "error" in body and "detail" not in body
        err = body["error"]
        assert err["type"] == "validation_error"
        assert err["request_id"]
        # Structured per-field errors are preserved under "detail".
        assert any("dataset_ids" in str(item.get("loc", "")) for item in err["detail"])
        assert resp.headers["X-Request-ID"]


class TestAuth:
    def test_missing_key_401_when_enabled(self, monkeypatch):
        monkeypatch.setenv("CAS_AUTH_ENABLED", "true")
        monkeypatch.setenv("CAS_API_KEYS", "secret123")
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            resp = client.post("/api/v1/extract", json=VALID_BODY)
        assert resp.status_code == 401
        assert resp.json()["error"]["type"] == "unauthorized"

    def test_valid_key_allows_request(self, monkeypatch):
        monkeypatch.setenv("CAS_AUTH_ENABLED", "true")
        monkeypatch.setenv("CAS_API_KEYS", "secret123")
        get_settings.cache_clear()
        d, g = _mock_engine()
        with d, g, TestClient(create_app()) as client:
            resp = client.post(
                "/api/v1/extract",
                json=VALID_BODY,
                headers={"X-API-Key": "secret123"},
            )
        assert resp.status_code == 200


class TestRateLimit:
    def test_third_call_429_when_limit_two(self, monkeypatch):
        monkeypatch.setenv("CAS_RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("CAS_RATE_LIMIT_PER_MINUTE", "2")
        get_settings.cache_clear()
        get_rate_limiter().reset()
        with TestClient(create_app()) as client:
            r1 = client.get("/api/v1/providers")
            r2 = client.get("/api/v1/providers")
            r3 = client.get("/api/v1/providers")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429
        assert r3.json()["error"]["type"] == "rate_limited"
        assert r3.headers["Retry-After"]


class TestCatalog:
    def test_datasets_pagination(self):
        def _ds(i: int) -> Dataset:
            return Dataset(
                id=f"p:ds{i}",
                provider="p",
                name=f"ds{i}",
                variables=[Variable(name="v", units="m", data_type=DataType.CONTINUOUS)],
                resolution_m=250,
                bbox=BoundingBox(min_lon=-1, min_lat=-1, max_lon=1, max_lat=1),
                temporal=TemporalExtent(),
                protocol=Protocol.WCS,
            )

        async def fake_datasets_for(slug):
            return [_ds(i) for i in range(5)]

        with (
            patch("cas.api.app.list_providers", return_value=["p"]),
            patch("cas.api.app._datasets_for", side_effect=fake_datasets_for),
            TestClient(create_app()) as client,
        ):
            resp = client.get("/api/v1/datasets?limit=2&offset=1")
        body = resp.json()
        assert body["total"] == 5
        assert body["count"] == 2
        assert body["offset"] == 1
        assert body["datasets"][0]["id"] == "p:ds1"


class TestOps:
    def test_health(self):
        with TestClient(create_app()) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "cache" in body

    def test_metrics_exposition(self):
        with TestClient(create_app()) as client:
            client.get("/health")
            resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "cas_http_requests_total" in resp.text
