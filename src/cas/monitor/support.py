# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Derive reproducible provider support tiers from health evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cas.core.registry import discover, get_connector, list_providers

TIER_DESCRIPTIONS = {
    "verified": "A real extraction passed in the committed health baseline.",
    "credentialed": "Extraction is implemented but requires provider credentials.",
    "mirror-backed": "Extraction uses a local curated mirror; CI does not provision it.",
    "degraded": "The latest committed extraction check did not return usable data.",
    "metadata-only": "Registered and discoverable, but absent from the committed extraction baseline.",
}


def build_support_report(baseline_path: str | Path) -> dict[str, Any]:
    """Build a support report by joining the registry to a health baseline."""
    path = Path(baseline_path)
    baseline = json.loads(path.read_text())
    observations = {item["provider"]: item for item in baseline["providers"]}

    discover()
    providers: list[dict[str, Any]] = []
    counts = {tier: 0 for tier in TIER_DESCRIPTIONS}
    for slug in list_providers():
        cls = get_connector(slug)
        observation = observations.get(slug)
        status = observation["status"] if observation else "unverified"
        if status == "healthy":
            tier = "verified"
        elif status == "auth_gated":
            tier = "credentialed"
        elif getattr(cls, "kind", None) == "mirror":
            tier = "mirror-backed"
        elif status in {"degraded", "down"}:
            tier = "degraded"
        else:
            tier = "metadata-only"
        counts[tier] += 1
        providers.append(
            {
                "provider": slug,
                "tier": tier,
                "health_status": status,
                "protocol": cls.protocol,
                "evidence": str(path),
            }
        )

    return {
        "generated_from": str(path),
        "observed_at": baseline.get("generated_at"),
        "definitions": TIER_DESCRIPTIONS,
        "summary": {**counts, "total": len(providers)},
        "providers": providers,
    }
