# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Prometheus metrics for the CAS API."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUESTS = Counter(
    "cas_http_requests_total",
    "HTTP requests by method, path template, and status.",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "cas_http_request_duration_seconds",
    "HTTP request latency in seconds, by path template.",
    ["method", "path"],
)
PROVIDER_FAILURES = Counter(
    "cas_provider_failures_total",
    "Per-dataset extraction failures surfaced as warnings.",
    ["dataset"],
)
CACHE_HIT_RATE = Gauge(
    "cas_result_cache_hit_rate",
    "Result cache hit rate (0-1), sampled at scrape time.",
)
CACHE_SIZE = Gauge(
    "cas_result_cache_size",
    "Number of entries currently in the result cache.",
)


def render_latest() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics endpoint."""
    from cas.extract.engine import get_result_cache

    stats = get_result_cache().stats()
    CACHE_HIT_RATE.set(stats["hit_rate"])
    CACHE_SIZE.set(stats["size"])
    return generate_latest(), CONTENT_TYPE_LATEST
