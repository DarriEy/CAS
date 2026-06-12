# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Pydantic models for the curated-mirror tier.

Three groups:

- **Registry declarations** (:class:`MirrorDataset`, :class:`MirrorSource`,
  :class:`MirrorLicense`) — the static, version-pinned facts CAS ships about
  each mirrorable dataset.
- **Manifest** (:class:`MirrorManifest` and its records) — what was actually
  materialized on *this* disk: exact sources, checksums, conversion
  provenance, license acknowledgments. Schema-versioned
  (``manifest_schema``).
- **Query results** (:class:`MirrorSubsetResult`) — what a subset call
  returns: a path plus license/attribution/provenance passthrough.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

MANIFEST_SCHEMA_VERSION = 1


class MirrorLicense(BaseModel):
    """License metadata per the design's verification pass (design §8)."""

    license: str
    """License identifier (SPDX-ish where one exists, verbatim label otherwise)."""
    license_url: str = ""
    """Pointer to the full license text."""
    license_verified: bool = False
    """True once the actual license text has been read and confirmed for
    this exact product/distribution (design §8); False for assumed terms."""
    attribution: str = ""
    """Attribution string outputs must carry (verbatim where mandated)."""
    license_flags: list[str] = Field(default_factory=list)
    """Caveats: e.g. ``unconfirmed``, ``share-alike``, ``nc-fork``."""
    requires_acknowledgment: bool = False
    """True ⇒ ``cas mirror sync`` must surface the terms and record explicit
    acceptance before materializing; the lazy path refuses outright."""
    disclaimer: str = ""
    """Extra honesty text carried into manifests/results (e.g. the NWS
    hydrofabric provisional-data disclaimer, design §8)."""


class MirrorSource(BaseModel):
    """One downloadable archive (or raw file) making up a dataset version."""

    url: str
    archive_name: str
    """Local filename for the downloaded archive."""
    sha256: str | None = None
    """Expected archive checksum. ``None`` ⇒ trust-on-first-fetch: the hash
    computed at first materialization is recorded into the local manifest
    (design §2); a registry value, once baked in, upgrades TOFU to verified
    and any mismatch is a hard :class:`~cas.core.exceptions.MirrorIntegrityError`."""
    size_bytes_approx: int = 0
    """Approximate download size, for status display and disk budgeting."""
    unit: str | None = None
    """Regional unit this source belongs to (RGI region ``"06"``, HydroBASINS
    ``"na_lev06"``, Pfaf ``"7"``, VPU ``"714"``, ``"conus"``); ``None`` for
    single-archive global datasets."""
    role: str = "data"
    """Distinguishes multiple files per unit (e.g. MERIT-Basins
    ``catchments`` / ``rivernet``; TDX ``catchments`` / ``streams``)."""


class MirrorDataset(BaseModel):
    """A version-pinned mirror registry entry (design §2: id = (slug, version))."""

    model_config = {"frozen": True}

    slug: str
    version: str
    """Upstream release string (e.g. ``"2.0"``); no "latest" alias exists."""
    display_name: str
    description: str = ""
    sources: list[MirrorSource]
    delivery: str = "subset"
    """``"subset"`` — converted to a GeoParquet query layer, served through
    :func:`cas.mirror_subset` (attribute vectors). ``"path"`` — materialized
    as topology-complete units whose verified local paths are delivered
    through :func:`cas.mirror_fetch`; **never** bbox-clipped (geofabrics,
    design §3: a bbox cannot guarantee upstream closure)."""
    unit_scheme: str = ""
    """Human description of the unit structure (e.g. ``"RGI first-order
    region"``, ``"continental region × Pfafstetter level"``, ``"VPU"``)."""
    unit_processing: str = "geoparquet"
    """How a downloaded unit becomes materialized files: ``"geoparquet"``
    (zip → shapefile → GeoParquet query layer), ``"gpkg"`` (zip → shapefile →
    GeoPackage, recorded as a conversion), ``"raw"`` (the download *is* the
    artifact, kept byte-identical), ``"tar_member"`` (tar.gz → extract the
    member matching ``shapefile_patterns``)."""
    dynamic_units: bool = False
    """True when units cannot be enumerated statically (TDX-Hydro VPUs come
    from the upstream vpu-boundaries index); sources are then built by a
    per-slug factory registered in :mod:`cas.mirror.datasets`."""
    units_required_for_sync: bool = False
    """True ⇒ ``cas mirror sync <slug>`` without explicit units is refused
    (TDX-Hydro: ~25–40 GB global; per-VPU lazy is mandatory, design §1)."""
    auth: str | None = None
    """``"earthdata"`` for NASA Earthdata-gated sources (RGI 7.0). The mirror
    fetches with the *user's* credentials; they are never stored in
    manifests (design §4)."""
    notice_file: str | None = None
    """Filename under ``cas/mirror/notices/`` of a verbatim license notice
    that must be copied next to every materialized unit and referenced in
    the manifest (HydroBASINS Exhibit B, design §8)."""
    disk_note: str = ""
    """Disk-cost warning surfaced by ``cas mirror sync``/``status`` *before*
    download (e.g. NWS: 1.6 GB archive expands to a ~7 GB GeoPackage)."""
    shapefile_patterns: list[str] = Field(default_factory=lambda: ["*.shp"])
    """Ordered, case-insensitive glob preferences selecting the vector layer
    inside the archive; the first pattern with matches wins, sidecar files
    sharing the stem are kept alongside."""
    source_crs_note: str = ""
    default_buffer_deg: float = 0.1
    """Default bbox buffer for subset queries (matches native handler behavior)."""
    known_columns: list[str] = Field(default_factory=list)
    """The "columns actually used" set documented in design §0 — what the
    SYMFLUENCE shim is expected to request. The mirror keeps ALL columns."""
    license: MirrorLicense
    citation: str = ""
    approx_materialized_bytes: int = 0

    @property
    def spec(self) -> str:
        """The pinned ``slug==version`` form."""
        return f"{self.slug}=={self.version}"

    @property
    def is_unit_structured(self) -> bool:
        """True when the dataset materializes per regional unit."""
        return self.dynamic_units or any(s.unit is not None for s in self.sources)

    def unit_ids(self) -> list[str]:
        """Statically declared unit ids, in registry order (deduplicated)."""
        seen: dict[str, None] = {}
        for source in self.sources:
            if source.unit is not None:
                seen.setdefault(source.unit)
        return list(seen)

    def sources_for_unit(self, unit: str) -> list[MirrorSource]:
        """Statically declared sources for one unit."""
        return [s for s in self.sources if s.unit == unit]


# ── Manifest records ────────────────────────────────────────────────


class ArchiveRecord(BaseModel):
    """Provenance of one downloaded source archive (kept even after the
    archive itself is deleted post-conversion)."""

    url: str
    archive_name: str
    sha256: str
    size_bytes: int
    sha256_source: str = "tofu"
    """``registry`` if verified against a shipped checksum, ``tofu`` if
    recorded trust-on-first-fetch."""
    unit: str | None = None


class FileRecord(BaseModel):
    """One materialized file under the dataset directory."""

    path: str
    """Relative to the dataset-version directory."""
    sha256: str
    size_bytes: int
    role: str = "query_layer"
    unit: str | None = None


class ConversionRecord(BaseModel):
    """Provenance of the mirror-time format conversion (design §2/§3)."""

    tool_versions: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, object] = Field(default_factory=dict)


class AcknowledgmentRecord(BaseModel):
    """Recorded acceptance of acknowledgment-requiring license terms."""

    license: str
    accepted_at: datetime
    accepted_via: str
    """``interactive`` | ``flag`` | ``env``."""


class MirrorManifest(BaseModel):
    """``manifest.json`` for one materialized dataset version (design §2 + §8)."""

    manifest_schema: int = MANIFEST_SCHEMA_VERSION
    slug: str
    version: str
    display_name: str = ""
    source_urls: list[str] = Field(default_factory=list)
    retrieved_at: datetime
    archives: list[ArchiveRecord] = Field(default_factory=list)
    files: list[FileRecord] = Field(default_factory=list)
    kept_members: list[str] = Field(default_factory=list)
    """Archive members extracted and converted (the rest were dropped)."""
    crs: str = ""
    feature_count: int = 0
    columns: list[str] = Field(default_factory=list)
    license: MirrorLicense
    citation: str = ""
    conversion: ConversionRecord | None = None
    acknowledgments: list[AcknowledgmentRecord] = Field(default_factory=list)
    units: list[str] | None = None
    """Materialized regional units for unit-structured datasets (RGI regions,
    Pfaf codes, VPUs — later slices); ``None`` for single-unit globals."""
    cas_version: str = ""


# ── Query results / status ──────────────────────────────────────────


class MirrorSubsetResult(BaseModel):
    """Result of a mirror bbox subset — a written GeoPackage path plus
    license/attribution passthrough (attribution obligations are satisfied
    mechanically, design §4)."""

    dataset_id: str
    slug: str
    version: str
    path: Path
    feature_count: int
    columns: list[str]
    crs: str
    bbox: tuple[float, float, float, float]
    """The requested bbox (EPSG:4326, pre-buffer)."""
    buffer_deg: float
    license: str = ""
    license_verified: bool = False
    license_flags: list[str] = Field(default_factory=list)
    attribution: str = ""
    citation: str = ""
    provenance: str = ""
    elapsed_ms: int = 0


class MirrorFetchResult(BaseModel):
    """Result of a path-delivery fetch (design §3, geofabric contract).

    CAS materializes the **topology-complete versioned unit** and delivers
    verified local paths — no bbox clipping, no upstream tracing (those stay
    in the consumer, e.g. SYMFLUENCE's ``GeofabricSubsetter``).
    """

    dataset_id: str
    slug: str
    version: str
    unit: str
    paths: dict[str, Path]
    """Role → path (e.g. MERIT ``{"catchments": ..., "rivernet": ...}``)."""
    path: Path
    """Primary path: the single data file, or the first role for multi-file
    units (all paths are in :attr:`paths`)."""
    format: str
    """Materialized format(s), e.g. ``"gpkg"``, ``"geoparquet"``,
    ``"geoparquet+gpkg"`` — what the consumer's reader will open."""
    notice_path: Path | None = None
    """Verbatim license notice copied next to the unit, when required."""
    license: str = ""
    license_verified: bool = False
    license_flags: list[str] = Field(default_factory=list)
    attribution: str = ""
    citation: str = ""
    disclaimer: str = ""
    provenance: str = ""
    elapsed_ms: int = 0


class MirrorDatasetStatus(BaseModel):
    """Per-dataset status row for ``cas mirror status``."""

    slug: str
    version: str
    display_name: str = ""
    materialized: bool = False
    disk_bytes: int = 0
    license: str = ""
    license_flags: list[str] = Field(default_factory=list)
    checksum_state: str = "unverified"
    """``registry-verified`` | ``tofu`` | ``unverified`` (not materialized)."""
    path: str = ""
    delivery: str = "subset"
    units_total: int | None = None
    """Statically known unit count (``None`` for single-unit globals and
    dynamic-unit datasets like TDX)."""
    units_materialized: list[str] = Field(default_factory=list)
    disk_note: str = ""
