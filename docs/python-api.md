# Python API (embedded)

`import cas` exposes the blessed public API for running CAS **in-process** —
no HTTP service required. This is the supported interface for embedding CAS in
other frameworks (e.g. a SYMFLUENCE adapter) and for scripted/notebook use.
Everything you need is re-exported at the package root, with an explicit
`__all__` defining the stable surface.

```python
import cas
print(cas.__version__, cas.__all__)
```

!!! note "In-process vs. deployed service"
    The HTTP client ([`cas.client`](sdk.md)) is the alternative for talking to
    a *deployed* CAS service. Both interfaces return the same canonical
    response models (`AttributeResponse`, `AttributeResult`, …), so code that
    consumes results is portable between the two.

## Quickstart

Build a request, extract, iterate results:

```python
import cas

request = cas.BatchAttributeRequest(
    geometries=[
        {"type": "Point", "coordinates": [-96.5, 39.0]},
        {"type": "Polygon", "coordinates": [[
            [-96.6, 39.0], [-96.5, 39.0], [-96.5, 39.1],
            [-96.6, 39.1], [-96.6, 39.0],
        ]]},
    ],
    dataset_ids=["copernicus_dem:elevation", "isric_soilgrids:clay_0-5cm"],
    aggregation="mean",
)

batch = cas.batch_extract_sync(request)

for resp in batch.responses:
    for r in resp.results:
        print(r.dataset_id, r.value, r.units, r.quality, r.coverage_fraction)
    for w in resp.warnings:
        print("warning:", w)
```

For a single geometry, use `cas.AttributeRequest` with `cas.extract_sync`.
Geometries accept GeoJSON dicts or `cas.Geometry` instances; per-result
`quality` is a `cas.QualityFlag` enum.

### Sync vs. async

`extract_sync` / `batch_extract_sync` are blocking wrappers that run the async
engine to completion via `asyncio.run()`. They raise a clear `RuntimeError` if
called from inside a running event loop — in async code, await the engine
coroutines directly:

```python
response = await cas.extract(request)        # single geometry
batch = await cas.batch_extract(batch_req)   # many geometries
```

## Configuration: `cas.configure()`

CAS reads its settings from `CAS_`-prefixed environment variables, cached on
first use. That caching has a caveat for embedders: **environment variables set
after CAS has already been used are silently ignored.** `cas.configure()`
solves this — it clears the settings cache and applies programmatic overrides,
so a host framework can configure CAS at any point after import:

```python
import cas

cas.configure(
    provider_timeout_s=60,     # slower upstreams allowed
    result_cache_ttl_s=3600,   # cache extraction results for an hour
)
```

- Keyword arguments name [`Settings`](api.md#configuration) fields directly,
  are validated by pydantic, and take precedence over the environment.
- `cas.configure()` with **no** arguments just re-reads the current
  environment (useful if env vars were set late).
- Overrides accumulate across calls; the engine's result cache is rebuilt
  against the refreshed settings on next use.
- Unknown setting names raise `TypeError`.

## Catalog discovery

Connector modules are imported lazily — `import cas` stays light, and
providers are registered on the first `discover()` (or `extract*`) call:

```python
import cas

cas.discover()
print(len(cas.list_providers()), "providers")

connector_cls = cas.get_connector("copernicus_dem")
```

## Raster mode (in-process only)

Beyond scalar statistics, the embedded API can extract the gridded data
itself as a GeoTIFF written to a caller-supplied directory:

```python
result = cas.extract_raster_sync(
    "copernicus_dem:elevation",
    bbox=(-112.05, 41.95, -111.95, 42.05),
    output_dir="/path/to/rasters",
)
print(result.path, result.crs, result.shape, result.nodata)
```

This capability is **not** served over the HTTP API (the service is
stats-only by identity and licensing posture) — see
[Raster Mode (in-process)](rasters.md) for the contract, the mosaic
semantics, and the v1 scope (STAC/COG + WCS, native CRS).

## Stable surface

The facade exports: `extract`, `batch_extract`, `extract_raster`,
`extract_sync`, `batch_extract_sync`, `extract_raster_sync`, `configure`,
`discover`, `list_providers`, `get_connector`, and the core models
`AttributeRequest`, `BatchAttributeRequest`, `AttributeResponse`,
`BatchAttributeResponse`, `AttributeResult`, `RasterResult`, `Geometry`,
`BoundingBox`, `TimeRange`, `AggregationMethod`, `OutputMode`,
`RasterResampling`, `QualityFlag`, plus `__version__`.

Deeper modules (`cas.extract.zonal`, `cas.connectors.*`, …) are importable but
not covered by the same stability promise. See the
[SDK Reference](reference.md) for generated API docs.
