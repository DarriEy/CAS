# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Health snapshot serialization and regression detection.

A *snapshot* is a machine-readable record of one ``cas health`` run. The
committed ``health/baseline.json`` is itself a snapshot — the expected
status per provider — so the honest-degraded set (e.g. jrc_gsw) is baked
in as ``degraded`` and never alerts. A *regression* is any provider whose
current status is strictly worse than its baseline status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cas.core.models import HealthCheckResult

# Higher is better. A regression is current_rank < baseline_rank.
# auth_gated (creds unset) and unknown both mean "couldn't verify", ranked below
# healthy so an unexpected healthy→auth_gated/unknown drop surfaces, while the
# known auth-gated providers stay quiet (auth_gated in baseline → auth_gated now).
STATUS_RANK = {"down": 0, "degraded": 1, "unknown": 2, "auth_gated": 2, "healthy": 3}


def snapshot_dict(results: list[HealthCheckResult], generated_at: datetime | None = None) -> dict:
    """Serialize health results to a stable, diff-friendly snapshot dict."""
    counts = {"healthy": 0, "degraded": 0, "auth_gated": 0, "down": 0, "unknown": 0}
    providers = []
    for r in sorted(results, key=lambda r: r.provider):
        status = str(r.status)
        counts[status] = counts.get(status, 0) + 1
        providers.append(
            {
                "provider": r.provider,
                "status": status,
                "response_time_ms": r.response_time_ms,
                "datasets_available": r.datasets_available,
                "test_value": r.test_value,
                "error": r.error,
            }
        )
    return {
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "summary": {**counts, "total": len(providers)},
        "providers": providers,
    }


def _status_map(snapshot: dict) -> dict[str, str]:
    return {p["provider"]: p["status"] for p in snapshot.get("providers", [])}


@dataclass
class StatusChange:
    provider: str
    baseline: str
    current: str

    def __str__(self) -> str:
        return f"{self.provider}: {self.baseline} → {self.current}"


@dataclass
class Comparison:
    regressions: list[StatusChange]   # worse than baseline — the alert set
    improvements: list[StatusChange]  # better than baseline — refresh hint
    new_down: list[StatusChange]      # provider absent from baseline, now degraded/down
    removed: list[str]                # in baseline but absent from current run

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)


def compare(baseline: dict, current: dict) -> Comparison:
    """Compare a current snapshot against a baseline snapshot."""
    base = _status_map(baseline)
    cur = _status_map(current)

    regressions: list[StatusChange] = []
    improvements: list[StatusChange] = []
    new_down: list[StatusChange] = []

    for provider, cur_status in sorted(cur.items()):
        base_status = base.get(provider)
        if base_status is None:
            # Provider not in baseline. Only flag if it is arriving broken.
            if STATUS_RANK.get(cur_status, 3) < STATUS_RANK["healthy"]:
                new_down.append(StatusChange(provider, "absent", cur_status))
            continue
        cur_rank = STATUS_RANK.get(cur_status, 3)
        base_rank = STATUS_RANK.get(base_status, 3)
        if cur_rank < base_rank:
            regressions.append(StatusChange(provider, base_status, cur_status))
        elif cur_rank > base_rank:
            improvements.append(StatusChange(provider, base_status, cur_status))

    removed = sorted(set(base) - set(cur))
    return Comparison(regressions, improvements, new_down, removed)


def format_report(cmp: Comparison) -> str:
    """Render a comparison as a human-readable report."""
    lines: list[str] = []
    if cmp.regressions:
        lines.append(f"Regressions ({len(cmp.regressions)}) — worse than baseline:")
        lines += [f"  ✗ {c}" for c in cmp.regressions]
    if cmp.new_down:
        lines.append(f"New broken providers not in baseline ({len(cmp.new_down)}):")
        lines += [f"  ? {c}" for c in cmp.new_down]
    if cmp.improvements:
        lines.append(f"Improvements ({len(cmp.improvements)}) — consider refreshing baseline:")
        lines += [f"  ✓ {c}" for c in cmp.improvements]
    if cmp.removed:
        lines.append(f"In baseline but not checked this run ({len(cmp.removed)}): {', '.join(cmp.removed)}")
    if not lines:
        lines.append("No status changes vs baseline.")
    return "\n".join(lines)
