# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-06-11

### Added

- **In-process raster mode** (`output="raster"`): bbox-mode extraction of
  the gridded data itself as a GeoTIFF, via the new embedded entry points
  `cas.extract_raster(dataset_id, bbox, output_dir, ...)` and
  `cas.extract_raster_sync(...)`, returning a new `RasterResult` model
  (provider, dataset_id, crs, transform/shape, nodata, license, provenance,
  **path** — never bytes). The written file honors the discretization-grade
  contract SYMFLUENCE consumes: a domain-bbox, tile-mosaicked,
  native-resolution, EPSG:4326 GeoTIFF with correct nodata.
  - `AttributeRequest` gains `output` ("stats" default, behavior unchanged),
    `bbox` (rectangular domain, raster-only), `target_resolution`, and
    `resampling`; a model validator keeps the two shapes mutually
    exclusive. v1 limitation enforced at validation: raster output is a
    native-CRS passthrough (`target_crs` must stay `EPSG:4326`).
  - **STAC mosaic**: the STAC mixin's raster path merges *all* catalog
    items intersecting the bbox (windowed `rasterio.merge` of per-item
    bbox windows) instead of the stats path's single-item truncation — a
    basin spanning Copernicus DEM 1° tiles comes back as one seamless
    raster, bbox-clipped, native-resolution, nodata carried through. Wired
    on `copernicus_dem`, `cop_dem_90`, `usgs_3dep`, `nasadem`, `alos_dem`,
    `esa_worldcover`; any other COG-backed connector opts in with two
    class attributes.
  - **WCS passthrough**: native-resolution GetCoverage GeoTIFF written
    verbatim and validated on open; wired on `tandem_x`.
  - Capability gating: `BaseConnector.supports_raster` defaults to False;
    non-STAC/WCS protocols fail raster requests with a clear
    `RasterUnsupportedError` (new exception).
  - The engine raster path bypasses the JSON TTL result cache and the
    scalar QC layer; failures raise instead of degrading into warnings.
  - **HTTP exclusion**: the FastAPI layer rejects `output="raster"` at
    request validation (422) with a message pointing at the in-process
    facade — CAS the service stays a stats-only, no-storage passthrough
    and never redistributes provider rasters.
  - New "Raster Mode (in-process)" documentation page (`docs/rasters.md`).
- **SYMFLUENCE integration** (`cas.integrations.symfluence`), wired at two
  seams:
  - *Primary*: a `CASAttributeProcessor` attribute processor registered
    under the `symfluence.attribute_processors` entry point. SYMFLUENCE's
    attribute machinery discovers it, constructs it `(config, logger)`, and
    merges its `.process() -> dict` output into the same per-HRU results the
    in-tree elevation/soil/climate processors feed — `cas.{dataset}` keys
    for lumped domains, `HRU_{id}_cas.{dataset}` for distributed ones, with
    `*_quality` / `*_coverage_fraction` metadata riding along. Reads
    `CAS_DATASETS` (required opt-in), `CAS_AGGREGATION` (default `mean`) and
    `CAS_API_CONFIG`; extracts via chunked `cas.batch_extract_sync` calls
    (≤1000 geometries per request) over the domain's HRU polygons.
  - *Secondary*: a `CASAttributeAcquirer` acquisition handler registered
    under `'CAS'` via the `symfluence.plugins` entry point for explicit use,
    writing an analysis-oriented per-HRU CSV
    (`{DOMAIN_NAME}_cas_attributes.csv`, by convention into
    `data/attributes/cas/`). It is not auto-appended to SYMFLUENCE's
    built-in attribute profiles — the processor seam supersedes that.

  Both seams are strict no-ops until `CAS_DATASETS` is set, and the module
  imports defensively, so `import cas` works without SYMFLUENCE installed
  and adds no new dependency.
- New "SYMFLUENCE Plugin" documentation page (`docs/symfluence.md`) covering
  both seams, the `.process()` keying contract SYMFLUENCE consumes, a
  native-attribute → CAS-dataset replacement recipe with a verified starter
  mapping table, hard limits (raster consumers stay native), and the CSV
  export contract.

## [0.2.0] — 2026-06-11

### Added

- **Public Python API facade**: `import cas` now re-exports the blessed
  embedding surface with an explicit `__all__` — the async engine entry
  points (`extract`, `batch_extract`), the provider registry (`discover`,
  `list_providers`, `get_connector`), and the core request/response models
  (`AttributeRequest`, `BatchAttributeRequest`, `AttributeResponse`,
  `BatchAttributeResponse`, `AttributeResult`, `Geometry`, `TimeRange`,
  `AggregationMethod`, `QualityFlag`). Connector modules still load lazily on
  first `discover()`/`extract()`, so `import cas` stays fast.
- **Sync convenience wrappers** `cas.extract_sync()` and
  `cas.batch_extract_sync()` for scripts, notebooks, and synchronous hosts;
  they raise a clear error when called from a running event loop.
- **`cas.configure(**overrides)`** runtime configuration hook: clears the
  cached settings singleton and applies validated programmatic overrides
  (taking precedence over `CAS_*` env vars), so embedding frameworks can
  configure CAS after import; the engine's result cache is rebuilt against
  the refreshed settings.
- New "Python API (embedded)" documentation page covering the in-process
  interface, with the HTTP client (`cas.client`) repositioned as the
  deployed-service alternative returning the same models.

### Changed

- The extraction engine's result cache is now built lazily on first use
  (instead of at import time) and tracks the current settings.

## [0.1.0] — 2026-06-11

Initial public release.

### Added

- **228 provider connectors** spanning DEM/elevation, soil, land cover,
  hydrology/water, vegetation/canopy, climate/water-balance, geology, and
  biodiversity — global flagship datasets (Copernicus DEM, ISRIC SoilGrids,
  ESA WorldCover, MERIT Hydro, TerraClimate, …) plus deep national/regional
  coverage across 38 countries.
- **Async extraction engine**: fan-out to providers, server-side subsetting,
  continuous (mean/median/min/max/std) and categorical (majority/distribution)
  zonal statistics, per-provider and per-request deadlines, and result caching.
- **Protocol mixins** (WCS, STAC+COG, OPeNDAP, Zarr) composable into
  self-contained plugin connectors registered via a `@register` decorator.
- **Quality control**: range checks, coverage thresholds, and cross-provider
  consistency warnings on every result.
- **Three interfaces**: a `cas` CLI, a FastAPI HTTP API (typed Pydantic
  responses, OpenAPI docs, Prometheus metrics, optional API-key auth and rate
  limiting), and a typed Python SDK (`cas.client`) with sync and async clients.
- **Health monitoring**: daily CI end-to-end sweep of all providers over
  coverage-derived test polygons, baseline comparison (`cas health-compare`),
  and fast reachability checks (`cas verify`).
- **Machine-readable catalog** at `inventory/providers.yaml`, regenerated with
  `cas export-inventory`.
- MkDocs documentation site, Dockerfile, and CI (lint, type-check, tests on
  Python 3.11–3.13).

[0.2.0]: https://github.com/DarriEy/CAS/releases/tag/v0.2.0
[0.1.0]: https://github.com/DarriEy/CAS/releases/tag/v0.1.0
