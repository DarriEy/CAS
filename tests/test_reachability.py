# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the endpoint-reachability sweep, esp. the serial re-probe."""

from __future__ import annotations

import asyncio

from cas.monitor import reachability as rb
from cas.monitor.reachability import ReachabilityResult, check_all_reachability


class _FakeConn:
    base_url = "https://example.test/wcs"
    protocol = "wcs"


def _patch_single_provider(monkeypatch):
    monkeypatch.setattr(rb, "discover", lambda: None)
    monkeypatch.setattr(rb, "list_providers", lambda: ["x"])
    monkeypatch.setattr(rb, "get_connector", lambda slug: _FakeConn)


def _stateful_check(reachable_after: int):
    """Fake check_reachability: unreachable until call number > reachable_after."""
    calls = {"n": 0}

    async def fake(slug, url, protocol, client, semaphore):
        calls["n"] += 1
        ok = calls["n"] > reachable_after
        return ReachabilityResult(
            slug=slug, url=url, status_code=200 if ok else None,
            reachable=ok, elapsed_s=0.1,
            detail="HTTP 200" if ok else "timeout",
        )

    return fake, calls


def test_serial_recheck_recovers_load_timeout(monkeypatch):
    # Parallel pass fails (call 1), serial re-probe succeeds (call 2).
    _patch_single_provider(monkeypatch)
    fake, calls = _stateful_check(reachable_after=1)
    monkeypatch.setattr(rb, "check_reachability", fake)

    results = asyncio.run(check_all_reachability())
    assert [r.reachable for r in results] == [True]
    assert calls["n"] == 2  # one parallel + one serial recheck


def test_recheck_can_be_disabled(monkeypatch):
    _patch_single_provider(monkeypatch)
    fake, calls = _stateful_check(reachable_after=1)
    monkeypatch.setattr(rb, "check_reachability", fake)

    results = asyncio.run(check_all_reachability(recheck_failures=False))
    assert [r.reachable for r in results] == [False]
    assert calls["n"] == 1  # no serial re-probe


def test_genuine_failure_stays_failed(monkeypatch):
    # Down on both the parallel pass and the serial re-probe.
    _patch_single_provider(monkeypatch)
    fake, calls = _stateful_check(reachable_after=99)
    monkeypatch.setattr(rb, "check_reachability", fake)

    results = asyncio.run(check_all_reachability())
    assert [r.reachable for r in results] == [False]
    assert calls["n"] == 2  # tried twice, still down
