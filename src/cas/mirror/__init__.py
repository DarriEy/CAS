# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Curated-mirror tier: version-pinned, checksummed local mirrors of
bulk-download-only vector datasets, plus subset queries over them.

Storage lives on the **user's** disk (``CAS_MIRROR_DIR``); CAS is only ever
a download client + local subsetter — never a host or redistributor. The
HTTP API deliberately mounts no mirror endpoints.

Module boundaries (designed so later slices slot in cleanly):

- :mod:`cas.mirror.datasets` — the shipped registry; RGI per-region units
  and geofabric path-delivery datasets become new entries here.
- :mod:`cas.mirror.store` — materialization, manifests, locking, integrity;
  ``cas mirror sync/status/verify/remove`` are thin CLI wrappers over it.
- :mod:`cas.mirror.convert` — mirror-time GeoParquet conversion.
- :mod:`cas.mirror.query` — bbox subset over the converted query layers.
  (A ``fetch()`` for unit/path-delivery datasets and a GLHYMPS ``extract()``
  stats hook land beside it in later slices.)

In-process usage::

    import cas
    result = cas.mirror_subset_sync(
        "wokam", bbox=(9.0, 46.0, 13.0, 47.5), output_dir="/tmp/karst",
    )
    result.path, result.feature_count, result.attribution
"""

from cas.mirror.datasets import (
    get_mirror_dataset,
    list_mirror_datasets,
    parse_dataset_spec,
    parse_unit_spec,
    register_mirror_dataset,
    register_unit_source_factory,
    sources_for_unit,
    unregister_mirror_dataset,
)
from cas.mirror.fetch import mirror_fetch, mirror_fetch_sync
from cas.mirror.models import (
    MirrorDataset,
    MirrorDatasetStatus,
    MirrorFetchResult,
    MirrorLicense,
    MirrorManifest,
    MirrorSource,
    MirrorSubsetResult,
)
from cas.mirror.query import mirror_subset, mirror_subset_sync
from cas.mirror.store import (
    dataset_dir,
    ensure_materialized,
    ensure_units_materialized,
    is_materialized,
    is_unit_materialized,
    load_manifest,
    manifest_path,
    query_layer_path,
    remove,
    status,
    unit_dir,
    unit_notice_path,
    unit_paths,
    verify,
)
from cas.mirror.units import register_unit_resolver, units_for_bbox

__all__ = [
    # Query facade
    "mirror_subset",
    "mirror_subset_sync",
    # Path-delivery facade (geofabrics)
    "mirror_fetch",
    "mirror_fetch_sync",
    # Registry
    "get_mirror_dataset",
    "list_mirror_datasets",
    "parse_dataset_spec",
    "parse_unit_spec",
    "register_mirror_dataset",
    "register_unit_source_factory",
    "sources_for_unit",
    "unregister_mirror_dataset",
    # Units
    "register_unit_resolver",
    "units_for_bbox",
    # Store
    "dataset_dir",
    "ensure_materialized",
    "ensure_units_materialized",
    "is_materialized",
    "is_unit_materialized",
    "load_manifest",
    "manifest_path",
    "query_layer_path",
    "remove",
    "status",
    "unit_dir",
    "unit_notice_path",
    "unit_paths",
    "verify",
    # Models
    "MirrorDataset",
    "MirrorDatasetStatus",
    "MirrorFetchResult",
    "MirrorLicense",
    "MirrorManifest",
    "MirrorSource",
    "MirrorSubsetResult",
]
