# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests for the endpoint-reachability sweep, esp. the serial re-probe."""

from __future__ import annotations

import asyncio

import pytest

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


def test_reachability_verifies_tls_by_default(monkeypatch):
    _patch_single_provider(monkeypatch)
    fake, _ = _stateful_check(reachable_after=0)
    monkeypatch.setattr(rb, "check_reachability", fake)
    clients = []

    class FakeClient:
        def __init__(self, **kwargs):
            clients.append(kwargs)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(rb.httpx, "AsyncClient", FakeClient)
    asyncio.run(check_all_reachability())
    assert clients[0]["verify"] is True


def test_reachability_allows_explicit_scoped_tls_override(monkeypatch):
    _patch_single_provider(monkeypatch)
    fake, _ = _stateful_check(reachable_after=0)
    monkeypatch.setattr(rb, "check_reachability", fake)
    clients = []

    class FakeClient:
        def __init__(self, **kwargs):
            clients.append(kwargs)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(rb.httpx, "AsyncClient", FakeClient)
    asyncio.run(check_all_reachability(tls_verify=False, allow_insecure_tls=True))
    assert clients[0]["verify"] is False


def test_reachability_rejects_unacknowledged_insecure_tls(monkeypatch):
    _patch_single_provider(monkeypatch)
    with pytest.raises(ValueError, match="allow_insecure_tls=True"):
        asyncio.run(check_all_reachability(tls_verify=False))
