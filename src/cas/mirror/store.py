# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Mirror materialization engine: layout, manifests, integrity, locking.

Layout (design §2)::

    $CAS_MIRROR_DIR/
      index.json                  # mirror-wide: datasets present, totals
      glhymps/2.0/
        manifest.json
        glhymps_v2.0.parquet      # query layer (converted at mirror time)
      rgi7/7.0/
        manifest.json
        units/06/RGI2000-v7.0-G-06_iceland.parquet   # per-unit subdirs

Two materialization shapes share one engine:

- **Global subset datasets** (GLHYMPS, HydroLAKES, WOKAM): one archive →
  one GeoParquet query layer (:func:`ensure_materialized`).
- **Unit-structured datasets** (RGI regions; geofabric regions/VPUs): lazy
  per-unit materialization under ``units/<unit>/``
  (:func:`ensure_units_materialized`), with the manifest accumulating units
  as they land. Per-unit processing is declared on the registry entry
  (``unit_processing``): zip→GeoParquet, zip→GeoPackage, raw passthrough,
  or tar.gz member extraction.

Concurrency (design §1, decision 2 — non-deferrable): materialization takes
an **exclusive OS file lock** on ``<dataset dir>/.lock`` and downloads to
``*.part`` temp files that are checksummed and atomically renamed. A second
process (or thread — each acquires its own file description) arriving
mid-download blocks on the lock, then finds the manifest and returns without
downloading. Lazy first-use under parallel calibration workers is therefore
a single download, not a thundering herd.

Read-only roots (design decision 2): a non-writable mirror root is fine for
*reads* of materialized datasets; materialization into it fails with an
actionable error naming ``cas mirror sync`` and the group-admin path.

Integrity (design §2): upstreams publish no checksums, so archive sha256 is
trust-on-first-fetch — computed while streaming and recorded into the local
manifest. Once a registry entry ships an expected sha256, TOFU upgrades to
verified and a mismatch is a hard :class:`MirrorIntegrityError` carrying
both hashes (upstream silently replacing a file under the same version must
never be silently accepted). These are static releases: no TTL, ever.

Auth (design §4): Earthdata-gated sources (RGI 7.0) download through
:mod:`cas.mirror.earthdata` with the *user's* credentials; credentials are
never written to manifests or logs.
"""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import os
import shutil
import tarfile
import time
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

import httpx
import structlog

from cas.core.config import Settings, get_settings
from cas.core.exceptions import (
    MirrorError,
    MirrorIntegrityError,
    MirrorLicenseError,
    MirrorOfflineError,
    MirrorUnitError,
    MirrorWriteError,
)
from cas.mirror.convert import convert_to_geoparquet
from cas.mirror.datasets import (
    get_mirror_dataset,
    list_mirror_datasets,
    sources_for_unit,
)
from cas.mirror.models import (
    AcknowledgmentRecord,
    ArchiveRecord,
    ConversionRecord,
    FileRecord,
    MirrorDataset,
    MirrorDatasetStatus,
    MirrorManifest,
)

logger = structlog.get_logger(__name__)

_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)
_CHUNK = 1 << 20  # 1 MiB

NOTICE_ROLE = "license_notice"


@contextmanager
def _exclusive_file_lock(path: Path, *, timeout_s: float = 30.0):
    """Hold a blocking, cross-process lock using the host OS primitive."""
    with open(path, "a+b") as lock_file:
        if os.name == "nt":
            if lock_file.seek(0, os.SEEK_END) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            deadline = time.monotonic() + timeout_s
            while True:
                try:
                    msvcrt.locking(  # type: ignore[attr-defined]
                        lock_file.fileno(), msvcrt.LK_NBLCK, 1,  # type: ignore[attr-defined]
                    )
                    break
                except OSError as exc:
                    winerror = getattr(exc, "winerror", None)
                    contended = winerror in {33, 36} or (
                        winerror is None and exc.errno in {errno.EACCES, errno.EAGAIN}
                    )
                    if not contended:
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out waiting for mirror lock {path}") from exc
                    time.sleep(0.05)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock_file.seek(0)
                msvcrt.locking(  # type: ignore[attr-defined]
                    lock_file.fileno(), msvcrt.LK_UNLCK, 1,  # type: ignore[attr-defined]
                )
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# ── Layout helpers ──────────────────────────────────────────────────


def dataset_dir(ds: MirrorDataset, settings: Settings | None = None) -> Path:
    """``{mirror_dir}/{slug}/{version}/`` for a dataset version."""
    settings = settings or get_settings()
    return settings.mirror_dir / ds.slug / ds.version


def manifest_path(ds: MirrorDataset, settings: Settings | None = None) -> Path:
    return dataset_dir(ds, settings) / "manifest.json"


def query_layer_path(ds: MirrorDataset, settings: Settings | None = None) -> Path:
    """The converted GeoParquet query layer for a *global* attribute vector."""
    return dataset_dir(ds, settings) / f"{ds.slug}_v{ds.version}.parquet"


def unit_dir(ds: MirrorDataset, unit: str, settings: Settings | None = None) -> Path:
    """``{dataset dir}/units/{unit}/`` for a unit-structured dataset."""
    return dataset_dir(ds, settings) / "units" / unit


def load_manifest(ds: MirrorDataset, settings: Settings | None = None) -> MirrorManifest | None:
    """Parse ``manifest.json``; ``None`` when absent or unparsable.

    An unparsable manifest is treated as "not materialized" so that a
    re-sync can repair it; ``cas mirror verify`` reports it explicitly.
    """
    path = manifest_path(ds, settings)
    try:
        return MirrorManifest.model_validate_json(path.read_text())
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 — corrupt manifest == not materialized
        logger.warning("mirror.manifest_unparsable", path=str(path))
        return None


def _files_present(manifest: MirrorManifest, ddir: Path, *, unit: str | None = None) -> bool:
    records = [r for r in manifest.files if unit is None or r.unit == unit]
    if unit is not None and not records:
        return False
    for record in records:
        f = ddir / record.path
        if not f.is_file() or f.stat().st_size != record.size_bytes:
            return False
    return True


def is_materialized(ds: MirrorDataset, settings: Settings | None = None) -> bool:
    """Cheap read-path check: manifest parses and every recorded file exists
    with the recorded size (full sha256 is ``cas mirror verify``'s job).

    For unit-structured datasets this means "every *recorded* unit is
    intact" — use :func:`is_unit_materialized` for a specific unit.
    """
    manifest = load_manifest(ds, settings)
    if manifest is None:
        return False
    if not manifest.files:
        return False
    return _files_present(manifest, dataset_dir(ds, settings))


def is_unit_materialized(ds: MirrorDataset, unit: str, settings: Settings | None = None) -> bool:
    """Whether one regional unit is recorded in the manifest and intact."""
    manifest = load_manifest(ds, settings)
    if manifest is None or not manifest.units or unit not in manifest.units:
        return False
    return _files_present(manifest, dataset_dir(ds, settings), unit=unit)


def unit_paths(
    ds: MirrorDataset, unit: str, settings: Settings | None = None
) -> dict[str, Path]:
    """Role → absolute path for one materialized unit (data files only;
    the license notice, when present, is alongside under role
    ``license_notice`` in the manifest)."""
    manifest = load_manifest(ds, settings)
    if manifest is None:
        return {}
    ddir = dataset_dir(ds, settings)
    return {
        r.role: ddir / r.path
        for r in manifest.files
        if r.unit == unit and r.role != NOTICE_ROLE
    }


def unit_notice_path(
    ds: MirrorDataset, unit: str, settings: Settings | None = None
) -> Path | None:
    manifest = load_manifest(ds, settings)
    if manifest is None:
        return None
    ddir = dataset_dir(ds, settings)
    for r in manifest.files:
        if r.unit == unit and r.role == NOTICE_ROLE:
            return ddir / r.path
    return None


# ── Acknowledgment gate ─────────────────────────────────────────────


def acknowledgment_error(ds: MirrorDataset) -> MirrorLicenseError:
    """The actionable refusal for un-acknowledged license terms."""
    lic = ds.license
    return MirrorLicenseError(
        f"Dataset '{ds.spec}' is distributed under license terms that require "
        f"explicit acknowledgment before CAS downloads it on your behalf.\n"
        f"  License:     {lic.license}\n"
        f"  Full text:   {lic.license_url}\n"
        f"  Attribution: {lic.attribution}\n"
        f"To accept: run `cas mirror sync {ds.spec}` interactively, pass "
        f"`--accept-licenses`, or set CAS_MIRROR_ACCEPT_LICENSES={ds.slug}. "
        f"CAS never accepts license terms silently."
    )


def _resolve_acknowledgment(
    ds: MirrorDataset,
    settings: Settings,
    licenses_accepted: bool,
    ack_via: str | None,
) -> AcknowledgmentRecord | None:
    """Return the acceptance to record, or raise if acknowledgment is due.

    The lazy in-process path arrives with ``licenses_accepted=False`` and is
    refused unless the user pre-accepted via ``CAS_MIRROR_ACCEPT_LICENSES``.
    """
    if not ds.license.requires_acknowledgment:
        return None
    if licenses_accepted:
        via = ack_via or "flag"
    elif ds.slug in settings.mirror_accept_licenses:
        via = "env"
    else:
        raise acknowledgment_error(ds)
    return AcknowledgmentRecord(
        license=ds.license.license,
        accepted_at=datetime.now(UTC),
        accepted_via=via,
    )


# ── Shared materialization plumbing ─────────────────────────────────


def _check_offline_and_lazy(ds: MirrorDataset, settings: Settings, explicit: bool) -> None:
    if settings.mirror_offline:
        raise MirrorOfflineError(
            f"Mirror dataset '{ds.spec}' is not materialized and CAS_MIRROR_OFFLINE "
            f"is set. Run `cas mirror sync {ds.spec}` on a network-connected node "
            f"(e.g. an HPC login node) first; reads then work offline."
        )
    if not explicit and not settings.mirror_auto_materialize:
        raise MirrorError(
            f"Mirror dataset '{ds.spec}' is not materialized and lazy "
            f"materialization is disabled (CAS_MIRROR_AUTO_MATERIALIZE=false). "
            f"Run `cas mirror sync {ds.spec}` explicitly."
        )


def _writable_dataset_dir(ds: MirrorDataset, settings: Settings) -> Path:
    ddir = dataset_dir(ds, settings)
    try:
        ddir.mkdir(parents=True, exist_ok=True)
        writable = os.access(ddir, os.W_OK)
    except OSError:
        writable = False
    if not writable:
        raise MirrorWriteError(
            f"Mirror dataset '{ds.spec}' is not materialized and the mirror root "
            f"'{settings.mirror_dir}' is not writable. Point CAS_MIRROR_DIR at a "
            f"writable location and run `cas mirror sync {ds.spec}` (or let the "
            f"first query materialize it) — or, on a shared read-only mirror, ask "
            f"the mirror administrator to sync this dataset into the shared root."
        )
    return ddir


# ── Global (single-archive) materialization ─────────────────────────


def ensure_materialized(
    spec: str | MirrorDataset,
    settings: Settings | None = None,
    *,
    explicit: bool = False,
    licenses_accepted: bool = False,
    ack_via: str | None = None,
    local_archives: dict[str, Path] | None = None,
) -> Path:
    """Materialize a *global* dataset version if needed; return the
    query-layer path.

    Unit-structured datasets (RGI, geofabrics) go through
    :func:`ensure_units_materialized` instead; calling this for one raises
    with that pointer.

    ``explicit=True`` marks a ``cas mirror sync`` invocation; the lazy
    in-process path (``explicit=False``) additionally honors
    ``mirror_auto_materialize`` and refuses ack-requiring datasets that were
    not pre-accepted via the environment.

    ``local_archives`` (archive_name → local path) substitutes user-supplied
    archives for downloads — the ``cas mirror import`` path; everything after
    acquisition (verify/extract/convert/manifest) is identical.
    """
    settings = settings or get_settings()
    ds = get_mirror_dataset(spec) if isinstance(spec, str) else spec
    if ds.is_unit_structured:
        raise MirrorUnitError(
            f"Mirror dataset '{ds.spec}' is unit-structured "
            f"({ds.unit_scheme or 'regional units'}); use "
            f"ensure_units_materialized()/mirror_fetch()/mirror_subset() "
            f"instead of the global query-layer path."
        )

    # Read path first: a materialized dataset is served regardless of
    # offline mode, read-only roots, or acknowledgment state.
    if is_materialized(ds, settings):
        return query_layer_path(ds, settings)

    if local_archives is None:
        _check_offline_and_lazy(ds, settings, explicit)
    ack = _resolve_acknowledgment(ds, settings, licenses_accepted, ack_via)
    ddir = _writable_dataset_dir(ds, settings)

    with _exclusive_file_lock(ddir / ".lock"):
        # A concurrent materializer may have finished while we waited.
        if is_materialized(ds, settings):
            return query_layer_path(ds, settings)
        return _materialize_global_locked(ds, settings, ddir, ack, local_archives)


def _materialize_global_locked(
    ds: MirrorDataset,
    settings: Settings,
    ddir: Path,
    ack: AcknowledgmentRecord | None,
    local_archives: dict[str, Path] | None = None,
) -> Path:
    """Download (or stage) → verify → extract → convert → manifest, locked."""
    archives: list[ArchiveRecord] = []
    kept_members: list[str] = []
    extract_dir = ddir / "_extract"
    archive_paths: list[Path] = []
    shp_path: Path | None = None

    try:
        for source in ds.sources:
            archive = ddir / source.archive_name
            local = (local_archives or {}).get(source.archive_name)
            archives.append(_obtain_archive(ds, source, archive, local))
            archive_paths.append(archive)

            members = _extract_layer(archive, extract_dir, ds.shapefile_patterns)
            kept_members.extend(members)
            for member in members:
                if member.lower().endswith(".shp"):
                    shp_path = extract_dir / Path(member).name

        if shp_path is None:
            raise MirrorError(
                f"No member matching {ds.shapefile_patterns} found in the "
                f"archive(s) for '{ds.spec}' — upstream layout changed?"
            )

        logger.info("mirror.convert", dataset=ds.spec, layer=str(shp_path))
        parquet = query_layer_path(ds, settings)
        conversion, feature_count, columns, crs = convert_to_geoparquet(
            shp_path, parquet, assumed_crs=ds.assumed_crs
        )
        digest, size = _hash_file(parquet)

        manifest = MirrorManifest(
            slug=ds.slug,
            version=ds.version,
            display_name=ds.display_name,
            source_urls=[s.url for s in ds.sources],
            retrieved_at=datetime.now(UTC),
            archives=archives,
            files=[FileRecord(path=parquet.name, sha256=digest, size_bytes=size, role="query_layer")],
            kept_members=kept_members,
            crs=crs,
            feature_count=feature_count,
            columns=columns,
            license=ds.license,
            citation=_render_citation(ds),
            conversion=conversion,
            acknowledgments=[ack] if ack else [],
            units=None,
            cas_version=_cas_version(),
        )
        _write_manifest(manifest_path(ds, settings), manifest)
        logger.info(
            "mirror.materialized", dataset=ds.spec, features=feature_count,
            parquet_bytes=size, sha256=digest,
        )
        return parquet
    finally:
        # Design §3 (3B): keep only the converted parquet — drop the archive
        # and the extracted shapefile; their checksums live in the manifest.
        shutil.rmtree(extract_dir, ignore_errors=True)
        for archive in archive_paths:
            archive.unlink(missing_ok=True)
        for part in ddir.glob("*.part"):
            part.unlink(missing_ok=True)
        _rebuild_index(settings)


# ── Unit-structured materialization ─────────────────────────────────


def resolve_unit_ids(
    ds: MirrorDataset, units: list[str] | None, *, explicit: bool = False
) -> list[str]:
    """Validate/expand a unit-id request against the registry entry.

    ``units=None`` expands to *all* statically declared units — except for
    datasets marked ``units_required_for_sync`` (TDX-Hydro: ~25–40 GB
    global), which refuse with guidance (design §1: ``sync --all`` refused).
    """
    _ = explicit
    if units is None:
        if ds.units_required_for_sync or ds.dynamic_units:
            raise MirrorUnitError(
                f"'{ds.spec}' is materialized per {ds.unit_scheme or 'unit'} only — "
                f"a full sync would be ~{_approx_total(ds)} and is refused by design. "
                f"Name the unit(s) you need (e.g. `cas mirror sync "
                f"{ds.slug}:<unit>`) or let mirror_fetch(..., bbox=...) resolve "
                f"them from your domain."
            )
        return ds.unit_ids()
    known = ds.unit_ids()
    if known:
        unknown = sorted(set(units) - set(known))
        if unknown:
            raise MirrorUnitError(
                f"Unknown unit(s) {unknown} for '{ds.spec}'. "
                f"Known units: {', '.join(known)}."
            )
    return list(dict.fromkeys(units))


def _approx_total(ds: MirrorDataset) -> str:
    if ds.approx_materialized_bytes:
        return _human_bytes(ds.approx_materialized_bytes)
    return "many GB"


def _human_bytes(n: int) -> str:
    size = float(n)
    for suffix in ("B", "KB", "MB", "GB"):
        if size < 1024 or suffix == "GB":
            return f"{int(size)} B" if suffix == "B" else f"{size:.1f} {suffix}"
        size /= 1024
    return f"{size:.1f} GB"


def ensure_units_materialized(
    spec: str | MirrorDataset,
    units: list[str] | None = None,
    settings: Settings | None = None,
    *,
    explicit: bool = False,
    licenses_accepted: bool = False,
    ack_via: str | None = None,
    local_archives: dict[str, Path] | None = None,
) -> dict[str, dict[str, Path]]:
    """Materialize the requested units of a unit-structured dataset.

    Returns ``{unit: {role: path}}`` for the requested units. Only missing
    units are downloaded; the manifest accumulates units as they land (each
    unit is committed to the manifest individually, so a failure mid-way
    keeps every completed unit valid).

    ``local_archives`` (archive_name → local path) substitutes user-supplied
    archives for downloads (the ``cas mirror import`` path).
    """
    settings = settings or get_settings()
    ds = get_mirror_dataset(spec) if isinstance(spec, str) else spec
    if not ds.is_unit_structured:
        raise MirrorUnitError(
            f"Mirror dataset '{ds.spec}' is not unit-structured; use "
            f"ensure_materialized()/mirror_subset() instead."
        )
    wanted = resolve_unit_ids(ds, units, explicit=explicit)
    if not wanted:
        raise MirrorUnitError(f"No units requested for '{ds.spec}'.")

    # Read path first.
    missing = [u for u in wanted if not is_unit_materialized(ds, u, settings)]
    if not missing:
        return {u: unit_paths(ds, u, settings) for u in wanted}

    if local_archives is None:
        _check_offline_and_lazy(ds, settings, explicit)
    ack = _resolve_acknowledgment(ds, settings, licenses_accepted, ack_via)
    ddir = _writable_dataset_dir(ds, settings)

    with _exclusive_file_lock(ddir / ".lock"):
        try:
            for unit in missing:
                # A concurrent materializer may have landed this unit.
                if is_unit_materialized(ds, unit, settings):
                    continue
                _materialize_unit_locked(ds, unit, settings, ddir, ack, local_archives)
        finally:
            _rebuild_index(settings)
    return {u: unit_paths(ds, u, settings) for u in wanted}


def _materialize_unit_locked(
    ds: MirrorDataset,
    unit: str,
    settings: Settings,
    ddir: Path,
    ack: AcknowledgmentRecord | None,
    local_archives: dict[str, Path] | None = None,
) -> None:
    """Download (or stage) → verify → process → merge manifest, one unit."""
    sources = sources_for_unit(ds, unit)
    udir = unit_dir(ds, unit, settings)
    udir.mkdir(parents=True, exist_ok=True)
    extract_dir = ddir / "_extract"

    archives: list[ArchiveRecord] = []
    files: list[FileRecord] = []
    kept_members: list[str] = []
    conversion: ConversionRecord | None = None
    crs = ""
    feature_count = 0
    columns: list[str] = []
    cleanup: list[Path] = []

    try:
        for source in sources:
            archive = ddir / source.archive_name
            local = (local_archives or {}).get(source.archive_name)
            record = _obtain_archive(ds, source, archive, local, unit=unit)
            archives.append(record)

            if ds.unit_processing == "raw":
                # The download *is* the artifact — keep it byte-identical.
                _validate_raw_format(archive, source.archive_name, ds)
                dest = udir / source.archive_name
                archive.replace(dest)
                files.append(
                    FileRecord(
                        path=str(dest.relative_to(ddir)),
                        sha256=record.sha256,
                        size_bytes=record.size_bytes,
                        role=source.role,
                        unit=unit,
                    )
                )
                continue

            cleanup.append(archive)
            if ds.unit_processing == "tar_member":
                produced = _extract_tar_members(archive, udir, ds.shapefile_patterns)
                if not produced:
                    raise MirrorError(
                        f"No member matching {ds.shapefile_patterns} found in "
                        f"{source.archive_name} for '{ds.spec}' unit {unit} — "
                        f"upstream layout changed?"
                    )
                kept_members.extend(name for name, _ in produced)
                for _name, out in produced:
                    fdigest, fsize = _hash_file(out)
                    files.append(
                        FileRecord(
                            path=str(out.relative_to(ddir)),
                            sha256=fdigest,
                            size_bytes=fsize,
                            role=source.role,
                            unit=unit,
                        )
                    )
                continue

            # zip → shapefile(s) → GeoParquet ("geoparquet") or GeoPackage
            # ("gpkg"). One archive may carry several layers to materialize
            # (MERIT-Basins: cat_pfaf_* + riv_pfaf_* in one zip) — declared
            # per source as role → member patterns.
            role_patterns = (
                list(source.members.items())
                if source.members
                else [(source.role, ds.shapefile_patterns)]
            )
            for role, patterns in role_patterns:
                members = _extract_layer(archive, extract_dir, patterns)
                shp_path = next(
                    (extract_dir / Path(m).name for m in members if m.lower().endswith(".shp")),
                    None,
                )
                if shp_path is None:
                    raise MirrorError(
                        f"No member matching {patterns} found in "
                        f"{source.archive_name} for '{ds.spec}' unit {unit} "
                        f"(role {role}) — upstream layout changed, or the "
                        f"wrong archive was supplied to `cas mirror import`?"
                    )
                kept_members.extend(members)

                if ds.unit_processing == "geoparquet":
                    out = udir / f"{shp_path.stem}.parquet"
                    converted = convert_to_geoparquet(
                        shp_path, out, assumed_crs=ds.assumed_crs
                    )
                elif ds.unit_processing == "gpkg":
                    out = udir / f"{shp_path.stem}.gpkg"
                    converted = _convert_to_gpkg(
                        shp_path, out, assumed_crs=ds.assumed_crs
                    )
                else:
                    raise MirrorError(
                        f"Unknown unit_processing '{ds.unit_processing}' for '{ds.spec}'."
                    )
                # The manifest keeps ONE conversion record per dataset; when
                # several layers convert (MERIT cat+riv), carry an
                # assumed_crs note forward so it is never silently dropped
                # (the cat layer has no .prj, the riv layer does).
                prev_assumed = (
                    conversion.parameters.get("assumed_crs") if conversion else None
                )
                conversion, count, columns, crs = converted
                if prev_assumed and "assumed_crs" not in conversion.parameters:
                    conversion.parameters["assumed_crs"] = prev_assumed
                feature_count += count
                fdigest, fsize = _hash_file(out)
                files.append(
                    FileRecord(
                        path=str(out.relative_to(ddir)),
                        sha256=fdigest,
                        size_bytes=fsize,
                        role=role,
                        unit=unit,
                    )
                )
                shutil.rmtree(extract_dir, ignore_errors=True)

        if ds.notice_file:
            notice = _copy_notice(ds, udir)
            ndigest, nsize = _hash_file(notice)
            files.append(
                FileRecord(
                    path=str(notice.relative_to(ddir)),
                    sha256=ndigest,
                    size_bytes=nsize,
                    role=NOTICE_ROLE,
                    unit=unit,
                )
            )

        _merge_manifest(
            ds, settings, unit=unit, archives=archives, files=files,
            kept_members=kept_members, conversion=conversion, crs=crs,
            feature_count=feature_count, columns=columns, ack=ack,
        )
        logger.info("mirror.unit_materialized", dataset=ds.spec, unit=unit,
                    files=len(files))
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        for path in cleanup:
            path.unlink(missing_ok=True)
        for part in (*ddir.glob("*.part"), *ddir.rglob("*.part.gpkg")):
            part.unlink(missing_ok=True)


def _merge_manifest(
    ds: MirrorDataset,
    settings: Settings,
    *,
    unit: str,
    archives: list[ArchiveRecord],
    files: list[FileRecord],
    kept_members: list[str],
    conversion: ConversionRecord | None,
    crs: str,
    feature_count: int,
    columns: list[str],
    ack: AcknowledgmentRecord | None,
) -> None:
    """Fold one freshly materialized unit into the dataset manifest."""
    manifest = load_manifest(ds, settings)
    if manifest is None:
        manifest = MirrorManifest(
            slug=ds.slug,
            version=ds.version,
            display_name=ds.display_name,
            retrieved_at=datetime.now(UTC),
            license=ds.license,
            citation=_render_citation(ds),
            units=[],
        )
    # Replace any stale records for this unit (re-sync after corruption).
    manifest.archives = [a for a in manifest.archives if a.unit != unit] + archives
    manifest.files = [f for f in manifest.files if f.unit != unit] + files
    manifest.source_urls = sorted(set(manifest.source_urls) | {a.url for a in archives})
    manifest.kept_members = sorted(set(manifest.kept_members) | set(kept_members))
    manifest.units = sorted(set(manifest.units or []) | {unit})
    manifest.retrieved_at = datetime.now(UTC)
    manifest.citation = _render_citation(ds)
    if conversion is not None:
        manifest.conversion = conversion
    if crs:
        manifest.crs = crs
    if columns and not manifest.columns:
        manifest.columns = columns
    manifest.feature_count += feature_count
    if ack and not any(
        a.license == ack.license for a in manifest.acknowledgments
    ):
        manifest.acknowledgments.append(ack)
    manifest.cas_version = _cas_version()
    _write_manifest(manifest_path(ds, settings), manifest)


def _copy_notice(ds: MirrorDataset, udir: Path) -> Path:
    """Copy the packaged verbatim license notice next to the unit's files."""
    from importlib import resources

    assert ds.notice_file is not None
    ref = resources.files("cas.mirror") / "notices" / ds.notice_file
    dest = udir / ds.notice_file
    dest.write_bytes(ref.read_bytes())
    return dest


def _convert_to_gpkg(
    source: Path, dest: Path, *, assumed_crs: str = ""
):  # -> tuple[ConversionRecord, int, list[str], str]
    """Shapefile → single-layer GeoPackage (recorded as a conversion).

    Used for shapefile-distributed geofabric units: the consumer's reader
    (SYMFLUENCE ``GeofabricSubsetter``) reads GeoPackage natively, and gpkg
    carries an R-tree index plus lossless column names. All columns —
    including the topology columns — are kept untouched. ``assumed_crs`` is
    assigned (never reprojected) when the layer ships without a CRS
    (MERIT-Basins catchments have no ``.prj``) and recorded as provenance.
    """
    from cas.mirror.convert import _import_geopandas

    gpd = _import_geopandas()
    import pyogrio

    gdf = gpd.read_file(source)
    crs_assumed = False
    if gdf.crs is None and assumed_crs:
        gdf = gdf.set_crs(assumed_crs)
        crs_assumed = True
    # Keep a .gpkg suffix on the temp so the GPKG driver doesn't warn.
    tmp = dest.with_name(dest.stem + ".part.gpkg")
    gdf.to_file(tmp, driver="GPKG", layer=dest.stem)
    tmp.replace(dest)
    columns = [c for c in gdf.columns if c != gdf.geometry.name]
    crs = str(gdf.crs) if gdf.crs is not None else ""
    record = ConversionRecord(
        tool_versions={"geopandas": gpd.__version__, "pyogrio": pyogrio.__version__},
        parameters={
            "format": "GPKG",
            "source_format": source.suffix.lstrip(".") or "unknown",
            "columns_kept": "all",
            **({"assumed_crs": assumed_crs} if crs_assumed else {}),
        },
    )
    return record, len(gdf), columns, crs


def _render_citation(ds: MirrorDataset) -> str:
    """Fill the ``{access_date}`` placeholder (NSIDC-style citations require
    the access date; the manifest's ``retrieved_at`` is the authority)."""
    return ds.citation.replace("{access_date}", datetime.now(UTC).date().isoformat())


# ── Download / extraction primitives ────────────────────────────────


def _obtain_archive(
    ds: MirrorDataset,
    source,  # MirrorSource
    archive: Path,
    local: Path | None,
    *,
    unit: str | None = None,
) -> ArchiveRecord:
    """Land one source archive at ``archive`` — by download, or by staging a
    user-supplied local copy (``cas mirror import``) — and record provenance.

    A registry-pinned sha256 is enforced either way; a local archive that
    matches it upgrades straight to ``registry``-verified, otherwise the
    import is recorded as ``tofu-import`` with the origin path.
    """
    if local is None:
        logger.info(
            "mirror.download", dataset=ds.spec, unit=unit, url=source.url,
            approx_bytes=source.size_bytes_approx,
        )
        digest, size = _download(source.url, archive, source.sha256, auth=ds.auth)
        return ArchiveRecord(
            url=source.url,
            archive_name=source.archive_name,
            sha256=digest,
            size_bytes=size,
            sha256_source="registry" if source.sha256 else "tofu",
            unit=unit,
        )
    logger.info(
        "mirror.import_archive", dataset=ds.spec, unit=unit, path=str(local),
    )
    digest, size = _stage_local_archive(local, archive, source.sha256)
    return ArchiveRecord(
        url=source.url,
        archive_name=source.archive_name,
        sha256=digest,
        size_bytes=size,
        sha256_source="registry" if source.sha256 else "tofu-import",
        unit=unit,
        source="manual-import",
        imported_from=str(local),
    )


def _stage_local_archive(
    src: Path, dest: Path, expected_sha256: str | None
) -> tuple[str, int]:
    """Copy a user-supplied archive into the dataset dir while hashing;
    verify against a registry checksum when one is pinned; atomic rename.
    The original file is never modified or removed."""
    part = dest.with_name(dest.name + ".part")
    hasher = hashlib.sha256()
    with open(src, "rb") as fh, open(part, "wb") as out:
        while chunk := fh.read(_CHUNK):
            hasher.update(chunk)
            out.write(chunk)
    digest = hasher.hexdigest()
    if expected_sha256 and digest != expected_sha256:
        part.unlink(missing_ok=True)
        raise MirrorIntegrityError(
            f"Imported archive {src} does not match the registry checksum for "
            f"this dataset version — wrong file, wrong version, or a modified "
            f"copy; refusing to import",
            expected=expected_sha256,
            actual=digest,
        )
    size = part.stat().st_size
    part.replace(dest)
    return digest, size


def _download(
    url: str, dest: Path, expected_sha256: str | None, *, auth: str | None = None
) -> tuple[str, int]:
    """Stream to ``dest.part`` while hashing; verify; atomically rename."""
    part = dest.with_name(dest.name + ".part")
    hasher = hashlib.sha256()
    if auth == "earthdata":
        from cas.mirror.earthdata import earthdata_stream

        stream_cm = earthdata_stream(url)
    elif auth is not None:
        raise MirrorError(f"Unknown mirror auth scheme '{auth}' for {url}.")
    else:
        from cas.mirror.gdrive import gdrive_stream, is_gdrive_url

        if is_gdrive_url(url):
            # Google Drive public files (MERIT-Basins) need the virus-scan
            # confirm dance; quota interstitials fail actionably.
            stream_cm = gdrive_stream(url)
        else:
            stream_cm = httpx.stream(
                "GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT
            )
    with stream_cm as response:
        response.raise_for_status()
        with open(part, "wb") as fh:
            for chunk in response.iter_bytes(_CHUNK):
                hasher.update(chunk)
                fh.write(chunk)
    digest = hasher.hexdigest()
    if expected_sha256 and digest != expected_sha256:
        part.unlink(missing_ok=True)
        raise MirrorIntegrityError(
            f"Archive downloaded from {url} does not match the registry checksum. "
            f"Upstream may have silently replaced the file under the same version; "
            f"refusing to materialize",
            expected=expected_sha256,
            actual=digest,
        )
    size = part.stat().st_size
    part.replace(dest)
    return digest, size


def _extract_layer(archive: Path, extract_dir: Path, patterns: list[str]) -> list[str]:
    """Extract only the needed vector layer from a zip (design §0 columns).

    The first pattern with matches wins; among multiple matching ``.shp``
    members the largest is taken; all sidecars sharing its stem (.shx, .dbf,
    .prj, .cpg, ...) come along. Members are flattened to their basenames
    (zip-slip safe). Returns the kept member names as found in the archive.
    """
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        zf_cm = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise MirrorError(
            f"{archive.name} is not a valid zip archive — a truncated "
            f"download, an upstream interstitial page, or (for `cas mirror "
            f"import`) the wrong file."
        ) from exc
    with zf_cm as zf:
        names = [info.filename for info in zf.infolist() if not info.is_dir()]
        selected: str | None = None
        for pattern in patterns:
            matches = [n for n in names if fnmatch.fnmatch(n.lower(), pattern.lower())]
            if matches:
                selected = max(matches, key=lambda n: zf.getinfo(n).file_size)
                break
        if selected is None:
            return []
        stem = selected[: selected.lower().rindex(".shp")]
        family = [n for n in names if n.lower().startswith(stem.lower() + ".")]
        for member in family:
            target = extract_dir / Path(member).name
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        return family


_RAW_MAGIC = {
    ".parquet": b"PAR1",
    ".gpkg": b"SQLite format 3\x00",
}


def _validate_raw_format(path: Path, archive_name: str, ds: MirrorDataset) -> None:
    """Cheap magic-byte check for ``unit_processing="raw"`` artifacts.

    Catches an upstream HTML error page saved as ``.parquet``/``.gpkg`` and,
    on the ``cas mirror import`` path, the wrong file supplied for the role.
    """
    suffix = Path(archive_name).suffix.lower()
    magic = _RAW_MAGIC.get(suffix)
    if magic is None:
        return
    with open(path, "rb") as fh:
        head = fh.read(len(magic))
    if head != magic:
        raise MirrorError(
            f"{archive_name} for '{ds.spec}' does not look like a "
            f"{suffix.lstrip('.')} file (bad magic bytes) — a truncated/HTML "
            f"download, or the wrong file supplied to `cas mirror import`."
        )


def _extract_tar_members(
    archive: Path, dest_dir: Path, patterns: list[str]
) -> list[tuple[str, Path]]:
    """Stream members matching ``patterns`` out of a tar(.gz) archive.

    Members are flattened to their basenames (path-traversal safe) and
    written via ``*.part`` + atomic rename. Returns (member name, path).
    """
    produced: list[tuple[str, Path]] = []
    with tarfile.open(archive, "r:*") as tf:
        for member in tf:
            if not member.isfile():
                continue
            if not any(fnmatch.fnmatch(member.name.lower(), p.lower()) for p in patterns):
                continue
            target = dest_dir / Path(member.name).name
            part = target.with_name(target.name + ".part")
            src = tf.extractfile(member)
            if src is None:  # pragma: no cover - directories filtered above
                continue
            with src, open(part, "wb") as dst:
                shutil.copyfileobj(src, dst, length=_CHUNK)
            part.replace(target)
            produced.append((member.name, target))
    return produced


def _hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest(), path.stat().st_size


def _write_manifest(path: Path, manifest: MirrorManifest) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(manifest.model_dump_json(indent=2) + "\n")
    tmp.replace(path)


def _cas_version() -> str:
    from cas import __version__

    return __version__


# ── Verify / status / remove / index ────────────────────────────────


def verify(spec: str | MirrorDataset, settings: Settings | None = None) -> list[str]:
    """Full re-checksum of materialized files against the manifest.

    Returns a list of problems (empty == verified OK).
    """
    settings = settings or get_settings()
    ds = get_mirror_dataset(spec) if isinstance(spec, str) else spec
    mpath = manifest_path(ds, settings)
    if not mpath.is_file():
        return [f"{ds.spec}: not materialized (no manifest at {mpath})"]
    manifest = load_manifest(ds, settings)
    if manifest is None:
        return [f"{ds.spec}: manifest at {mpath} is unparsable — re-sync to repair"]

    problems: list[str] = []
    ddir = dataset_dir(ds, settings)
    for record in manifest.files:
        f = ddir / record.path
        if not f.is_file():
            problems.append(f"{ds.spec}: missing file {record.path}")
            continue
        digest, size = _hash_file(f)
        if size != record.size_bytes:
            problems.append(
                f"{ds.spec}: size mismatch for {record.path} "
                f"(manifest {record.size_bytes}, on disk {size})"
            )
        if digest != record.sha256:
            problems.append(
                f"{ds.spec}: sha256 mismatch for {record.path} "
                f"(manifest {record.sha256}, on disk {digest})"
            )
    return problems


def remove(spec: str | MirrorDataset, settings: Settings | None = None) -> bool:
    """Delete a materialized dataset version (``cas mirror remove``)."""
    settings = settings or get_settings()
    ds = get_mirror_dataset(spec) if isinstance(spec, str) else spec
    ddir = dataset_dir(ds, settings)
    if not ddir.exists():
        return False
    shutil.rmtree(ddir)
    parent = ddir.parent
    if parent != settings.mirror_dir and not any(parent.iterdir()):
        parent.rmdir()
    _rebuild_index(settings)
    return True


def _disk_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def status(settings: Settings | None = None) -> list[MirrorDatasetStatus]:
    """Per-dataset status: materialized state, disk use, license, checksums."""
    settings = settings or get_settings()
    rows: list[MirrorDatasetStatus] = []
    for ds in list_mirror_datasets(all_versions=True):
        manifest = load_manifest(ds, settings)
        materialized = is_materialized(ds, settings)
        if materialized and manifest is not None:
            checksum_state = (
                "registry-verified"
                if manifest.archives and all(a.sha256_source == "registry" for a in manifest.archives)
                else "tofu"
            )
        else:
            checksum_state = "unverified"
        ddir = dataset_dir(ds, settings)
        static_units = ds.unit_ids()
        rows.append(
            MirrorDatasetStatus(
                slug=ds.slug,
                version=ds.version,
                display_name=ds.display_name,
                materialized=materialized,
                disk_bytes=_disk_bytes(ddir) if ddir.exists() else 0,
                license=ds.license.license,
                license_flags=ds.license.license_flags,
                checksum_state=checksum_state,
                path=str(ddir),
                delivery=ds.delivery,
                units_total=len(static_units) if static_units else None,
                units_materialized=list(manifest.units or []) if manifest else [],
                disk_note=ds.disk_note,
            )
        )
    return rows


def _rebuild_index(settings: Settings) -> None:
    """Best-effort mirror-wide ``index.json`` (datasets present, totals)."""
    root = settings.mirror_dir
    try:
        entries = []
        total = 0
        for mpath in sorted(root.glob("*/*/manifest.json")):
            ddir = mpath.parent
            size = _disk_bytes(ddir)
            total += size
            try:
                data = json.loads(mpath.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            entries.append(
                {
                    "slug": data.get("slug", ddir.parent.name),
                    "version": data.get("version", ddir.name),
                    "size_bytes": size,
                    "path": str(ddir.relative_to(root)),
                }
            )
        index = {
            "generated_at": datetime.now(UTC).isoformat(),
            "datasets": entries,
            "total_size_bytes": total,
        }
        tmp = root / "index.json.part"
        tmp.write_text(json.dumps(index, indent=2) + "\n")
        tmp.replace(root / "index.json")
    except OSError:  # read-only or vanished root — index is advisory only
        logger.debug("mirror.index_skip", root=str(root))


# Re-exported for the CLI and tests.
__all__ = [
    "dataset_dir",
    "manifest_path",
    "query_layer_path",
    "unit_dir",
    "unit_paths",
    "unit_notice_path",
    "load_manifest",
    "is_materialized",
    "is_unit_materialized",
    "ensure_materialized",
    "ensure_units_materialized",
    "resolve_unit_ids",
    "acknowledgment_error",
    "verify",
    "remove",
    "status",
    "NOTICE_ROLE",
]
