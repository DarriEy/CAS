# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Network tests that verify all provider endpoints are reachable.

Marked with ``@pytest.mark.network`` — skipped in fast CI runs.
Run with: ``pytest tests/test_provider_endpoints.py -v``
"""

from __future__ import annotations

import asyncio

import pytest

from cas.monitor.reachability import check_all_reachability

MAX_ALLOWED_FAILURES = 5


@pytest.mark.network
def test_all_endpoints_reachable():
    """Every registered provider's base_url must be reachable.

    Allows up to MAX_ALLOWED_FAILURES transient timeouts since probing
    227+ endpoints in parallel inevitably hits slow servers.
    Retries timeouts once before counting them as failures.
    """
    results = asyncio.run(check_all_reachability())

    failures = [r for r in results if not r.reachable]

    if failures and all(r.detail == "timeout" for r in failures):
        retry_slugs = [r.slug for r in failures]
        retry_results = asyncio.run(check_all_reachability(slugs=retry_slugs))
        still_failing = [r for r in retry_results if not r.reachable]
        failures = still_failing

    if len(failures) > MAX_ALLOWED_FAILURES:
        lines = [f"\n{len(failures)} provider endpoint(s) unreachable (threshold {MAX_ALLOWED_FAILURES}):\n"]
        for r in sorted(failures, key=lambda r: r.slug):
            lines.append(f"  {r.slug:30s}  {r.url}")
            lines.append(f"  {'':30s}  {r.detail}\n")
        pytest.fail("\n".join(lines))
    elif failures:
        for r in failures:
            print(f"  [WARN] {r.slug}: {r.detail} (within tolerance)")
