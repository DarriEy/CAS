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

For talking to a *deployed* CAS service over HTTP, use :mod:`cas.client`,
which returns these same response models.
"""

from cas.api_sync import batch_extract_sync, extract_sync
from cas.core.config import configure
from cas.core.models import (
    AggregationMethod,
    AttributeRequest,
    AttributeResponse,
    AttributeResult,
    BatchAttributeRequest,
    BatchAttributeResponse,
    Geometry,
    QualityFlag,
    TimeRange,
)
from cas.core.registry import discover, get_connector, list_providers
from cas.extract.engine import batch_extract, extract

__version__ = "0.3.0"

__all__ = [
    "__version__",
    # Engine entry points (async) and sync wrappers
    "extract",
    "batch_extract",
    "extract_sync",
    "batch_extract_sync",
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
    "Geometry",
    "TimeRange",
    "AggregationMethod",
    "QualityFlag",
]
