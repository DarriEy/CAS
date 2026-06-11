# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
