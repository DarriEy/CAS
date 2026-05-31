# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Python client SDK for the CAS HTTP API.

A thin, typed wrapper over the CAS REST API. Both a synchronous
(:class:`CASClient`) and an asynchronous (:class:`AsyncCASClient`) client are
provided; they share request-building and response-parsing logic and return the
same canonical :mod:`cas.core.models` types as the service itself.

Example
-------
>>> from cas.client import CASClient
>>> with CASClient("http://localhost:8000") as cas:
...     providers = cas.providers()
...     resp = cas.extract(
...         geometry={"type": "Point", "coordinates": [-96.5, 39.0]},
...         dataset_ids=["copernicus_dem:elevation"],
...     )
...     print(resp.results[0].value)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from cas.core.models import (
    AggregationMethod,
    AttributeRequest,
    AttributeResponse,
    BatchAttributeRequest,
    BatchAttributeResponse,
    DatasetListResponse,
    Geometry,
    ProviderDetail,
    ProviderListResponse,
    TimeRange,
)

__all__ = [
    "CASClient",
    "AsyncCASClient",
    "CASError",
    "DEFAULT_BASE_URL",
]

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 120.0

GeometryLike = Geometry | dict[str, Any]
TimeRangeLike = TimeRange | tuple[datetime | str, datetime | str]


class CASError(Exception):
    """Raised when the CAS API returns an error response.

    Mirrors the service error envelope::

        {"error": {"type": "...", "message": "...", "request_id": "...", "detail": ...}}
    """

    def __init__(
        self,
        status_code: int,
        error_type: str,
        message: str,
        request_id: str | None = None,
        detail: Any = None,
    ) -> None:
        self.status_code = status_code
        self.error_type = error_type
        self.message = message
        self.request_id = request_id
        self.detail = detail
        suffix = f" (request_id={request_id})" if request_id else ""
        super().__init__(f"[{status_code} {error_type}] {message}{suffix}")


# ── Shared request/response helpers ─────────────────────────────────


def _coerce_geometry(geometry: GeometryLike) -> Geometry:
    """Accept a Geometry, a GeoJSON geometry dict, or a GeoJSON Feature dict."""
    if isinstance(geometry, Geometry):
        return geometry
    if isinstance(geometry, dict):
        # Unwrap a Feature → its geometry.
        geo_dict = geometry
        if geo_dict.get("type") == "Feature" and "geometry" in geo_dict:
            geo_dict = geo_dict["geometry"]
        return Geometry(**geo_dict)
    raise TypeError(f"Unsupported geometry type: {type(geometry)!r}")


def _coerce_time_range(time_range: TimeRangeLike | None) -> TimeRange | None:
    if time_range is None or isinstance(time_range, TimeRange):
        return time_range
    start, end = time_range

    def _dt(v: datetime | str) -> datetime:
        return v if isinstance(v, datetime) else datetime.fromisoformat(v)

    return TimeRange(start=_dt(start), end=_dt(end))


def _build_extract_request(
    geometry: GeometryLike,
    dataset_ids: list[str],
    aggregation: AggregationMethod | str,
    time_range: TimeRangeLike | None,
    target_crs: str,
) -> AttributeRequest:
    return AttributeRequest(
        geometry=_coerce_geometry(geometry),
        dataset_ids=list(dataset_ids),
        aggregation=AggregationMethod(aggregation),
        time_range=_coerce_time_range(time_range),
        target_crs=target_crs,
    )


def _build_batch_request(
    geometries: list[GeometryLike],
    dataset_ids: list[str],
    aggregation: AggregationMethod | str,
    time_range: TimeRangeLike | None,
    target_crs: str,
) -> BatchAttributeRequest:
    return BatchAttributeRequest(
        geometries=[_coerce_geometry(g) for g in geometries],
        dataset_ids=list(dataset_ids),
        aggregation=AggregationMethod(aggregation),
        time_range=_coerce_time_range(time_range),
        target_crs=target_crs,
    )


def _error_from_response(resp: httpx.Response) -> CASError:
    """Turn a >=400 response into a CASError, parsing the CAS envelope when present."""
    env: dict[str, Any] = {}
    try:
        body = resp.json()
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            env = body["error"]
    except Exception:
        pass
    return CASError(
        status_code=resp.status_code,
        error_type=env.get("type", "http_error"),
        message=env.get("message") or resp.text or resp.reason_phrase,
        request_id=env.get("request_id"),
        detail=env.get("detail"),
    )


def _json_body(request: AttributeRequest | BatchAttributeRequest) -> dict[str, Any]:
    return request.model_dump(mode="json", exclude_none=True)


# ── Synchronous client ───────────────────────────────────────────────


class CASClient:
    """Synchronous client for the CAS HTTP API.

    Parameters
    ----------
    base_url:
        Root URL of the CAS service (no trailing ``/api`` needed).
    api_key:
        Optional API key sent as ``X-API-Key`` when the service has auth enabled.
    timeout:
        Per-request timeout in seconds.
    transport:
        Optional ``httpx`` transport — useful for tests (e.g. ``ASGITransport``).
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
            transport=transport,
            follow_redirects=True,
        )

    def __enter__(self) -> CASClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise _error_from_response(resp)
        return resp.json()

    # ── Extraction ──────────────────────────────────────────────────

    def extract(
        self,
        geometry: GeometryLike,
        dataset_ids: list[str],
        *,
        aggregation: AggregationMethod | str = AggregationMethod.MEAN,
        time_range: TimeRangeLike | None = None,
        target_crs: str = "EPSG:4326",
    ) -> AttributeResponse:
        """Extract attribute values for a single geometry."""
        req = _build_extract_request(geometry, dataset_ids, aggregation, time_range, target_crs)
        data = self._request("POST", "/api/v1/extract", json=_json_body(req))
        return AttributeResponse.model_validate(data)

    def batch_extract(
        self,
        geometries: list[GeometryLike],
        dataset_ids: list[str],
        *,
        aggregation: AggregationMethod | str = AggregationMethod.MEAN,
        time_range: TimeRangeLike | None = None,
        target_crs: str = "EPSG:4326",
    ) -> BatchAttributeResponse:
        """Extract attribute values for many geometries in one call."""
        req = _build_batch_request(geometries, dataset_ids, aggregation, time_range, target_crs)
        data = self._request("POST", "/api/v1/extract/batch", json=_json_body(req))
        return BatchAttributeResponse.model_validate(data)

    # ── Catalog ─────────────────────────────────────────────────────

    def providers(self, *, limit: int = 100, offset: int = 0) -> ProviderListResponse:
        """List registered providers (paginated)."""
        data = self._request("GET", "/api/v1/providers", params={"limit": limit, "offset": offset})
        return ProviderListResponse.model_validate(data)

    def provider(self, slug: str) -> ProviderDetail:
        """Return one provider with its full dataset metadata."""
        data = self._request("GET", f"/api/v1/providers/{slug}")
        return ProviderDetail.model_validate(data)

    def datasets(
        self, *, provider: str | None = None, limit: int = 100, offset: int = 0
    ) -> DatasetListResponse:
        """List available datasets (paginated, optionally filtered by provider)."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if provider:
            params["provider"] = provider
        data = self._request("GET", "/api/v1/datasets", params=params)
        return DatasetListResponse.model_validate(data)

    def iter_providers(self, *, page_size: int = 200) -> list[ProviderDetail]:
        """Convenience: fetch every provider summary, then its detail.

        Note: this issues one request per provider, so it is intended for
        catalog tooling rather than hot paths.
        """
        summary = self.providers(limit=page_size, offset=0)
        details: list[ProviderDetail] = []
        offset = 0
        while offset < summary.total:
            page = self.providers(limit=page_size, offset=offset)
            for p in page.providers:
                details.append(self.provider(p.slug))
            offset += len(page.providers) or page_size
        return details

    # ── Ops ─────────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        """Liveness check + result-cache stats."""
        result: dict[str, Any] = self._request("GET", "/health")
        return result


# ── Asynchronous client ──────────────────────────────────────────────


class AsyncCASClient:
    """Asynchronous client for the CAS HTTP API (mirror of :class:`CASClient`)."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
            transport=transport,
            follow_redirects=True,
        )

    async def __aenter__(self) -> AsyncCASClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise _error_from_response(resp)
        return resp.json()

    async def extract(
        self,
        geometry: GeometryLike,
        dataset_ids: list[str],
        *,
        aggregation: AggregationMethod | str = AggregationMethod.MEAN,
        time_range: TimeRangeLike | None = None,
        target_crs: str = "EPSG:4326",
    ) -> AttributeResponse:
        req = _build_extract_request(geometry, dataset_ids, aggregation, time_range, target_crs)
        data = await self._request("POST", "/api/v1/extract", json=_json_body(req))
        return AttributeResponse.model_validate(data)

    async def batch_extract(
        self,
        geometries: list[GeometryLike],
        dataset_ids: list[str],
        *,
        aggregation: AggregationMethod | str = AggregationMethod.MEAN,
        time_range: TimeRangeLike | None = None,
        target_crs: str = "EPSG:4326",
    ) -> BatchAttributeResponse:
        req = _build_batch_request(geometries, dataset_ids, aggregation, time_range, target_crs)
        data = await self._request("POST", "/api/v1/extract/batch", json=_json_body(req))
        return BatchAttributeResponse.model_validate(data)

    async def providers(self, *, limit: int = 100, offset: int = 0) -> ProviderListResponse:
        data = await self._request(
            "GET", "/api/v1/providers", params={"limit": limit, "offset": offset}
        )
        return ProviderListResponse.model_validate(data)

    async def provider(self, slug: str) -> ProviderDetail:
        data = await self._request("GET", f"/api/v1/providers/{slug}")
        return ProviderDetail.model_validate(data)

    async def datasets(
        self, *, provider: str | None = None, limit: int = 100, offset: int = 0
    ) -> DatasetListResponse:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if provider:
            params["provider"] = provider
        data = await self._request("GET", "/api/v1/datasets", params=params)
        return DatasetListResponse.model_validate(data)

    async def health(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._request("GET", "/health")
        return result
