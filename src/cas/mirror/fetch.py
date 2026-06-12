# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Path-delivery fetch for geofabric mirrors (design header decisions 1 & 4,
§0(b), §3).

The geofabric contract is deliberately **not** the attribute-vector subset
contract. CAS materializes **topology-complete versioned units** (a
continental HydroBASINS region, a MERIT Pfaf-L1 region, a TDX VPU, the NWS
CONUS fabric) and delivers **verified local paths** — never a bbox clip.
A bbox cannot guarantee upstream closure, so clipping a geofabric before the
consumer's upstream-trace would be a correctness regression (design §3). The
trace itself (NetworkX ancestors, the NWS ``network`` table) stays in the
consumer — SYMFLUENCE's ``GeofabricSubsetter`` — which already reads the
``.shp``/``.gpkg``/``.parquet`` formats the mirror produces.

``mirror_fetch`` resolves the unit(s) to deliver (an explicit ``unit``, a
list of ``units``, or a ``bbox``/``point`` resolved through the dataset's unit
resolver), lazily materializes them, copies the required verbatim license
notice next to each unit when the dataset mandates one (HydroBASINS Exhibit
B), and returns one :class:`MirrorFetchResult` per unit with the paths,
materialized format, and full license/attribution/citation/disclaimer
passthrough.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import structlog

from cas.core.config import Settings, get_settings
from cas.core.exceptions import MirrorError, MirrorUnitError
from cas.core.models import BoundingBox
from cas.mirror.datasets import get_mirror_dataset
from cas.mirror.models import MirrorDataset, MirrorFetchResult
from cas.mirror.store import (
    ensure_units_materialized,
    load_manifest,
    unit_notice_path,
    unit_paths,
)
from cas.mirror.units import units_for_bbox

logger = structlog.get_logger(__name__)

BboxLike = BoundingBox | tuple[float, float, float, float] | list[float] | dict

__all__ = ["mirror_fetch", "mirror_fetch_sync"]


def _normalize_bbox(bbox: BboxLike) -> tuple[float, float, float, float]:
    if isinstance(bbox, BoundingBox):
        return (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)
    if isinstance(bbox, dict):
        b = BoundingBox(**bbox)
        return (b.min_lon, b.min_lat, b.max_lon, b.max_lat)
    if len(bbox) != 4:
        raise MirrorError("bbox must be (min_lon, min_lat, max_lon, max_lat)")
    return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))


def _resolve_units(
    ds: MirrorDataset,
    *,
    unit: str | None,
    units: list[str] | None,
    bbox: BboxLike | None,
    point: tuple[float, float] | None,
    settings: Settings,
) -> list[str]:
    """Determine which units to deliver from the selector given."""
    given = [s for s in (unit is not None, units is not None, bbox is not None,
                         point is not None) if s]
    if sum(given) != 1:
        raise MirrorUnitError(
            "mirror_fetch needs exactly one of unit=, units=, bbox=, point=."
        )
    if unit is not None:
        return [unit]
    if units is not None:
        if not units:
            raise MirrorUnitError("units=[] is empty.")
        return list(dict.fromkeys(units))
    if point is not None:
        lat, lon = point
        # A degenerate bbox at the point; the resolver widens conceptually.
        eps = 1e-6
        resolved = units_for_bbox(ds.slug, (lon - eps, lat - eps, lon + eps, lat + eps), settings)
    else:
        assert bbox is not None  # the single-selector guard above guarantees this
        resolved = units_for_bbox(ds.slug, _normalize_bbox(bbox), settings)
    if not resolved:
        sel = point if point is not None else bbox
        raise MirrorUnitError(
            f"No {ds.unit_scheme or 'unit'} of '{ds.spec}' intersects {sel}."
        )
    return resolved


def _format_label(paths: dict[str, Path]) -> str:
    """A consumer-facing format string from the materialized file suffixes."""
    suffixes = []
    for p in paths.values():
        suf = p.suffix.lstrip(".").lower()
        label = "geoparquet" if suf == "parquet" else suf
        if label not in suffixes:
            suffixes.append(label)
    return "+".join(suffixes)


def mirror_fetch_sync(
    dataset_id: str,
    *,
    unit: str | None = None,
    units: list[str] | None = None,
    bbox: BboxLike | None = None,
    point: tuple[float, float] | None = None,
    output_dir: str | Path | None = None,
    settings: Settings | None = None,
    licenses_accepted: bool = False,
    ack_via: str | None = None,
) -> list[MirrorFetchResult]:
    """Materialize topology-complete unit(s) and return their verified paths.

    Selector (exactly one): ``unit`` (one unit id), ``units`` (several),
    ``bbox`` / ``point`` (resolved through the dataset's unit resolver).
    ``output_dir`` optionally copies each unit's files there (and the notice);
    by default the in-mirror paths are returned (true path delivery, no copy).

    Returns one :class:`MirrorFetchResult` per delivered unit.
    """
    start = time.monotonic()
    settings = settings or get_settings()
    ds = get_mirror_dataset(dataset_id)
    if ds.delivery != "path":
        raise MirrorError(
            f"Mirror dataset '{ds.spec}' is not a path-delivery geofabric "
            f"(delivery={ds.delivery!r}). Use cas.mirror_subset(...) for "
            f"attribute vectors."
        )

    wanted = _resolve_units(
        ds, unit=unit, units=units, bbox=bbox, point=point, settings=settings
    )
    # mirror_fetch is the lazy in-process path (explicit=False): an
    # ack-requiring dataset (HydroBASINS) is refused unless pre-accepted via
    # CAS_MIRROR_ACCEPT_LICENSES or licenses_accepted=. Explicit pre-staging
    # is `cas mirror sync`, which goes through ensure_units_materialized
    # directly with explicit=True.
    materialized = ensure_units_materialized(
        ds, wanted, settings,
        explicit=False,
        licenses_accepted=licenses_accepted, ack_via=ack_via,
    )
    manifest = load_manifest(ds, settings)
    if manifest is None:  # pragma: no cover - just materialized
        raise MirrorError(f"manifest for '{ds.spec}' vanished after materialization")

    results: list[MirrorFetchResult] = []
    for u in wanted:
        paths = dict(materialized.get(u) or unit_paths(ds, u, settings))
        if not paths:
            raise MirrorError(f"No files materialized for '{ds.spec}' unit {u}.")
        notice = unit_notice_path(ds, u, settings)

        if output_dir is not None:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            paths = {role: _copy(p, out / p.name) for role, p in paths.items()}
            if notice is not None:
                notice = _copy(notice, out / notice.name)

        primary = paths.get("catchments") or paths.get("basins") or next(iter(paths.values()))
        relevant = [a for a in manifest.archives if a.unit == u]
        archive_notes = "; ".join(
            f"{a.url} (sha256 {a.sha256[:16]}…)" for a in relevant
        )
        notice_note = f"; notice {notice.name}" if notice else ""
        provenance = (
            f"cas {manifest.cas_version} curated mirror {ds.spec} unit {u} "
            f"(path-delivery, topology-complete, NOT bbox-clipped); "
            f"source {archive_notes}; retrieved {manifest.retrieved_at.isoformat()}"
            f"{notice_note}"
        )
        results.append(
            MirrorFetchResult(
                dataset_id=ds.spec,
                slug=ds.slug,
                version=ds.version,
                unit=u,
                paths=paths,
                path=primary,
                format=_format_label(paths),
                notice_path=notice,
                license=ds.license.license,
                license_verified=ds.license.license_verified,
                license_flags=ds.license.license_flags,
                attribution=ds.license.attribution,
                citation=manifest.citation or ds.citation,
                disclaimer=ds.license.disclaimer,
                provenance=provenance,
                elapsed_ms=int((time.monotonic() - start) * 1000),
            )
        )
    logger.info("mirror.fetch", dataset=ds.spec, units=wanted, n=len(results))
    return results


def _copy(src: Path, dest: Path) -> Path:
    import shutil

    if dest.resolve() == src.resolve():
        return dest
    shutil.copy2(src, dest)
    return dest


async def mirror_fetch(
    dataset_id: str,
    *,
    unit: str | None = None,
    units: list[str] | None = None,
    bbox: BboxLike | None = None,
    point: tuple[float, float] | None = None,
    output_dir: str | Path | None = None,
    settings: Settings | None = None,
    licenses_accepted: bool = False,
    ack_via: str | None = None,
) -> list[MirrorFetchResult]:
    """Async facade for :func:`mirror_fetch_sync` (runs in a worker thread)."""
    return await asyncio.to_thread(
        lambda: mirror_fetch_sync(
            dataset_id, unit=unit, units=units, bbox=bbox, point=point,
            output_dir=output_dir, settings=settings,
            licenses_accepted=licenses_accepted, ack_via=ack_via,
        )
    )
