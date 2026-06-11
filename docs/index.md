# CAS — Community Attribute Service

**Harmonized, QC'd, passthrough access to global geospatial attribute datasets**
— DEM/elevation, soil, land cover, hydrology, vegetation, geology, and biodiversity.

CAS is **not a data warehouse**. It is a thin quality-control and harmonization
layer that pulls from upstream providers on demand, validates responses, and
returns consistent results. Give it a geometry and one or more dataset IDs; it
fans out to the relevant providers, subsets server-side, computes zonal
statistics, runs QC, and hands back a uniform result.

## Statement of need

Large-sample hydrology depends on harmonized catchment attributes — terrain,
soil, land cover, climate, and geology summarized over thousands of basins — as
popularized by CAMELS-style datasets. Assembling such attributes today still
means writing bespoke, per-dataset extraction scripts: every provider exposes a
different protocol (WCS, STAC+COG, OPeNDAP, Zarr), grid, projection, and
no-data convention, and the resulting one-off pipelines are rarely reusable or
comparable across studies. CAS replaces that with a single interface for
harmonized, quality-controlled zonal attribute extraction across 200+
providers: given a geometry and dataset identifiers, it fans out to the
upstream services, subsets server-side, computes zonal statistics, applies QC
(range, coverage, cross-provider consistency), and returns uniform results with
provenance and citations. It is aimed at hydrologists, land-surface modelers,
and large-sample studies that need reproducible attribute datasets without
maintaining their own extraction code.

## Why CAS?

- **228 active providers** across 38 countries — global flagships plus deep
  national coverage, all behind one consistent interface.
- **Passthrough, not storage** — no stale local copies; every request reflects
  the live upstream.
- **Harmonized output** — the same `AttributeResult` shape whether the source is
  a WCS server, a STAC+COG, or an OPeNDAP endpoint.
- **Built-in QC** — range checks, coverage thresholds, and cross-provider
  consistency warnings.
- **Three ways in** — a [CLI](cli.md), an [HTTP API](api.md), and a typed
  [Python SDK](sdk.md).

## Pick your interface

=== "Python SDK"

    ```python
    from cas.client import CASClient

    with CASClient("http://localhost:8000") as cas:
        resp = cas.extract(
            geometry={"type": "Point", "coordinates": [-96.5, 39.0]},
            dataset_ids=["copernicus_dem:elevation"],
        )
        print(resp.results[0].value, resp.results[0].units)
    ```

=== "HTTP API"

    ```bash
    curl -s localhost:8000/api/v1/extract -H 'content-type: application/json' -d '{
      "geometry": {"type": "Point", "coordinates": [-96.5, 39.0]},
      "dataset_ids": ["copernicus_dem:elevation"]
    }'
    ```

=== "CLI"

    ```bash
    cas extract \
      -g '{"type":"Point","coordinates":[-96.5,39.0]}' \
      -d copernicus_dem:elevation
    ```

## Where to next

- New here? Start with the [Quick Start](quickstart.md).
- Building an app? Read the [Python SDK](sdk.md) guide and the
  [SDK Reference](reference.md).
- Integrating over HTTP? See the [HTTP API](api.md) and the live
  `/docs` (Swagger UI) on a running instance.
- Want the full dataset list? See the [Provider Catalog](catalog.md).
