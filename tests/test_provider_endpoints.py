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


@pytest.mark.network
def test_all_endpoints_reachable():
    """Every registered provider's base_url must be reachable."""
    results = asyncio.run(check_all_reachability())

    failures = [r for r in results if not r.reachable]

    if failures:
        lines = [f"\n{len(failures)} provider endpoint(s) unreachable:\n"]
        for r in sorted(failures, key=lambda r: r.slug):
            lines.append(f"  {r.slug:30s}  {r.url}")
            lines.append(f"  {'':30s}  {r.detail}\n")
        pytest.fail("\n".join(lines))
