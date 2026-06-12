# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Mirror materialization engine: layout, manifests, integrity, locking.

Layout (design §2)::

    $CAS_MIRROR_DIR/
      index.json                  # mirror-wide: datasets present, totals
      glhymps/2.0/
        manifest.json
        glhymps_v2.0.parquet      # query layer (converted at mirror time)

Concurrency (design §1, decision 2 — non-deferrable): materialization takes
an **exclusive fcntl lock** on ``<dataset dir>/.lock`` and downloads to
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
"""

from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import json
import os
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog

from cas.core.config import Settings, get_settings
from cas.core.exceptions import (
    MirrorError,
    MirrorIntegrityError,
    MirrorLicenseError,
    MirrorOfflineError,
    MirrorWriteError,
)
from cas.mirror.convert import convert_to_geoparquet
from cas.mirror.datasets import get_mirror_dataset, list_mirror_datasets
from cas.mirror.models import (
    AcknowledgmentRecord,
    ArchiveRecord,
    FileRecord,
    MirrorDataset,
    MirrorDatasetStatus,
    MirrorManifest,
)

logger = structlog.get_logger(__name__)

_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)
_CHUNK = 1 << 20  # 1 MiB


# ── Layout helpers ──────────────────────────────────────────────────


def dataset_dir(ds: MirrorDataset, settings: Settings | None = None) -> Path:
    """``{mirror_dir}/{slug}/{version}/`` for a dataset version."""
    settings = settings or get_settings()
    return settings.mirror_dir / ds.slug / ds.version


def manifest_path(ds: MirrorDataset, settings: Settings | None = None) -> Path:
    return dataset_dir(ds, settings) / "manifest.json"


def query_layer_path(ds: MirrorDataset, settings: Settings | None = None) -> Path:
    """The converted GeoParquet query layer for an attribute-vector dataset."""
    return dataset_dir(ds, settings) / f"{ds.slug}_v{ds.version}.parquet"


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


def is_materialized(ds: MirrorDataset, settings: Settings | None = None) -> bool:
    """Cheap read-path check: manifest parses and every file exists with the
    recorded size (full sha256 is ``cas mirror verify``'s job, design §2)."""
    manifest = load_manifest(ds, settings)
    if manifest is None:
        return False
    ddir = dataset_dir(ds, settings)
    for record in manifest.files:
        f = ddir / record.path
        if not f.is_file() or f.stat().st_size != record.size_bytes:
            return False
    return True


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


# ── Materialization ─────────────────────────────────────────────────


def ensure_materialized(
    spec: str | MirrorDataset,
    settings: Settings | None = None,
    *,
    explicit: bool = False,
    licenses_accepted: bool = False,
    ack_via: str | None = None,
) -> Path:
    """Materialize a dataset version if needed; return the query-layer path.

    ``explicit=True`` marks a ``cas mirror sync`` invocation; the lazy
    in-process path (``explicit=False``) additionally honors
    ``mirror_auto_materialize`` and refuses ack-requiring datasets that were
    not pre-accepted via the environment.
    """
    settings = settings or get_settings()
    ds = get_mirror_dataset(spec) if isinstance(spec, str) else spec

    # Read path first: a materialized dataset is served regardless of
    # offline mode, read-only roots, or acknowledgment state.
    if is_materialized(ds, settings):
        return query_layer_path(ds, settings)

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
    ack = _resolve_acknowledgment(ds, settings, licenses_accepted, ack_via)

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

    lock_path = ddir / ".lock"
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            # A concurrent materializer may have finished while we waited.
            if is_materialized(ds, settings):
                return query_layer_path(ds, settings)
            return _materialize_locked(ds, settings, ddir, ack)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _materialize_locked(
    ds: MirrorDataset,
    settings: Settings,
    ddir: Path,
    ack: AcknowledgmentRecord | None,
) -> Path:
    """Download → verify → extract → convert → manifest, under the lock."""
    archives: list[ArchiveRecord] = []
    kept_members: list[str] = []
    extract_dir = ddir / "_extract"
    archive_paths: list[Path] = []
    shp_path: Path | None = None

    try:
        for source in ds.sources:
            archive = ddir / source.archive_name
            logger.info(
                "mirror.download", dataset=ds.spec, url=source.url,
                approx_bytes=source.size_bytes_approx,
            )
            digest, size = _download(source.url, archive, source.sha256)
            archive_paths.append(archive)
            archives.append(
                ArchiveRecord(
                    url=source.url,
                    archive_name=source.archive_name,
                    sha256=digest,
                    size_bytes=size,
                    sha256_source="registry" if source.sha256 else "tofu",
                )
            )

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
        conversion, feature_count, columns, crs = convert_to_geoparquet(shp_path, parquet)
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
            citation=ds.citation,
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


def _download(url: str, dest: Path, expected_sha256: str | None) -> tuple[str, int]:
    """Stream to ``dest.part`` while hashing; verify; atomically rename."""
    part = dest.with_name(dest.name + ".part")
    hasher = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT) as response:
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
    with zipfile.ZipFile(archive) as zf:
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
