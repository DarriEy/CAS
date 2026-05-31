# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for health snapshot serialization and regression detection."""

from __future__ import annotations

from datetime import UTC, datetime

from cas.core.models import HealthCheckResult, ProviderStatus
from cas.monitor.trend import compare, snapshot_dict


def _snap(statuses: dict[str, str]) -> dict:
    return {"providers": [{"provider": p, "status": s} for p, s in statuses.items()]}


def test_snapshot_dict_counts_and_shape():
    now = datetime(2026, 5, 31, tzinfo=UTC)
    results = [
        HealthCheckResult(provider="b", status=ProviderStatus.DEGRADED, last_checked=now),
        HealthCheckResult(provider="a", status=ProviderStatus.HEALTHY, last_checked=now,
                          datasets_available=2, test_value=1.5),
    ]
    snap = snapshot_dict(results, generated_at=now)
    assert snap["summary"] == {"healthy": 1, "degraded": 1, "unknown": 0, "down": 0, "total": 2}
    # providers sorted by name for stable diffs
    assert [p["provider"] for p in snap["providers"]] == ["a", "b"]
    assert snap["providers"][0]["test_value"] == 1.5
    assert snap["generated_at"] == "2026-05-31T00:00:00+00:00"


def test_regression_detected_when_status_worsens():
    base = _snap({"x": "healthy", "y": "degraded"})
    cur = _snap({"x": "degraded", "y": "down"})
    cmp = compare(base, cur)
    assert cmp.has_regressions
    assert {c.provider for c in cmp.regressions} == {"x", "y"}


def test_honest_degraded_does_not_alert():
    # A provider that is degraded in baseline and still degraded now is fine.
    cmp = compare(_snap({"jrc_gsw": "degraded"}), _snap({"jrc_gsw": "degraded"}))
    assert not cmp.has_regressions
    assert not cmp.improvements


def test_auth_gated_stays_quiet():
    cmp = compare(_snap({"finland_dem": "unknown"}), _snap({"finland_dem": "unknown"}))
    assert not cmp.has_regressions


def test_improvement_reported_not_regression():
    cmp = compare(_snap({"x": "degraded"}), _snap({"x": "healthy"}))
    assert not cmp.has_regressions
    assert [c.provider for c in cmp.improvements] == ["x"]


def test_new_broken_provider_flagged_but_healthy_new_is_silent():
    cmp = compare(_snap({}), _snap({"new_down": "down", "new_ok": "healthy"}))
    assert not cmp.has_regressions  # new providers are not regressions
    assert [c.provider for c in cmp.new_down] == ["new_down"]


def test_already_down_in_baseline_is_not_a_regression():
    cmp = compare(_snap({"dead": "down"}), _snap({"dead": "down"}))
    assert not cmp.has_regressions


def test_removed_provider_listed():
    cmp = compare(_snap({"gone": "healthy"}), _snap({}))
    assert cmp.removed == ["gone"]
    assert not cmp.has_regressions
