# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""CAS — Community Attribute Service.

The names exported here are the blessed public Python API for embedding CAS
in-process (no HTTP service required):

>>> import cas
>>> request = cas.BatchAttributeRequest(
...     geometries=[{"type": "Point", "coordinates": [-96.5, 39.0]}],
...     dataset_ids=["copernicus_dem:elevation"],
... )
>>> batch = cas.batch_extract_sync(request)
>>> for resp in batch.responses:
...     for r in resp.results:
...         print(r.dataset_id, r.value, r.units, r.quality)

``extract`` / ``batch_extract`` are the async engine entry points;
``extract_sync`` / ``batch_extract_sync`` are blocking wrappers. Use
``cas.configure(**overrides)`` to (re)configure settings after import.
Connector modules are imported lazily on first ``discover()``/``extract()``
call, so ``import cas`` stays light.

Raster mode (in-process **only** — the HTTP API stays stats-only by design):

>>> result = cas.extract_raster_sync(
...     "copernicus_dem:elevation",
...     bbox=(-112.05, 41.95, -111.95, 42.05),
...     output_dir="/tmp/rasters",
... )
>>> result.path, result.crs, result.shape, result.nodata

``extract_raster`` / ``extract_raster_sync`` write a domain-bbox,
tile-mosaicked, native-resolution, EPSG:4326 GeoTIFF with correct nodata to
the caller-supplied directory and return a :class:`RasterResult` carrying
the path — the contract SYMFLUENCE discretization consumes.

Curated-mirror tier (in-process **only** — the HTTP API mounts no mirror
endpoints): version-pinned, checksummed local mirrors of bulk-download-only
vector datasets (GLHYMPS, HydroLAKES, WOKAM), materialized on *your* disk
under ``CAS_MIRROR_DIR`` on first use or via ``cas mirror sync``:

>>> result = cas.mirror_subset_sync(
...     "wokam",
...     bbox=(9.0, 46.0, 13.0, 47.5),
...     output_dir="/tmp/karst",
... )
>>> result.path, result.feature_count, result.attribution

For talking to a *deployed* CAS service over HTTP, use :mod:`cas.client`,
which returns these same response models (stats only; neither raster mode
nor mirrors are served over HTTP).
"""

from cas.api_sync import batch_extract_sync, extract_raster_sync, extract_sync
from cas.core.config import configure
from cas.core.models import (
    AggregationMethod,
    AttributeRequest,
    AttributeResponse,
    AttributeResult,
    BatchAttributeRequest,
    BatchAttributeResponse,
    BoundingBox,
    Geometry,
    OutputMode,
    QualityFlag,
    RasterResampling,
    RasterResult,
    TimeRange,
)
from cas.core.registry import discover, get_connector, list_providers
from cas.extract.engine import batch_extract, extract, extract_raster
from cas.mirror.fetch import mirror_fetch, mirror_fetch_sync
from cas.mirror.importer import mirror_import, mirror_import_sync
from cas.mirror.models import MirrorFetchResult, MirrorImportResult, MirrorSubsetResult
from cas.mirror.query import mirror_subset, mirror_subset_sync

__version__ = "0.3.0"

__all__ = [
    "__version__",
    # Engine entry points (async) and sync wrappers
    "extract",
    "batch_extract",
    "extract_raster",
    "extract_sync",
    "batch_extract_sync",
    "extract_raster_sync",
    # Curated mirror tier (in-process only)
    "mirror_subset",
    "mirror_subset_sync",
    "mirror_fetch",
    "mirror_fetch_sync",
    "mirror_import",
    "mirror_import_sync",
    "MirrorSubsetResult",
    "MirrorFetchResult",
    "MirrorImportResult",
    # Runtime configuration
    "configure",
    # Provider registry
    "discover",
    "list_providers",
    "get_connector",
    # Request / response models
    "AttributeRequest",
    "BatchAttributeRequest",
    "AttributeResponse",
    "BatchAttributeResponse",
    "AttributeResult",
    "RasterResult",
    "Geometry",
    "BoundingBox",
    "TimeRange",
    "AggregationMethod",
    "OutputMode",
    "RasterResampling",
    "QualityFlag",
]
