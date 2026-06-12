# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Manual-staging import: ``cas mirror import`` (design §2 escape hatch).

Some distributions cannot be downloaded by CAS at all (Globus-only mirrors
such as the reachhydro MERIT-Basins collection), are registration-gated
(MSWEP-style upstreams), or are simply rate-limited at the moment the user
needs them (Google Drive quota interstitials). The user obtains the archive
themselves; CAS then

1. **verifies** it against the registry expectations — exact archive names
   for multi-file units, the expected member names/format for the dataset's
   processing mode, and the registry sha256 when one is pinned (a mismatch
   is a hard :class:`MirrorIntegrityError`);
2. records the checksum as ``tofu-import`` with provenance
   ``source="manual-import"`` plus the local path it came from; and
3. runs the **same** extraction/conversion/manifest pipeline as
   ``cas mirror sync`` — the materialized result is indistinguishable apart
   from its provenance.

License terms that require acknowledgment still require it at import time:
CAS never accepts terms silently, even when it moved none of the bytes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from cas.core.config import Settings, get_settings
from cas.core.exceptions import MirrorError, MirrorUnitError
from cas.mirror.datasets import get_mirror_dataset, sources_for_unit
from cas.mirror.models import MirrorDataset, MirrorImportResult, MirrorSource
from cas.mirror.store import (
    ensure_materialized,
    ensure_units_materialized,
    is_materialized,
    is_unit_materialized,
    load_manifest,
    query_layer_path,
    unit_paths,
)

logger = structlog.get_logger(__name__)

__all__ = ["mirror_import", "mirror_import_sync"]


def _map_local_archives(
    ds: MirrorDataset, sources: list[MirrorSource], source_path: Path
) -> dict[str, Path]:
    """Resolve the user's file/directory to ``{archive_name: local path}``.

    A single file is accepted only when the target expects exactly one
    archive; a directory must contain every expected archive under its
    exact registry name.
    """
    expected = [s.archive_name for s in sources]
    if source_path.is_file():
        if len(expected) != 1:
            raise MirrorError(
                f"'{ds.spec}' expects {len(expected)} archives "
                f"({', '.join(expected)}); pass a directory containing all of "
                f"them instead of a single file."
            )
        return {expected[0]: source_path}
    if source_path.is_dir():
        mapping: dict[str, Path] = {}
        missing: list[str] = []
        for name in expected:
            candidate = source_path / name
            if candidate.is_file():
                mapping[name] = candidate
            else:
                missing.append(name)
        if missing:
            raise MirrorError(
                f"Directory {source_path} is missing expected archive(s) for "
                f"'{ds.spec}': {', '.join(missing)}. Expected exact names: "
                f"{', '.join(expected)}."
            )
        return mapping
    raise MirrorError(f"Import source {source_path} is not a file or directory.")


def mirror_import_sync(
    dataset_id: str,
    source: str | Path,
    *,
    unit: str | None = None,
    settings: Settings | None = None,
    licenses_accepted: bool = False,
    ack_via: str | None = None,
) -> MirrorImportResult:
    """Verify a user-obtained archive and materialize it like a sync.

    ``dataset_id`` is a mirror spec (``"merit_basins"``, ``"glhymps==2.0"``).
    ``source`` is the local archive file, or a directory holding every
    expected archive under its exact registry name. ``unit`` is required for
    unit-structured datasets (RGI regions, geofabric units) and rejected for
    single-archive globals.
    """
    settings = settings or get_settings()
    ds = get_mirror_dataset(dataset_id)
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise MirrorError(f"Import source {source_path} does not exist.")

    if ds.is_unit_structured:
        if unit is None:
            known = ds.unit_ids()
            raise MirrorUnitError(
                f"'{ds.spec}' is materialized per {ds.unit_scheme or 'unit'}; "
                f"name the unit being imported with --unit "
                f"(known units: {', '.join(known) or 'resolved dynamically'})."
            )
        if is_unit_materialized(ds, unit, settings):
            raise MirrorError(
                f"'{ds.spec}' unit {unit} is already materialized. Run "
                f"`cas mirror remove {ds.spec}` first to replace it."
            )
        srcs = sources_for_unit(ds, unit)
        mapping = _map_local_archives(ds, srcs, source_path)
        ensure_units_materialized(
            ds, [unit], settings,
            explicit=True,
            licenses_accepted=licenses_accepted,
            ack_via=ack_via,
            local_archives=mapping,
        )
        paths = unit_paths(ds, unit, settings)
        primary = (
            paths.get("catchments") or paths.get("basins") or next(iter(paths.values()))
        )
    else:
        if unit is not None:
            raise MirrorUnitError(
                f"'{ds.spec}' is a single global dataset; it has no unit '{unit}'."
            )
        if is_materialized(ds, settings):
            raise MirrorError(
                f"'{ds.spec}' is already materialized. Run "
                f"`cas mirror remove {ds.spec}` first to replace it."
            )
        mapping = _map_local_archives(ds, ds.sources, source_path)
        primary = ensure_materialized(
            ds, settings,
            explicit=True,
            licenses_accepted=licenses_accepted,
            ack_via=ack_via,
            local_archives=mapping,
        )
        paths = {"query_layer": query_layer_path(ds, settings)}

    manifest = load_manifest(ds, settings)
    if manifest is None:  # pragma: no cover - just materialized
        raise MirrorError(f"manifest for '{ds.spec}' vanished after import")
    relevant = [
        a for a in manifest.archives
        if (a.unit == unit if unit is not None else a.unit is None)
    ]
    archive_notes = "; ".join(
        f"{a.archive_name} (sha256 {a.sha256[:16]}…, {a.sha256_source})"
        for a in relevant
    )
    provenance = (
        f"cas {manifest.cas_version} curated mirror {ds.spec}"
        f"{f' unit {unit}' if unit else ''}; manual-import from {source_path}; "
        f"{archive_notes}; corresponds to upstream "
        f"{'; '.join(a.url for a in relevant)}"
    )
    logger.info(
        "mirror.imported", dataset=ds.spec, unit=unit, source=str(source_path),
    )
    return MirrorImportResult(
        dataset_id=ds.spec,
        slug=ds.slug,
        version=ds.version,
        unit=unit,
        paths=paths,
        path=primary,
        imported_from=str(source_path),
        archives=relevant,
        license=ds.license.license,
        license_flags=ds.license.license_flags,
        attribution=ds.license.attribution,
        citation=manifest.citation or ds.citation,
        provenance=provenance,
    )


async def mirror_import(
    dataset_id: str,
    source: str | Path,
    *,
    unit: str | None = None,
    settings: Settings | None = None,
    licenses_accepted: bool = False,
    ack_via: str | None = None,
) -> MirrorImportResult:
    """Async facade for :func:`mirror_import_sync` (runs in a worker thread)."""
    return await asyncio.to_thread(
        lambda: mirror_import_sync(
            dataset_id, source, unit=unit, settings=settings,
            licenses_accepted=licenses_accepted, ack_via=ack_via,
        )
    )
