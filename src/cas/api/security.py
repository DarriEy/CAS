# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Optional, env-gated auth and rate limiting for the CAS API.

Both features are **off by default** and become no-ops unless enabled via
settings (``CAS_AUTH_ENABLED`` / ``CAS_RATE_LIMIT_ENABLED``). The rate limiter
is an in-memory fixed-window counter — per-process, no external infra, matching
CAS's "no stored state" stance. For multi-replica deployments, front it with a
shared limiter (e.g. an ingress/gateway).
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request

from cas.core.config import get_settings


class AuthError(Exception):
    """Caller did not present a valid API key."""


class RateLimitExceededError(Exception):
    """Caller exceeded the configured request rate."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded; retry after {retry_after}s")


API_KEY_HEADER = "X-API-Key"
_WINDOW_S = 60.0


async def require_api_key(request: Request) -> None:
    """FastAPI dependency: enforce ``X-API-Key`` when auth is enabled."""
    settings = get_settings()
    if not settings.auth_enabled:
        return
    key = request.headers.get(API_KEY_HEADER)
    if not key or key not in settings.api_keys:
        raise AuthError("Missing or invalid API key")


class FixedWindowRateLimiter:
    """In-memory per-caller fixed-window rate limiter."""

    def __init__(self) -> None:
        # caller -> (window_start_monotonic, count)
        self._windows: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))

    def check(self, caller: str, limit: int, now: float) -> None:
        start, count = self._windows[caller]
        if now - start >= _WINDOW_S:
            self._windows[caller] = (now, 1)
            return
        if count >= limit:
            raise RateLimitExceededError(retry_after=max(1, int(_WINDOW_S - (now - start))))
        self._windows[caller] = (start, count + 1)

    def reset(self) -> None:
        self._windows.clear()


_limiter = FixedWindowRateLimiter()


def get_rate_limiter() -> FixedWindowRateLimiter:
    return _limiter


def _caller_id(request: Request) -> str:
    key = request.headers.get(API_KEY_HEADER)
    if key:
        return f"key:{key}"
    client = request.client
    return f"ip:{client.host}" if client else "ip:unknown"


async def rate_limit(request: Request) -> None:
    """FastAPI dependency: enforce per-caller rate limit when enabled."""
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    _limiter.check(
        caller=_caller_id(request),
        limit=settings.rate_limit_per_minute,
        now=time.monotonic(),
    )
