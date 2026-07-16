# CLI

The `cas` command is installed with the package (`pip install community-attribute-service`).

```bash
cas --help
cas --version
```

## Commands

| Command | Purpose |
| --- | --- |
| `cas extract` | Extract attribute values for a geometry from one or more datasets |
| `cas providers` | List registered provider slugs (`--support` adds evidence tiers) |
| `cas datasets` | List available datasets (optionally `-p <provider>`) |
| `cas export-inventory` | Regenerate `inventory/providers.yaml` from the live registry |
| `cas export-support` | Regenerate `inventory/support.json` from the health baseline |
| `cas verify` | Fast endpoint-reachability check for all providers (~20s) |
| `cas health` | End-to-end extraction health check (real `extract()` per provider) |
| `cas health-compare` | Compare a current health snapshot against a baseline |

## Extract

```bash
# Inline GeoJSON geometry
cas extract \
  -g '{"type":"Point","coordinates":[-96.5,39.0]}' \
  -d copernicus_dem:elevation

# Geometry / Feature / FeatureCollection from a file
cas extract -g @my_catchment.geojson -d isric_soilgrids:clay_0-5cm

# Multiple datasets + custom aggregation + output file
cas extract \
  -g @aoi.geojson \
  -d esa_worldcover:land_cover \
  -a majority \
  -o result.json

# Time-varying dataset
cas extract -g @aoi.geojson -d modis_lc:land_cover --start 2020-01-01 --end 2020-12-31
```

A `FeatureCollection` is dispatched as a batch automatically.

## Health & monitoring

```bash
cas verify                  # reachability only (fast)
cas verify -s usgs_3dep     # one provider

cas health                  # end-to-end sweep + summary
cas health -s usgs_3dep     # one provider
cas health --strict         # exit non-zero if any provider is down
cas health --json snap.json # write a machine-readable snapshot

# Regression gate (used in CI)
cas health-compare health/baseline.json snap.json
```

See [Architecture](architecture.md) for how the health sweep derives a
coverage-aware test polygon per provider.

## Provider support evidence

```bash
cas providers --support
cas export-support
```

These commands distinguish `verified`, `credentialed`, `mirror-backed`,
`degraded`, and `metadata-only` providers. See
[Provider support levels](support.md) for the exact evidence contract.
