# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Shared helpers for the mirror-tier tests.

Everything here is hermetic: tiny synthetic shapefiles zipped in memory and
served through respx — never a real download.
"""

from __future__ import annotations

import contextlib
import io
import zipfile
from pathlib import Path

from cas.mirror.datasets import register_mirror_dataset, unregister_mirror_dataset
from cas.mirror.models import MirrorDataset, MirrorLicense, MirrorSource

FAKE_URL = "https://mirror.invalid/fakeveg.zip"
FAKE_COLUMNS = ["fid_int", "name", "value"]

# Synthetic features (EPSG:4326 lon/lat) chosen around query bbox (2,2,4,4):
#   inside       box(0, 0, 1, 1)       — inside bbox (-0.5,-0.5,2,2) tests
#   straddle     box(3.9, 3.9, 4.5, 4.5) — straddles the (2,2,4,4) edge
#   buffer_only  box(4.2, 4.2, 5, 5)   — outside (2,2,4,4), inside +0.5 buffer
#   far          box(8, 8, 9, 9)       — never matched
FEATURE_NAMES = ["inside", "straddle", "buffer_only", "far"]


def build_fake_zip(tmp_path: Path, *, crs: str = "EPSG:4326", shp_name: str = "data") -> bytes:
    """A tiny zip holding one 4-polygon shapefile plus decoy members."""
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {
            "fid_int": [1, 2, 3, 4],
            "name": FEATURE_NAMES,
            "value": [1.0, 2.0, 3.0, 4.0],
            "geometry": [
                box(0.0, 0.0, 1.0, 1.0),
                box(3.9, 3.9, 4.5, 4.5),
                box(4.2, 4.2, 5.0, 5.0),
                box(8.0, 8.0, 9.0, 9.0),
            ],
        },
        crs="EPSG:4326",
    )
    if crs != "EPSG:4326":
        gdf = gdf.to_crs(crs)

    build_dir = tmp_path / f"_zip_build_{shp_name}_{crs.replace(':', '_')}"
    build_dir.mkdir(parents=True, exist_ok=True)
    gdf.to_file(build_dir / f"{shp_name}.shp")

    decoy = gpd.GeoDataFrame(
        {"a": [1], "geometry": [box(0.0, 0.0, 0.1, 0.1)]}, crs="EPSG:4326"
    )
    decoy.to_file(build_dir / "cave_points.shp")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted(build_dir.iterdir()):
            zf.write(f, arcname=f"payload/{f.name}")
        zf.writestr("payload/README.txt", "decoy member, must not be kept")
    return buf.getvalue()


def _fake_gdf(*, crs: str = "EPSG:4326"):
    import geopandas as gpd
    from shapely.geometry import box

    return gpd.GeoDataFrame(
        {
            "fid_int": [1, 2, 3, 4],
            "name": FEATURE_NAMES,
            "value": [1.0, 2.0, 3.0, 4.0],
            "geometry": [
                box(0.0, 0.0, 1.0, 1.0),
                box(3.9, 3.9, 4.5, 4.5),
                box(4.2, 4.2, 5.0, 5.0),
                box(8.0, 8.0, 9.0, 9.0),
            ],
        },
        crs=crs,
    )


def build_fake_gpkg_bytes(tmp_path: Path, *, layer: str = "data") -> bytes:
    """A tiny single-layer GeoPackage as raw bytes (TDX streams, raw mode)."""
    out = tmp_path / f"_gpkg_{layer}.gpkg"
    _fake_gdf().to_file(out, driver="GPKG", layer=layer)
    return out.read_bytes()


def build_fake_parquet_bytes(tmp_path: Path) -> bytes:
    """A tiny GeoParquet as raw bytes (TDX catchments, raw mode)."""
    out = tmp_path / "_catch.parquet"
    _fake_gdf().to_parquet(out)
    return out.read_bytes()


def build_fake_gpkg_tar(tmp_path: Path, *, member: str = "conus_nextgen.gpkg") -> bytes:
    """A .tar.gz holding one GeoPackage member (NWS tar_member mode)."""
    import io
    import tarfile

    gpkg = tmp_path / "_tar_src.gpkg"
    _fake_gdf().to_file(gpkg, driver="GPKG", layer="divides")
    raw = gpkg.read_bytes()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name=f"conus_nextgen/{member}")
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
        readme = b"decoy"
        rinfo = tarfile.TarInfo(name="conus_nextgen/README.txt")
        rinfo.size = len(readme)
        tf.addfile(rinfo, io.BytesIO(readme))
    return buf.getvalue()


def make_fake_dataset(
    *,
    slug: str = "fakeveg",
    version: str = "1.0",
    url: str = FAKE_URL,
    sha256: str | None = None,
    requires_acknowledgment: bool = False,
    default_buffer_deg: float = 0.5,
) -> MirrorDataset:
    return MirrorDataset(
        slug=slug,
        version=version,
        display_name="Fake Vegetation Test Dataset",
        sources=[
            MirrorSource(
                url=url,
                archive_name=f"{slug}.zip",
                sha256=sha256,
                size_bytes_approx=10_000,
            )
        ],
        shapefile_patterns=["*data*.shp"],
        default_buffer_deg=default_buffer_deg,
        known_columns=FAKE_COLUMNS,
        license=MirrorLicense(
            license="FAKE-ACK-1.0" if requires_acknowledgment else "CC-BY-4.0",
            license_url="https://mirror.invalid/license",
            license_verified=not requires_acknowledgment,
            attribution="Fake Data Consortium 2026",
            license_flags=["unconfirmed"] if requires_acknowledgment else [],
            requires_acknowledgment=requires_acknowledgment,
        ),
        citation="Fake et al. (2026). A synthetic test dataset.",
    )


def make_fake_unit_dataset(
    *,
    slug: str = "fakeunits",
    version: str = "1.0",
    units: tuple[str, ...] = ("a", "b"),
    url_template: str = "https://mirror.invalid/{slug}_{unit}.zip",
    unit_processing: str = "geoparquet",
    delivery: str = "subset",
    auth: str | None = None,
    requires_acknowledgment: bool = False,
    units_required_for_sync: bool = False,
    notice_file: str | None = None,
    roles: tuple[str, ...] = ("data",),
) -> MirrorDataset:
    """A unit-structured dataset with one source per (unit, role)."""
    return MirrorDataset(
        slug=slug,
        version=version,
        display_name="Fake Unit-Structured Test Dataset",
        sources=[
            MirrorSource(
                url=url_template.format(slug=slug, unit=unit) + (
                    f"?role={role}" if role != "data" else ""
                ),
                archive_name=f"{slug}_{unit}" + (f"_{role}" if role != "data" else "") + ".zip",
                sha256=None,
                size_bytes_approx=10_000,
                unit=unit,
                role=role,
            )
            for unit in units
            for role in roles
        ],
        delivery=delivery,
        unit_scheme="test unit",
        unit_processing=unit_processing,
        auth=auth,
        units_required_for_sync=units_required_for_sync,
        notice_file=notice_file,
        shapefile_patterns=["*data*.shp"],
        default_buffer_deg=0.0,
        known_columns=FAKE_COLUMNS,
        license=MirrorLicense(
            license="FAKE-ACK-1.0" if requires_acknowledgment else "CC-BY-4.0",
            license_url="https://mirror.invalid/license",
            license_verified=not requires_acknowledgment,
            attribution="Fake Data Consortium 2026",
            license_flags=["unconfirmed"] if requires_acknowledgment else [],
            requires_acknowledgment=requires_acknowledgment,
        ),
        citation="Fake et al. (2026). Accessed {access_date}.",
    )


@contextlib.contextmanager
def registered(dataset: MirrorDataset):
    """Register a dataset in the mirror registry for the duration of a test."""
    register_mirror_dataset(dataset)
    try:
        yield dataset
    finally:
        unregister_mirror_dataset(dataset.slug, dataset.version)
