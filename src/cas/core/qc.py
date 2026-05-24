# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Quality control validation for attribute extraction results."""

from __future__ import annotations

import structlog

from cas.core.models import AttributeResult, QualityFlag

logger = structlog.get_logger()

VALID_RANGES: dict[str, tuple[float, float]] = {
    "elevation": (-500.0, 9000.0),
    "dem": (-500.0, 9000.0),
    "slope": (0.0, 90.0),
    "aspect": (0.0, 360.0),
    "clay": (0.0, 1000.0),
    "sand": (0.0, 1000.0),
    "silt": (0.0, 1000.0),
    "soc": (0.0, 600.0),
    "phh2o": (2.0, 12.0),
    "bdod": (0.0, 2500.0),
    "cec": (0.0, 500.0),
    "nitrogen": (0.0, 50.0),
    "ndvi": (-1.0, 1.0),
    "lai": (0.0, 10.0),
    "temperature": (-100.0, 70.0),
    "precipitation": (0.0, 1000.0),
    "land_cover": (0.0, 255.0),
}

MIN_COVERAGE_GOOD = 0.8
MIN_COVERAGE_ACCEPTABLE = 0.3


def validate_result(result: AttributeResult) -> list[str]:
    """Validate an AttributeResult against known ranges and coverage thresholds.

    Modifies result.quality in place if issues are found.
    Returns a list of warning messages.
    """
    warnings: list[str] = []

    if result.coverage_fraction < MIN_COVERAGE_ACCEPTABLE:
        result.quality = QualityFlag.MISSING
        warnings.append(
            f"{result.dataset_id}: coverage {result.coverage_fraction:.0%} "
            f"below minimum threshold ({MIN_COVERAGE_ACCEPTABLE:.0%})"
        )
        return warnings

    if result.coverage_fraction < MIN_COVERAGE_GOOD:
        if result.quality == QualityFlag.GOOD:
            result.quality = QualityFlag.PARTIAL
        warnings.append(
            f"{result.dataset_id}: coverage {result.coverage_fraction:.0%} "
            f"below ideal threshold ({MIN_COVERAGE_GOOD:.0%})"
        )

    if isinstance(result.value, int | float):
        _check_range(result, warnings)

    return warnings


def _check_range(result: AttributeResult, warnings: list[str]) -> None:
    variable_lower = result.variable.lower()
    for var_prefix, (vmin, vmax) in VALID_RANGES.items():
        if variable_lower.startswith(var_prefix):
            if result.value < vmin or result.value > vmax:  # type: ignore[operator]
                result.quality = QualityFlag.SUSPECT
                warnings.append(
                    f"{result.dataset_id}: value {result.value} outside "
                    f"expected range [{vmin}, {vmax}] for '{result.variable}'"
                )
            break


def check_cross_provider_consistency(
    results: list[AttributeResult],
    variable: str,
    max_relative_diff: float = 0.3,
) -> list[str]:
    """Flag when multiple providers return divergent values for the same variable."""
    warnings: list[str] = []
    matching = [
        r
        for r in results
        if r.variable.lower() == variable.lower() and isinstance(r.value, int | float) and r.value is not None
    ]

    if len(matching) < 2:
        return warnings

    values = [r.value for r in matching]
    mean_val = sum(values) / len(values)  # type: ignore[arg-type]
    if mean_val == 0:
        return warnings

    for r in matching:
        rel_diff = abs(r.value - mean_val) / abs(mean_val)  # type: ignore[operator]
        if rel_diff > max_relative_diff:
            warnings.append(
                f"{r.dataset_id}: value {r.value:.2f} differs from "  # type: ignore[misc]
                f"cross-provider mean {mean_val:.2f} by {rel_diff:.0%}"
            )

    return warnings
