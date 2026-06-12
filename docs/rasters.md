# Raster mode (in-process)

CAS can hand back the *gridded data itself* — not just zonal statistics —
as a GeoTIFF written to a directory you supply. This is the **in-process
raster mode**: it exists only in the embedded Python API
(`cas.extract_raster_sync` / `await cas.extract_raster`) and is deliberately
absent from the HTTP service.

```python
import cas

result = cas.extract_raster_sync(
    "copernicus_dem:elevation",
    bbox=(-112.05, 41.95, -111.95, 42.05),   # (min_lon, min_lat, max_lon, max_lat)
    output_dir="/path/to/rasters",
)

print(result.path)       # .../copernicus_dem_elevation.tif
print(result.crs)        # EPSG:4326
print(result.shape)      # (rows, cols)
print(result.nodata)     # source nodata, carried through
print(result.license)    # provider license string
print(result.provenance) # e.g. "STAC mosaic: cop-dem-glo-30 (2 items: ...)"
```

The returned [`RasterResult`](reference.md) carries the **path**, never
bytes — the file on disk is yours; CAS keeps nothing.

## The contract

Raster mode exists to feed raster consumers like SYMFLUENCE's domain
discretization (DEM-based elevation bands, land-cover classes). What those
consumers get is exactly:

> a **domain-bbox, tile-mosaicked, native-resolution, EPSG:4326 GeoTIFF
> with correct nodata at a path**.

Concretely:

- **Domain-bbox**: requests are bbox-mode — a rectangular domain, not a
  per-HRU geometry. The output raster is clipped to that bbox.
- **Tile-mosaicked**: for STAC/COG sources, *all* catalog items intersecting
  the bbox are merged (windowed `rasterio.merge` of per-item bbox windows).
  A basin straddling Copernicus DEM 1° tile boundaries comes back as one
  seamless raster — never a single-tile truncation.
- **Native resolution**: the source grid is preserved by default.
  `target_resolution` (in source-CRS units, i.e. degrees for EPSG:4326)
  plus `resampling` (`nearest`/`bilinear`/`cubic`/`average`) opt into
  coarsening for STAC sources.
- **Correct nodata**: the source nodata value is carried into the output
  band and its metadata; gaps at tile seams stay nodata instead of leaking
  fill values.

## Why the HTTP API excludes it

`POST /api/v1/extract` rejects `output="raster"` at request validation with
a message pointing here. That is enforced policy, not a missing feature:

- **Service identity.** CAS the service is a stateless, no-storage
  *attribute* passthrough — scalar statistics in, scalar statistics out.
  Serving rasters would make it a tile server with storage, bandwidth, and
  caching obligations it is designed not to have.
- **Licensing posture.** Several upstream rasters (e.g. Copernicus DEM via
  Planetary Computer signed URLs) are fine for *end-user access* but not
  for third-party *re-serving*. In-process library use means the end user
  fetches data from the provider under the provider's terms; a CAS
  deployment re-serving those bytes would be redistribution. The license
  string is carried on every `RasterResult` so downstream tooling can keep
  honoring it.

## v1 scope

- **Protocols**: STAC/COG (mosaic) and WCS (native-resolution GetCoverage
  passthrough) only. Every other protocol declares
  `supports_raster = False` and raster requests fail with a clear
  `RasterUnsupportedError`.
- **CRS**: native-CRS passthrough. `target_crs` must stay `"EPSG:4326"`
  (the native CRS of the supported sources); reprojection is not
  implemented in v1 and is rejected at validation.
- **One dataset per call**: a raster request takes exactly one
  `dataset_id` and produces exactly one GeoTIFF.
- **WCS**: `target_resolution` is not supported (the passthrough keeps the
  server's native grid).

Raster-capable providers in v1:

| Provider | Protocol | Notes |
|---|---|---|
| `copernicus_dem` | STAC/COG | GLO-30, Planetary Computer (signed) |
| `cop_dem_90` | STAC/COG | GLO-90, Planetary Computer (signed) |
| `usgs_3dep` | STAC/COG | US 10 m, Planetary Computer (signed) |
| `nasadem` | STAC/COG | Planetary Computer (signed) |
| `alos_dem` | STAC/COG | Planetary Computer (signed) |
| `esa_worldcover` | STAC/COG | land cover (`map` asset) |
| `tandem_x` | WCS | requires DLR credentials |

Any other COG-backed connector can be wired by declaring two class
attributes (`supports_raster = True`,
`stac_raster_collections = ("<collection>",)`) — the STAC mixin provides the
full mosaic implementation.

## Engine semantics (vs. the stats path)

The raster path intentionally bypasses two stats-engine layers:

- **No result cache**: arrays don't belong in the JSON TTL cache; every
  call re-reads the source, and persistence is the caller's `output_dir`.
- **No scalar QC**: quality flags and cross-provider consistency checks are
  statistics concepts. Raster failures **raise** (`ProtocolError`,
  `DataFormatError`, `RasterUnsupportedError`, `ExtractionError` on
  timeout) instead of degrading into response warnings.

Request validation still applies (bbox sanity, single dataset, v1 CRS
limitation), as does the per-provider timeout.

## Async variant

```python
result = await cas.extract_raster(
    "copernicus_dem:elevation",
    bbox=(-112.05, 41.95, -111.95, 42.05),
    output_dir="/path/to/rasters",
    target_resolution=None,     # native by default
    resampling="nearest",
    filename="my_dem.tif",      # optional; defaults to {provider}_{dataset}.tif
)
```
