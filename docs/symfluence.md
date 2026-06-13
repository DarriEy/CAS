# SYMFLUENCE Plugin

CAS ships a [SYMFLUENCE](https://github.com/DarriEy/SYMFLUENCE) integration
(`cas.integrations.symfluence`) that lets SYMFLUENCE source **per-HRU zonal
attributes** from any CAS dataset — 228+ providers behind one config key.

It plugs into SYMFLUENCE at three coexisting seams:

| Seam | Entry point | What it produces |
|---|---|---|
| **Primary: attribute processor** | `symfluence.attribute_processors` → `CASAttributeProcessor` | Attributes merged into SYMFLUENCE's per-HRU attribute table, alongside the native `elevation.*` / `soil.*` / `climate.*` attributes |
| Secondary: acquisition handler | `symfluence.plugins` → `register()` (`CASAttributeAcquirer` under key `CAS`) | Standalone analysis CSV in `data/attributes/cas/` |
| **Tertiary: attribute backend** | `symfluence.plugins` → `register()` (`CommunityAttributeBackend` under `R.attribute_backends['community']`) | Per-HRU `HRU_STATS_V1` CSV + acquisition manifest in `data/attributes/cas/`, ingested by the model-ready `AttributesNetCDFBuilder` as a `cas` group |

The **attribute backend** is the contract-0.3.0 protocol tier — the same
backend pattern as the CFS forcing backend and the CSFS observation backend.
Under `DATA_ACCESS: community`, SYMFLUENCE's attribute pipeline selects it
*first* (parity-gated; attribute parity is **tolerance-based**, not
bit-identical, since zonal stats depend on resampling/masking/grid alignment),
and it delivers a declared-schema per-HRU table plus a sidecar manifest that
flows straight into the model-ready attributes store. All three seams wrap the
**same** extraction helpers (`batch_extract` over HRU geometries → per-HRU
stats); when the backend serves `CAS`, SYMFLUENCE excludes the `cas` *processor*
plugin from its plugin loop so CAS is extracted exactly once. Frameworks
predating contract 0.3.0 simply lack the `attribute_backends` registry and fall
back to the processor-plugin seam.

## Install & auto-discovery

Install CAS into the same environment as SYMFLUENCE:

```bash
pip install community-attribute-service
```

That is all the wiring there is. CAS declares both entry points in its
`pyproject.toml`:

```toml
[project.entry-points."symfluence.attribute_processors"]
cas = "cas.integrations.symfluence:CASAttributeProcessor"

[project.entry-points."symfluence.plugins"]
cas = "cas.integrations.symfluence:register"
```

SYMFLUENCE's `discover_attribute_plugins()` loads the first group and
validates that each entry subclasses its `BaseAttributeProcessor`; the second
group is loaded at `import symfluence` and registers the acquisition handler.
Verify discovery:

```bash
python -c "
from symfluence.data.preprocessing.attribute_processors.plugins import discover_attribute_plugins
print([name for name, _ in discover_attribute_plugins()])"
```

The module imports defensively: without SYMFLUENCE installed, `import cas`
(and even `import cas.integrations.symfluence`) still works — the classes'
base classes degrade to `object` and `register()` is a no-op. CAS gains
**no** SYMFLUENCE dependency.

## Primary: the attribute-processor seam

### No-op unless configured

Both seams do nothing until you opt in: when `CAS_DATASETS` is unset or
empty, `CASAttributeProcessor.process()` logs an info message and returns
`{}`. On the SYMFLUENCE side you can also switch all external attribute
plugins off with `ATTRIBUTE_PLUGINS_ENABLED: false` or skip just CAS with
`ATTRIBUTE_PLUGINS_EXCLUDE: [cas]`.

### Configuration

Add flat keys to your SYMFLUENCE YAML config:

```yaml
# Comma-separated CAS dataset ids ({provider}:{dataset}) — required opt-in
CAS_DATASETS: "copernicus_dem:elevation,isric_soilgrids:clay_0-5cm"

# Optional: zonal aggregation method (default: mean)
# mean | median | min | max | std | sum | majority | minority | distribution
CAS_AGGREGATION: mean

# Optional: CAS runtime settings, passed to cas.configure()
CAS_API_CONFIG:
  provider_timeout_s: 60
```

Browse dataset ids with `cas datasets <provider>` or the
[provider catalog](catalog.md).

### What it does

SYMFLUENCE's attribute machinery
(`attributeProcessor._process_plugin_attributes`) discovers the processor,
constructs it with `(config, logger)`, and calls `.process()`. The processor:

1. locates the domain's HRU/catchment polygons through the inherited
   `BaseAttributeProcessor` path resolution (`CATCHMENT_PATH`,
   `CATCHMENT_SHP_NAME`, `CATCHMENT_SHP_HRUID`, defaulting to
   `shapefiles/catchment/{DOMAIN_NAME}_HRUs_{discretization}.shp`) — the same
   geometry every in-tree processor reduces over,
2. reprojects to EPSG:4326 if needed and builds one CAS geometry per HRU,
3. batches the geometries into `BatchAttributeRequest`s (max 1000 geometries
   each, all datasets per request) and calls `cas.batch_extract_sync()`,
4. logs a quality-flag summary, and
5. returns a flat attribute dict.

### How results flow onward (the consumed contract)

What we verified in SYMFLUENCE's `_process_plugin_attributes` (and the
surrounding `process_attributes()`):

- A plugin that returns a non-empty dict has it merged **as-is** into the
  same results dict the in-tree elevation/soil/climate processors feed
  (`results.update(plugin_results)`). A plugin that raises is logged and
  skipped — it never aborts attribute processing.
- For **lumped** domains (`DOMAIN_DEFINITION_METHOD: lumped`) all keys
  become columns of a single attribute row. CAS emits plain
  `cas.{dataset}` keys (e.g. `cas.copernicus_dem_elevation`).
- For **distributed** domains SYMFLUENCE rebuilds one row per HRU from keys
  shaped `HRU_{id}_{attribute}`, parsing the id with
  `int(key.split("_")[1])`. CAS emits `HRU_{id}_cas.{dataset}` keys with
  integer-coerced HRU ids (non-integer ids fall back to the geometry's
  positional index, with a warning).
- Categorical `distribution` results expand to one `cas.{dataset}_{class}`
  fraction key per class. Per-dataset `cas.{dataset}_quality` (string) and
  `cas.{dataset}_coverage_fraction` (float) keys ride along; SYMFLUENCE's
  numeric-only CSV writers drop the string keys automatically, exactly as
  they do for climaclass's string class codes.

Because the keying is byte-for-byte the shape native processors emit, CAS
attributes ride whatever path native attribute results ride: the per-HRU
DataFrame `process_attributes()` rebuilds from the merged dict, and the
per-HRU numeric CSV reshaping (`BaseAttributeProcessor._write_results_csv`,
the format SYMFLUENCE's `AttributesNetCDFBuilder` and transfer-function
regionalization consume) both parse exactly these `HRU_{id}_{attribute}`
keys. No special-cased `attributes/cas/` directory is involved — which was
the gap in the CSV-export mode below.

### The replacement recipe

To source the zonal-statistics attribute layer from CAS instead of native
downloads:

```yaml
ATTRIBUTE_PROFILE: core          # skip the extended native processors
DOWNLOAD_WORLDCLIM: false        # and any other DOWNLOAD_* you replace
CAS_DATASETS: "copernicus_dem:elevation,\
isric_soilgrids:clay_0-5cm,isric_soilgrids:sand_0-5cm,\
esa_worldcover:land_cover,terraclimate:pet,terraclimate:aridity"
CAS_AGGREGATION: mean
```

#### Hard limits

Anything in SYMFLUENCE that consumes **rasters** cannot come from CAS: the
DEM used for delineation/discretization/elevation bands, and the
land-cover/soil grids used for HRU generation, stay native acquisitions. CAS
replaces the *zonal-statistics attribute layer* only (per-HRU scalar/fraction
attributes).

#### Starter mapping: native attributes → CAS dataset ids

All ids below are verified against CAS's provider registry (`cas datasets
<provider>`).

| Native attribute family | CAS dataset id(s) | Notes |
|---|---|---|
| `elevation.*` zonal stats | `copernicus_dem:elevation`, `cop_dem_90:elevation` | use `CAS_AGGREGATION: mean`/`min`/`max`/`std`; `merit_hydro:elevation_adjusted` for hydrologically conditioned elevation |
| `soil.*` texture / properties | `isric_soilgrids:clay_0-5cm`, `isric_soilgrids:sand_0-5cm`, `isric_soilgrids:silt_0-5cm` (layers to `100-200cm`), `isric_soilgrids:soc_0-5cm`, `isric_soilgrids:phh2o_0-5cm` | one id per depth layer |
| `soil.*` derived stocks/classes | `soilgrids_derived:ocs`, `soilgrids_derived:wrb_class` | `wrb_class` with `CAS_AGGREGATION: majority` or `distribution` |
| `landcover.*` class fractions | `esa_worldcover:land_cover`, `esa_cci_lc:land_cover` | use `CAS_AGGREGATION: distribution` for per-class fractions |
| `vegetation.*` structure | `canopy_height:canopy_height` | canopy height, not LAI |
| `climate.pet_annual_mean`, `climate.aridity_index`, `climate.prec_annual_mean` | `terraclimate:pet`, `terraclimate:aridity`, `terraclimate:precipitation` (also `aet`, `deficit`, `soil_moisture`, `runoff`, `tmax`, `tmin`, `vpd`, `pdsi`) | TerraClimate climatologies |
| permafrost extent | `permafrost:permafrost_index` | |

## Secondary: the acquisition-handler CSV export

`register()` adds `CASAttributeAcquirer` to SYMFLUENCE's acquisition registry
under the key **`CAS`** for *explicit* use — reference it from a custom
attribute profile or invoke it directly. It is **not** part of the built-in
profiles (`core`/`camels_spat`/`full`); the processor seam above superseded
that.

Given an output directory (by convention
`{project_dir}/data/attributes/cas/`), `download()` runs the same extraction
pipeline and writes `{DOMAIN_NAME}_cas_attributes.csv`:

- **one row per HRU**, sorted by HRU id, with an explicit `hru_id` first
  column;
- **one numeric column per dataset**, named from the sanitized dataset id
  (`isric_soilgrids:clay_0-5cm` → `isric_soilgrids_clay_0_5cm`); categorical
  `distribution` results expand to one `{dataset}_{class}` fraction column
  per class;
- per-dataset metadata extras: `{column}_units`, `{column}_quality`,
  `{column}_coverage_fraction`.

Re-runs skip the extraction when the CSV already exists (unless
`FORCE_DOWNLOAD: true`). This CSV is an analysis-oriented sidecar: it joins
on `hru_id` but is not folded into SYMFLUENCE's model-ready attribute store —
use the processor seam for that.
