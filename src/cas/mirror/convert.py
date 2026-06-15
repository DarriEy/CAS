# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Mirror-time GeoParquet conversion (design §3, option 3B).

The extracted vector layer is rewritten once, at materialization time, as a
hilbert-sorted GeoParquet file with a per-row-group bbox covering column
(GeoParquet 1.1). Subsequent bbox subset reads prune row groups instead of
scanning the whole layer — sub-second for typical domains — and the parquet
is ~3x smaller than the shapefile, which lets the store drop both the
extracted shapefile and the archive (source checksums stay in the manifest).

geopandas/pyogrio/pyarrow are **not** CAS core dependencies; they ship in
the ``[mirror]`` optional extra and are imported lazily here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cas.core.exceptions import MirrorDependencyError
from cas.mirror.models import ConversionRecord

ROW_GROUP_SIZE = 65_536
GEOPARQUET_SCHEMA_VERSION = "1.1.0"


def _import_geopandas() -> Any:
    try:
        import geopandas
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise MirrorDependencyError(
            "The mirror tier needs geopandas/pyogrio/pyarrow, which are not "
            "installed. Install the optional extra: "
            "pip install 'community-attribute-service[mirror]'"
        ) from exc
    return geopandas


def convert_to_geoparquet(
    source: Path,
    dest: Path,
    *,
    row_group_size: int = ROW_GROUP_SIZE,
    assumed_crs: str = "",
) -> tuple[ConversionRecord, int, list[str], str]:
    """Convert a vector layer to hilbert-sorted GeoParquet.

    Writes to a ``dest.part`` temp file and atomically renames, so a crashed
    conversion never leaves a plausible-looking query layer behind.

    ``assumed_crs`` is assigned (never reprojected) when the source layer
    ships without a CRS (e.g. MERIT-Basins catchment shapefiles have no
    ``.prj``); the assumption is recorded in the conversion provenance.

    Returns ``(conversion_record, feature_count, columns, crs)`` — the
    provenance and layer facts recorded in the manifest.
    """
    gpd = _import_geopandas()
    import pyarrow
    import pyogrio

    gdf = gpd.read_file(source)
    crs_assumed = False
    if gdf.crs is None and assumed_crs:
        gdf = gdf.set_crs(assumed_crs)
        crs_assumed = True
    sort = "none"
    if len(gdf) > 1:
        try:
            order = gdf.geometry.hilbert_distance().argsort()
            gdf = gdf.iloc[order].reset_index(drop=True)
            sort = "hilbert"
        except Exception:  # noqa: BLE001 — sorting is an optimization, not a requirement
            sort = "none (hilbert_distance failed; row-group pruning still applies)"

    tmp = dest.with_name(dest.name + ".part")
    gdf.to_parquet(
        tmp,
        index=False,
        compression="snappy",
        schema_version=GEOPARQUET_SCHEMA_VERSION,
        write_covering_bbox=True,
        row_group_size=row_group_size,
    )
    tmp.replace(dest)

    columns = [c for c in gdf.columns if c != gdf.geometry.name]
    crs = str(gdf.crs) if gdf.crs is not None else ""
    record = ConversionRecord(
        tool_versions={
            "geopandas": gpd.__version__,
            "pyogrio": pyogrio.__version__,
            "pyarrow": pyarrow.__version__,
        },
        parameters={
            "format": "GeoParquet",
            "schema_version": GEOPARQUET_SCHEMA_VERSION,
            "sort": sort,
            "row_group_size": row_group_size,
            "write_covering_bbox": True,
            "compression": "snappy",
            "source_format": source.suffix.lstrip(".") or "unknown",
            **({"assumed_crs": assumed_crs} if crs_assumed else {}),
        },
    )
    return record, len(gdf), columns, crs
