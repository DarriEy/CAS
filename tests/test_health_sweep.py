# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the health-sweep concurrency model.

The sweep must not hammer a single upstream host with all of its providers
at once: providers that share one server (several national WCS layers can sit
behind one host) were dropping connections under concurrent load, producing
false-positive regression alerts. The per-host concurrency cap prevents that.

(The original CSIRO/ASRIS soil layers that motivated this test were
deregistered on 2026-06-30 when CSIRO decommissioned the ASRIS WCS — see
DarriEy/CAS#11 — so the mechanism is now exercised by the generic test below.)
"""

from __future__ import annotations

import asyncio

import cas.monitor.health as health


def test_provider_host_falls_back_to_slug_when_unknown():
    # Unknown slug can't resolve a base_url; must not collapse into a shared
    # bucket — falls back to the slug so it gets its own throttle slot.
    assert health._provider_host("does_not_exist") == "does_not_exist"


def test_per_host_concurrency_is_bounded(monkeypatch):
    """Same-host providers never exceed per_host_concurrency in flight."""
    same_host = [f"p{i}" for i in range(6)]
    monkeypatch.setattr(health, "discover", lambda: None)
    monkeypatch.setattr(health, "_provider_host", lambda slug: "shared.example")

    in_flight = 0
    peak = 0

    async def fake_check(slug):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

        class R:
            status = "healthy"
            response_time_ms = 1
        return R()

    monkeypatch.setattr(health, "check_provider", fake_check)

    asyncio.run(
        health.check_all_providers(slugs=same_host, per_host_concurrency=2)
    )
    assert peak <= 2, f"peak per-host concurrency was {peak}"
