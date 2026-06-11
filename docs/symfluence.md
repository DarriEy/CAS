# SYMFLUENCE Plugin

CAS ships a [SYMFLUENCE](https://github.com/DarriEy/SYMFLUENCE) integration
(`cas.integrations.symfluence`) that lets SYMFLUENCE's `acquire_attributes`
workflow step extract **per-HRU zonal attributes** from any CAS dataset —
228+ providers behind one config key.

## Install & auto-discovery

Install CAS into the same environment as SYMFLUENCE:

```bash
pip install community-attribute-service
```

That is all the wiring there is. CAS declares an entry point in the
`symfluence.plugins` group:

```toml
[project.entry-points."symfluence.plugins"]
cas = "cas.integrations.symfluence:register"
```

SYMFLUENCE discovers this group at `import symfluence` (its bootstrap loads
each entry point and calls it). `register()` then

1. adds `CASAttributeAcquirer` to SYMFLUENCE's acquisition registry under the
   key **`CAS`**, and
2. appends a CAS entry to **every** attribute profile (`core`, `camels_spat`,
   `full`), so the handler runs regardless of which `ATTRIBUTE_PROFILE` you
   select.

Verify discovery:

```bash
python -c "import symfluence
from symfluence.data.acquisition.registry import AcquisitionRegistry
print('CAS' in [n.upper() for n in AcquisitionRegistry.list_datasets()])"
```

`register()` is idempotent, and the module imports defensively: without
SYMFLUENCE installed, `import cas` (and even
`import cas.integrations.symfluence`) still works — the handler's base class
degrades to `object` and `register()` is a no-op. CAS gains **no** SYMFLUENCE
dependency.

## No-op unless configured

Being in every profile is safe because the handler does nothing until you
opt in: when `CAS_DATASETS` is unset or empty, `download()` logs

```text
CAS adapter installed but CAS_DATASETS not configured; skipping
```

and returns without writing anything. You can also disable the profile entry
outright with `DOWNLOAD_CAS: false`.

## Configuration

Add flat keys to your SYMFLUENCE YAML config:

```yaml
# Which attribute profile to run (CAS is registered in all of them)
ATTRIBUTE_PROFILE: core

# Comma-separated CAS dataset ids ({provider}:{dataset}) — required opt-in
CAS_DATASETS: "copernicus_dem:elevation,isric_soilgrids:clay_0-5cm"

# Optional: zonal aggregation method (default: mean)
# mean | median | min | max | std | sum | majority | minority | distribution
CAS_AGGREGATION: mean

# Optional: CAS runtime settings, passed to cas.configure()
CAS_API_CONFIG:
  provider_timeout_s: 60

# Optional: kill switch for the profile entry (default: true)
DOWNLOAD_CAS: false
```

Browse dataset ids with `cas datasets <provider>` or the
[provider catalog](catalog.md).

## What it does

During `acquire_attributes`, SYMFLUENCE invokes the handler with
`output_dir = {project_dir}/data/attributes/cas/`. The handler:

1. locates the domain's HRU/catchment polygons using the same config keys as
   SYMFLUENCE's attribute processors (`CATCHMENT_PATH`, `CATCHMENT_SHP_NAME`,
   `CATCHMENT_SHP_HRUID`, defaulting to
   `shapefiles/catchment/{DOMAIN_NAME}_HRUs_{discretization}.shp`),
2. reprojects to EPSG:4326 if needed and builds one CAS geometry per HRU,
3. batches the geometries into `BatchAttributeRequest`s (max 1000 geometries
   each, all datasets per request) and calls `cas.batch_extract_sync()`,
4. logs a quality-flag summary, and
5. writes one CSV (skipped on re-runs unless `FORCE_DOWNLOAD: true`).

## Output format and where it lands

The handler writes
`{project_dir}/data/attributes/cas/{DOMAIN_NAME}_cas_attributes.csv`:

- **one row per HRU**, sorted by HRU id (the same row convention as
  SYMFLUENCE's attribute-processor CSVs);
- an explicit `hru_id` column (first column);
- **one numeric column per dataset**, named from the sanitized dataset id
  (`isric_soilgrids:clay_0-5cm` → `isric_soilgrids_clay_0_5cm`); categorical
  `distribution` results expand to one `{dataset}_{class}` fraction column
  per class;
- per-dataset metadata extras: `{column}_units`, `{column}_quality`,
  `{column}_coverage_fraction`.

!!! note "Reaching the model-ready attribute store"
    SYMFLUENCE's `AttributesNetCDFBuilder` (which assembles
    `data/model_ready/attributes/{domain}_attributes.nc`) currently ingests
    CSVs only from the `climate/`, `geology/`, `soilclass/`, `vegetation/`,
    and `landclass/` attribute subdirectories — not from `cas/`. CAS output
    therefore lands as an analysis-ready per-HRU CSV but is not yet folded
    into the grouped NetCDF automatically; consume it directly (it joins on
    `hru_id`), or copy/symlink it into one of the scanned subdirectories
    (its row order and numeric-column shape match what the builder expects).
