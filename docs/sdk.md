# Python SDK (HTTP client)

`cas.client` is a thin, typed wrapper over the [HTTP API](api.md) of a
**deployed** CAS service. It ships with the core package (no extra needed) and
returns the same canonical `cas.core.models` types the service uses.

!!! tip "Embedding CAS instead?"
    To run CAS in-process — no service to deploy — use the
    [Python API](python-api.md) (`import cas`). It returns the same response
    models, so downstream code is portable between the two.

Both a synchronous (`CASClient`) and an asynchronous (`AsyncCASClient`) client
are available with identical method surfaces.

## Connect

```python
from cas.client import CASClient

cas = CASClient(
    "https://cas.example.com",   # base URL of a running service
    api_key="…",                  # optional; only if the service has auth on
    timeout=120.0,
)
```

Use it as a context manager so the underlying HTTP connection pool is closed:

```python
with CASClient("http://localhost:8000") as cas:
    ...
```

## Extract

```python
resp = cas.extract(
    geometry={"type": "Point", "coordinates": [-96.5, 39.0]},
    dataset_ids=["copernicus_dem:elevation", "isric_soilgrids:clay_0-5cm"],
    aggregation="mean",
)
for r in resp.results:
    print(r.dataset_id, r.value, r.units, r.quality)
```

`geometry` accepts a `Geometry`, a GeoJSON geometry dict, or a GeoJSON
**Feature** (its `geometry` is unwrapped automatically).

### Time-varying datasets

```python
resp = cas.extract(
    geometry=poly,
    dataset_ids=["modis_lc:land_cover"],
    aggregation="majority",
    time_range=("2020-01-01", "2020-12-31"),   # or a TimeRange instance
)
```

### Batch

```python
batch = cas.batch_extract(
    geometries=[geom_a, geom_b, geom_c],
    dataset_ids=["copernicus_dem:elevation"],
)
print(batch.total_results)
```

## Discover the catalog

```python
# All providers (paginate with limit/offset)
for p in cas.providers(limit=1000).providers:
    print(p.slug, p.protocol, p.base_url)

# One provider with full dataset metadata
detail = cas.provider("copernicus_dem")
for ds in detail.datasets:
    print(ds.id, ds.resolution_m, ds.bbox, ds.license)

# Datasets across all providers (or one)
ds_page = cas.datasets(provider="esa_worldcover")
```

## Error handling

Any non-2xx response raises `CASError`, which carries the parsed error envelope:

```python
from cas.client import CASError

try:
    cas.extract(geometry=pt, dataset_ids=[])
except CASError as e:
    print(e.status_code, e.error_type, e.message, e.request_id)
```

## Async

```python
import asyncio
from cas.client import AsyncCASClient

async def main():
    async with AsyncCASClient("http://localhost:8000") as cas:
        resp = await cas.extract(
            geometry={"type": "Point", "coordinates": [-96.5, 39.0]},
            dataset_ids=["copernicus_dem:elevation"],
        )
        print(resp.results[0].value)

asyncio.run(main())
```

See the [SDK Reference](reference.md) for the full generated API.
