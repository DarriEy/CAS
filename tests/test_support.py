from __future__ import annotations

from cas.monitor.support import build_support_report


def test_support_report_covers_live_registry():
    report = build_support_report("health/baseline.json")
    assert report["summary"]["total"] == len(report["providers"])
    assert sum(report["summary"][tier] for tier in report["definitions"]) == report["summary"]["total"]
    assert {item["tier"] for item in report["providers"]} <= set(report["definitions"])


def test_support_report_exposes_expected_evidence_classes():
    report = build_support_report("health/baseline.json")
    tiers = {item["provider"]: item["tier"] for item in report["providers"]}
    assert tiers["copernicus_dem"] == "verified"
    assert tiers["aster_gdem"] == "credentialed"
    assert tiers["glhymps"] == "mirror-backed"
    assert tiers["france_lithology"] == "metadata-only"
