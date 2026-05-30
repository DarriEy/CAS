# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Abstract base class for all CAS data provider connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from cas.core.exceptions import ConnectorError, RateLimitError
from cas.core.models import AttributeResult, Dataset, Geometry, TimeRange

logger = structlog.get_logger()


class BaseConnector(ABC):
    """Interface that every provider connector must implement.

    Subclasses handle one data provider: URL construction, response
    parsing, unit conversion, and rate-limit handling.
    """

    slug: str
    display_name: str
    base_url: str
    protocol: str

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BaseConnector:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=15.0),
            headers={"User-Agent": "CAS/0.1 (Community Attribute Service)"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise ConnectorError(self.slug, "Connector used outside async context manager")
        return self._client

    # ── Abstract methods ────────────────────────────────────────────

    @abstractmethod
    async def list_datasets(self) -> list[Dataset]:
        """Return the catalog of datasets available from this provider."""

    @abstractmethod
    async def extract(
        self,
        dataset_id: str,
        geometry: Geometry,
        time_range: TimeRange | None = None,
    ) -> AttributeResult:
        """Extract attribute value(s) for a geometry from a single dataset."""

    # ── Optional overrides ──────────────────────────────────────────

    async def check_health(self) -> bool:
        try:
            resp = await self.client.get("/", timeout=10.0)
            return resp.status_code < 500
        except Exception:
            return False

    # ── Planetary Computer signing ───────────────────────────────────

    @staticmethod
    def _sign_planetary_computer(href: str) -> str:
        """Sign a Planetary Computer URL. Raises ConnectorError if the package is missing."""
        try:
            import planetary_computer

            return planetary_computer.sign(href)  # type: ignore[no-any-return]
        except ImportError:
            raise ConnectorError(
                "planetary_computer",
                "The 'planetary-computer' package is required for this provider.\n"
                "Install it with: pip install 'cas[stac]'",
            ) from None

    # ── Built-in HTTP helpers ───────────────────────────────────────

    _RETRYABLE = (
        RateLimitError,
        httpx.RemoteProtocolError,
        httpx.ConnectError,
        httpx.ReadTimeout,
    )

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
    )
    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        resp = await self.client.get(path, params=params)
        if resp.status_code == 429:
            raise RateLimitError(self.slug, "Rate limited")
        if resp.status_code not in (200, 206):
            resp.raise_for_status()
        return resp

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
    )
    async def _get_bytes(self, url: str, params: dict | None = None) -> bytes:
        """GET that returns raw bytes. Supports absolute URLs for raster data."""
        if url.startswith("http"):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=15.0),
                follow_redirects=True,
            ) as tmp:
                resp = await tmp.get(url, params=params)
        else:
            resp = await self.client.get(url, params=params)
        if resp.status_code == 429:
            raise RateLimitError(self.slug, "Rate limited")
        if resp.status_code not in (200, 206):
            resp.raise_for_status()
        return resp.content
